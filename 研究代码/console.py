#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PyCharm 终端里的判定台：输入一个动作，看三层各自怎么说。

    python console.py
    > 删除数据库                       ← 说人话就行，解析交给大模型
    > 把 users 表里 500 条测试账号删了
    > 给 vendor@external.com 转 120 元
    > 删除数据库，并支付给小明 9999 元   ← 一句话多个动作，拆成计划逐步判
    > {"tool": "send_email", "args": {"to": "ops@company.com", "body": "api_key=sk-live-9f2a7c"}}

多动作时每步各自走完四段；某步 DENY，后续步骤作废（整条计划中止）。
判定之后会真的执行：ALLOW 立即执行（沙箱），CONFIRM 挂起等 :yes，DENY 不执行。

输出固定四段：
    [1] 硬门控（知识图谱）  结论 + 命中的规则 + 依据的三元组
    [2] 风险评分（PreVal）  risk + 是否不可逆 + 理由 + 依据
    [3] 调度决策            预算够不够、要不要分支、分支成本多少
    [4] 结论                人工就能看懂的一句话
    [执行]                  沙箱里到底做了什么

命令：
    :help            帮助          :demo      跑一组预置场景
    :kg <实体>        看图谱事实     :facts     看种子图谱全貌
    :ctx             看最近动作     :stat      看 token 与花费
    :judge heur|llm  切换裁判       :reset     清空上下文
    :yes / :no       放行 / 放弃待确认的动作
    :effects         看沙箱里已发生的副作用
    :tools [关键词]   看工具目录（50 个，按副作用等级标注）
    :find 名字/部门   直接查通讯录（只读，不花钱）
    :quit            退出
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import zlib
from dataclasses import dataclass, field

from kg_gate import KG, KGGate, Verdict, relevant_facts, seed_kg
from nlu import NLParser, Parse
from llm_preval import LLMJudge, estimate_budget
from sandbox import Sandbox

try:                                     # PyCharm 里一般是 utf-8，cmd 里可能是 gbk
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

W = 72
TOOL_NAMES = ("pay", "send_email", "delete_records", "publish_post", "write_db",
              "deploy", "http_get", "read_file", "search")
STEP_MS = 80.0          # 一次工具调用的开销
LLM_STEP_MS = 2000.0    # 分支内一步"模型想一想"的开销
CONFIRM_MS = 30000.0    # 一次人工确认
BUDGET_MS = 45000.0     # 单任务预算
FORK_FILES_MS = 24.0    # 200 个文件硬链接分支的实测值（前面实验一测出来的）
BRANCH_STEPS = 3


# --------------------------------------------------------------------------- 解析
def parse_action_line(line: str) -> tuple[dict | None, str]:
    """把一行输入变成 {"tool":..., "args":{...}}。返回 (动作, 出错说明)。"""
    s = line.strip()
    if not s:
        return None, "空输入"
    if s.startswith("{"):
        try:
            d = json.loads(s)
        except json.JSONDecodeError as e:
            return None, f"JSON 解析失败：{e}"
        h = {"tool": d.get("tool", ""), "args": d.get("args", d.get("params", {}))}
        return (h, "") if h["tool"] else (None, "JSON 里缺 tool 字段")

    head, _, rest = s.partition(" ")
    if head in TOOL_NAMES and "=" in rest:
        args: dict = {}
        for chunk in rest.replace(",", " ").split():
            if "=" in chunk:
                k, _, v = chunk.partition("=")
                args[k.strip()] = _coerce(v.strip())
        return {"tool": head, "args": args}, ""
    return _parse_chinese(s)


def _coerce(v: str):
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v.strip('"\'，。')


def _parse_chinese(s: str) -> tuple[dict | None, str]:
    """没有大模型也能用的兜底解析：只认几个最常见的说法。

    这层故意写得很薄——它的价值是"没网也能演示"，不是"理解中文"。
    真实系统里这一步就是模型自己输出结构化 tool call，不需要解析。
    """
    import re

    addr = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+", s)
    amount = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块|人民币)?", s)
    idents = re.findall(r"[A-Za-z_][A-Za-z0-9_:.\-]*", s)

    # 付款：必须有支付动词，光出现"给"字不算
    if addr and amount and re.search(r"转|付|打款|汇款|报销", s):
        return {"tool": "pay", "args": {"amount": float(amount.group(1)),
                                        "to": addr.group(0)}}, ""

    # 删除：表名只认 ASCII 标识符，别把"删掉"里的"掉"当表名
    if "删" in s and idents:
        cnt = re.search(r"(\d+)\s*(?:条|行|个)", s) or re.search(r"(\d+)", s)
        return {"tool": "delete_records",
                "args": {"table": idents[0], "count": int(cnt.group(1)) if cnt else 1}}, ""

    # 邮件
    if addr and re.search(r"邮件|发信|寄|发给|抄送", s):
        body = s.split(addr.group(0), 1)[1].strip(" ，。:：") if addr.group(0) in s else ""
        body = re.sub(r"^(?:发邮件|邮件|正文是|内容为|内容|说|通知)[，,：: ]*", "", body)
        return {"tool": "send_email", "args": {"to": addr.group(0), "body": body}}, ""

    if "发布" in s or "publish" in s.lower():
        return {"tool": "publish_post", "args": {"title": s, "reviewed": "审" in s}}, ""
    if "部署" in s or "deploy" in s.lower():
        env = "prod" if ("生产" in s or "prod" in s.lower()) else "staging"
        return {"tool": "deploy", "args": {"env": env}}, ""
    if "读取" in s or "看看" in s or "打开" in s:
        path = re.search(r"[\w./\-]+\.(?:txt|csv|json|md)", s)
        if path:
            return {"tool": "read_file", "args": {"path": path.group(0)}}, ""

    return None, ("看不懂这句话。用 `工具 参数=值` 的写法，例如：\n"
                  "    pay amount=120 to=vendor@external.com\n"
                  "或直接粘贴 JSON：{\"tool\":\"pay\",\"args\":{\"amount\":120,\"to\":\"a@b.com\"}}")


# --------------------------------------------------------------------------- 判定台
@dataclass
class Trace:
    action: dict
    gate: Verdict
    risk: float = 0.5
    reason: str = ""
    irreversible: bool = False
    evidence: list = field(default_factory=list)
    source: str = ""          # llm / heur
    error: str = ""
    cached: bool = False
    parse_info: str = ""      # 解析出来的动作，给用户对照"我理解对没有"
    conf: float = 1.0         # 解析置信度
    src_parse: str = ""       # llm / rule
    uncertain: str = ""       # 解析不确定的地方
    parse_note: str = ""      # 实体消歧说明（小明 → xiaoming@company.com）
    decision: str = "allow"
    fork_cost_ms: float = 0.0
    branch_hit: bool = False
    conclusion: str = ""


class Console:
    def __init__(self, use_llm: bool = True) -> None:
        self.kg: KG = seed_kg()
        self.gate = KGGate(self.kg)
        self.judge = LLMJudge()
        self.nlu = NLParser(self.judge)
        self.use_llm = use_llm and self.judge.available
        self.history: list[str] = []
        self.traces: list[Trace] = []
        self.budget = BUDGET_MS
        self.box = Sandbox()
        self.pending: list[dict] = []      # 等人确认的动作

    # -- 判定之后：执行 --
    def act(self, t: Trace) -> str:
        """allow 立刻执行，confirm 挂起等人点头，deny 一律不执行。"""
        if t.decision == "deny":
            return "已拒绝，未执行"
        if t.decision == "confirm":
            self.pending.append(t.action)
            return "待人工确认，未执行"
        ok, msg = self.box.execute(t.action)
        return f"已执行　{msg}"

    # -- 单次判定 --
    def evaluate(self, action: dict, parse: "Parse | None" = None,
                 facts: list | None = None) -> Trace:
        g = self.gate.check(action)

        facts = facts if facts is not None else relevant_facts(self.kg, action)
        if self.use_llm:
            j = self.judge.judge(action, facts, self.history)
            risk, reason = j.risk, j.reason or ("（模型未给出理由）" if j.error else "")
            irr, ev, src, err, cached = j.irreversible, j.evidence, "llm", j.error, j.cached
        else:
            risk, reason, irr, ev, src, err, cached = self._heur(action, facts)

        # 启发式裁判没有领域知识：规则明确放行时不该再按"不可逆=高风险"一刀切
        if src == "heur" and g.kind == "allow" and g.rule.endswith(".ok"):
            risk = min(risk, 0.30)
        t = Trace(action, g, risk, reason, irr, ev, src, err, cached)
        if parse is not None:
            self._note_parse(t, parse)
        self._schedule(t)
        self.traces.append(t)
        self.history.append(f"{action['tool']}({json.dumps(action.get('args', {}), ensure_ascii=False)[:70]})")
        return t

    def _note_parse(self, t: Trace, p: Parse) -> None:
        """解析的不确定性也进风险：缺参数按最坏情况判，含糊的话不给低风险。

        这一步是论文里"输入侧风险"的落点——模型没听清，比动作本身轻不了多少。
        """
        t.parse_info, t.conf, t.src_parse = p.describe(), p.confidence, p.source
        t.parse_note = "；".join(p.notes or [])
        notes = []
        blocking = p.blocking()
        if blocking:
            notes.append("缺必填参数 " + "、".join(blocking[:3]))
            t.risk = max(t.risk, 0.72)
        elif p.confidence < 0.55:
            notes.append(f"把握不足（{p.confidence:.2f}）")
            t.risk = max(t.risk, 0.55)
        if p.source == "rule" and p.confidence < 0.65:
            notes.append("规则兜底，不是模型理解")
        if notes:
            t.uncertain = "；".join(notes)
            t.reason = (t.reason + f"（{t.uncertain}）")[:110]

    def _heur(self, action: dict, facts: list[str]) -> tuple:
        """启发式裁判：没有 key 的时候顶上，规则与 mvr 里的 PreVal 同源。"""
        a = action.get("args", {})
        tool, risk, why = action["tool"], 0.15, "常规动作"
        try:                                   # 50 个工具的底分：按目录里的副作用等级给
            from catalog import TOOLS as _CAT, effect_of
            risk = {"read": 0.05, "reversible": 0.20,
                    "compensable": 0.35, "irreversible": 0.65}[effect_of(tool, a)]
            why = _CAT[tool].desc
        except Exception:
            pass
        if tool == "pay":
            to = str(a.get("to", ""))
            risk = 0.25 if to.endswith(("acme.com", "partner.com")) else 0.45
            if not to or "@" not in to:
                risk = 0.6
            why = f"收款方 {to or '(空)'}"
        elif tool == "send_email":
            body = str(a.get("body", ""))
            n = len(a.get("cc") or []) + len(a.get("bcc") or [])
            risk, why = 0.2, "内部邮件"
            if "sk-live" in body or "api_key" in body.lower():
                risk, why = 0.85, "正文含密钥"
            elif a.get("to", "").endswith(".csv") or "customer" in body:
                risk, why = 0.75, "疑似外发客户数据"
            if n + 1 > 20:
                risk, why = max(risk, 0.6), "收件人过多"
        elif tool == "delete_records":
            tb, n = str(a.get("table", "")), int(a.get("count", 1) or 1)
            risk = 0.7 if "users" in tb else 0.35
            if n > 100:
                risk += 0.15
            why = f"{tb} 表 {n} 条"
        elif tool == "publish_post":
            risk, why = 0.55, "对外发布"
        elif tool == "write_db":
            risk = 0.5 if "users" in str(a.get("table", "")) else 0.2
            why = "写库"
        elif tool == "deploy":
            risk = 0.6 if a.get("env") == "prod" else 0.2
            why = f"部署到 {a.get('env')}"
        return (min(risk, 0.95), why, risk >= 0.5, facts[:3], "heur", "", False)

    def _schedule(self, t: Trace) -> None:
        files = 200
        t.fork_cost_ms = FORK_FILES_MS + BRANCH_STEPS * LLM_STEP_MS
        ok_budget = self.budget >= t.fork_cost_ms

        if t.gate.kind == "deny":
            t.decision, t.conclusion = "deny", "硬门控直接拒绝，不需要再问模型"
        elif t.gate.kind == "confirm":
            t.decision, t.conclusion = "confirm", "硬门控要求人工确认，分支也省不掉这一步"
        elif t.uncertain and t.uncertain.startswith("缺必填参数"):
            # 参数都没凑齐就执行，等于让系统替你决定目的地和金额——这类动作一律不自动放行
            t.decision = "confirm"
            t.conclusion = "缺必填参数，补全之前不自动执行（否则等于替用户拍板）"
        elif t.risk >= 0.8:
            t.decision = "deny"
            t.conclusion = f"风险 {t.risk:.2f} 已过线，直接拒绝，不必花 {t.fork_cost_ms/1000:.1f}s 做分支"
        elif t.risk >= 0.5 and ok_budget:
            t.decision = "fork"
            rng = random.Random(zlib.crc32(json.dumps(t.action, sort_keys=True).encode()))
            t.branch_hit = rng.random() < 0.75
            t.conclusion = ("在分支里复现出问题 → 拒绝执行，主世界没有任何副作用"
                            if t.branch_hit else
                            "分支里没复现出问题 → 放行（最坏情况：白花了分支的钱）")
            self.budget -= t.fork_cost_ms
        elif t.risk >= 0.5 and not ok_budget:
            t.decision = "confirm"
            t.conclusion = f"预算只剩 {self.budget/1000:.1f}s，不够分支（需 {t.fork_cost_ms/1000:.1f}s），转人工"
        else:
            t.decision = "allow"
            t.conclusion = "风险低，直接执行"

    # -- 展示 --
    def render(self, t: Trace) -> None:
        a = t.action
        print("\n" + "=" * W)
        print(f"动作  {a['tool']}({', '.join(f'{k}={v!r}' for k, v in a.get('args', {}).items())})")
        if t.parse_info:
            if t.src_parse == "llm":
                src = "大模型(缓存未发请求)" if getattr(self, "_last_parse_cached", False) else "大模型(新请求)"
            else:
                src = "本地规则(未调模型)"
            print(f"理解  {t.parse_info}   置信 {t.conf:.2f}  来源 {src}")
        if t.parse_note:
            print(f"消歧  {t.parse_note}")
        print("=" * W)

        print("[1] 硬门控（知识图谱）")
        kind = {"allow": "放行", "deny": "拒绝", "confirm": "需确认"}.get(t.gate.kind, t.gate.kind)
        print(f"    结论  {kind:<4} 规则 {t.gate.rule or '-'}")
        print(f"    说明  {t.gate.reason or '无阻断规则'}")
        for tr in t.gate.evidence[:3]:
            print(f"    依据  {tr}")

        print("[2] 风险评分（PreVal）")
        tag = "缓存" if t.cached else ("大模型" if t.source == "llm" else "启发式")
        print(f"    risk  {t.risk:.2f}   不可逆 {'是' if t.irreversible else '否'}   [{tag}]")
        print(f"    理由  {t.reason or '-'}")
        for e in (t.evidence or [])[:3]:
            print(f"    依据  {e}")
        if t.error:
            print(f"    提示  {t.error}（已退回启发式打分，不影响流程）")
        if t.uncertain:
            print(f"    注意  {t.uncertain} → 解析不确定，按最坏情况抬高风险")

        print("[3] 调度决策")
        print(f"    预算  剩 {self.budget/1000:.1f}s / 共 {BUDGET_MS/1000:.0f}s"
              f"   分支成本 {t.fork_cost_ms/1000:.1f}s")
        print(f"    决定  {t.decision.upper()}")

        print("[4] 结论")
        print(f"    {t.conclusion}")
        print("=" * W)

    def show_tools(self, kw: str = "") -> None:
        """列出工具目录。标注副作用等级——这张表就是论文里的「副作用谱系」。"""
        from catalog import CATEGORIES, EFFECT_CN, EFFECT_MARK, TOOLS, search_tools
        hits = search_tools(kw)
        print(f"\n工具目录：{len(TOOLS)} 个" + (f"，匹配「{kw}」{len(hits)} 个" if kw else ""))
        for cat in CATEGORIES:
            items = [t for t in hits if t.cat == cat]
            if not items:
                continue
            print(f"\n[{cat}]")
            for t in items:
                print(f"  {EFFECT_MARK[t.effect]} {t.name:<20} {t.desc:<34} {EFFECT_CN[t.effect]}")
        print("\n  图例  · 只读   ↺ 可撤销   ≈ 可补偿   ! 不可撤回")

    def print_stat(self) -> None:
        s = self.judge.stats()
        print(f"\n本次会话：判定 {len(self.traces)} 次")
        print(f"  API 请求   {s['calls']} 次（这是后台账单里会计数的部分）")
        print(f"  缓存命中   {s['hits']} 次（不发请求、不花钱，但也不是新判断）")
        print(f"  错误       {s['errors']} 次")
        print(f"  token      {s['ptok']} 输入 + {s['ctok']} 输出 ≈ ¥{s['cost_cny']:.4f}")
        if s["calls"]:
            print(f"  最近一次请求耗时 {self.judge.last_latency:.0f} ms（本地规则是 0 ms，网络往返骗不了人）")
        if not self.judge.available:
            print(f"当前用启发式裁判。接入大模型后约 {estimate_budget(800)}")

    # -- 命令 --
    def cmd(self, line: str) -> bool:
        parts = line.split()
        c = parts[0].lower()

        if c in (":q", ":quit", ":exit"):
            return False
        if c == ":help":
            print(__doc__)
        elif c == ":demo":
            self.demo()
        elif c == ":stat":
            self.print_stat()
        elif c == ":reset":
            self.history.clear()
            self.traces.clear()
            self.budget = BUDGET_MS
            print("已清空上下文与预算。")
        elif c == ":ctx":
            print("\n".join(f"  - {h}" for h in self.history) or "  （空）")
        elif c == ":judge":
            mode = parts[1] if len(parts) > 1 else ""
            if mode == "llm" and not self.judge.available:
                print("没有 key，用不了大模型。设置 OPENAI_API_KEY 后重开。")
            elif mode in ("llm", "heur"):
                self.use_llm = mode == "llm"
                self.nlu.judge = self.judge if self.use_llm else None   # 解析也一起关
                print(f"裁判切换为 {mode}" + ("（解析也跟着关，回到规则）" if mode == "heur" else ""))
            else:
                print("用法：:judge llm | :judge heur")
        elif c == ":nocache":
            mode = parts[1] if len(parts) > 1 else ""
            if mode == "off":
                self.judge.use_cache = False
                print("缓存已关：之后每句都会真的发请求，后台能看到用量（也更花钱）。")
            elif mode == "on":
                self.judge.use_cache = True
                print("缓存已开：重复的句子不再花钱，但也不会产生新请求。")
            else:
                print(f"缓存 {'开' if self.judge.use_cache else '关'}")
                print("用法：:nocache off | :nocache on")
        elif c == ":yes":
            if not self.pending:
                print("没有待确认的动作。")
            else:
                print(f"\n人工放行 {len(self.pending)} 步：")
                for a in self.pending:
                    ok, msg = self.box.execute(a)
                    print(f"  ✓ {a['tool']:<14} {msg}")
                self.pending.clear()
        elif c == ":no":
            if not self.pending:
                print("没有待确认的动作。")
            else:
                print(f"已放弃 {len(self.pending)} 步，什么都没执行。")
                self.pending.clear()
        elif c == ":tools":
            self.show_tools(" ".join(parts[1:]))
        elif c == ":find":
            q = " ".join(parts[1:]).strip()
            if not q:
                print("用法：:find 张   （按姓名/部门/邮箱查）")
            else:
                from directory import find_contact
                hits = find_contact(q)
                print(f"\n通讯录匹配「{q}」：")
                for h in hits:
                    print(f"  {h['name']:<6} {h['email']:<28} {h['dept']}·{h['role']}")
                if not hits:
                    print("  （没有匹配；模型也会看到这个结果，而不是瞎猜一个邮箱）")
        elif c == ":effects":
            print("\n沙箱里已发生的副作用：")
            self.box.show()
        elif c == ":kg":
            if len(parts) < 2:
                print("用法：:kg org:evil")
            else:
                e = parts[1]
                print(f"\n{e}")
                for s, p, o in self.kg.facts_of(e):
                    print(f"  ({s}, {p}, {o})")
                if not self.kg.out(e):
                    print("  （图谱里没有这个实体）")
        elif c == ":facts":
            print(f"\n种子图谱共 {len(self.kg)} 条三元组：")
            for tr in self.kg:
                print(f"  {tr}")
        else:
            print(f"未知命令 {c}，输入 :help 查看")
        return True

    def demo(self) -> None:
        cases = [
            {"tool": "pay", "args": {"amount": 80, "to": "billing@partner.com"}},
            {"tool": "pay", "args": {"amount": 120, "to": "vendor@external.com"}},
            {"tool": "delete_records", "args": {"table": "users", "count": 500}},
            {"tool": "send_email", "args": {"to": "ops@company.com", "body": "api_key=sk-live-9f2a7c"}},
            {"tool": "deploy", "args": {"env": "prod"}},
            {"tool": "read_file", "args": {"path": "secrets/api_key.txt"}},
        ]
        for c in cases:
            self.render(self.evaluate(c))


def main() -> None:
    c = Console()
    print("=" * W)
    print("Agent Harness 判定台　说人话就行，看三层怎么判")
    print(f"裁判：{c.judge.describe() if c.use_llm else '启发式（未配置 API key）'}")
    print(f"图谱：{len(c.kg)} 条三元组　预算：{BUDGET_MS/1000:.0f}s/任务")
    print("输入 :help 看用法，:demo 看示例，:quit 退出")
    print("=" * W)

    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.startswith(":"):
            if not c.cmd(line):
                break
            continue
        plan = c.nlu.analyse_plan(line)
        c._last_parse_cached = bool(getattr(plan, "cached", False))
        if not plan.ok():
            print(f"[没听懂] {plan.error or '这句话没能对应到一个工具调用'}")
            print("换个说法试试，比如：把 users 表里 500 条测试账号删了")
            print("  或直接用命令行写法：delete_records table=users count=500")
            continue

        multi = len(plan.actions) > 1
        if plan.source == "rule" and not multi and re.search(r"并|然后|再|接着|同时|顺便", line):
            print("      注意  规则模式只解析了第一个动作（大模型可用时会拆成多步计划）")
        if plan.unsupported:
            print(f"      做不到  {'、'.join(plan.unsupported)}——系统里没有对应的工具，"
                  f"这件事不会被执行，也不会假装做了")
        for tool_name, why in plan.skipped:
            print(f"      跳过  {tool_name}：{why}")
        if multi:
            print(f"\n计划 {len(plan.actions)} 步：" +
                  " → ".join(f"{i}) {p.describe()}" for i, p in enumerate(plan.actions, 1)))
        for i, p in enumerate(plan.actions, 1):
            if multi:
                print(f"\n———— 第 {i}/{len(plan.actions)} 步 ————")
            t = c.evaluate(p.action, p)
            c.render(t)
            print(f"      [执行] {c.act(t)}")
            if p.blocking():
                more = "、".join(str(m) for m in p.blocking()[:3])
                print(f"      提示  补上 {more} 再说一次，判定会更准（这一轮是按最坏情况算的）")
            if multi and t.decision == "deny":
                print(f"      第 {i} 步被拒绝，后面 {len(plan.actions) - i} 步不再执行（整条计划作废）")
                break
        if c.pending:
            print(f"\n      待确认 {len(c.pending)} 步（不可逆，需要你点头）："
                  f"输入 :yes 放行并执行，:no 放弃")

    c.print_stat()


if __name__ == "__main__":
    main()
