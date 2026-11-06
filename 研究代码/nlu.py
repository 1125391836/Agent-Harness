"""自然语言 → 工具调用计划（给 console.py 用）。

为什么要单独一层：以前 console 用正则猜中文，猜不中就说"看不懂"。
接了大模型之后，这件事应该由模型做——它知道"删除数据库"对应 delete_records，
也知道"把上周的客户表清理一下"可能缺参数。

还有一件以前漏掉的事：**一句话经常不止一个动作**。
"删除数据库，并支付给小明 9999 元"是两个动作，只解析第一个等于悄悄丢掉了用户的指令。
所以这里返回的是 Plan（有序动作列表），console 逐步判定、逐步执行。

但这里有个不能糊弄过去的点：**"听懂了"和"猜的"不能给同一个风险分**。
Parse 里带 confidence 和 missing，console 拿到之后会把风险抬高——
解析不确定，本身就是一种风险源，应该进入第三层的调度决策。

用法：
    from nlu import NLParser
    plan = NLParser(judge).analyse_plan("删除数据库，并支付给小明 9999 元")
    plan.actions[0].action   # {'tool': 'delete_records', 'args': {}}
    plan.actions[1].action   # {'tool': 'pay', 'args': {'amount': 9999.0, 'to': '小明'}}

    p = NLParser(judge).analyse("把 users 表里 500 条测试账号删了")   # 兼容旧接口，取第一步
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# 工具清单不在本文件里：catalog.py 是唯一事实源。
# 加一个工具只改 catalog，提示词、参数校验、门控、沙箱行为全都跟着变。
from catalog import TOOLS as _CAT

TOOL_SPEC: dict[str, tuple[str, dict[str, str]]] = {
    n: (t.desc, dict(t.params)) for n, t in _CAT.items()}
REQUIRED: dict[str, list[str]] = {
    n: list(t.required) for n, t in _CAT.items() if t.required}

SYSTEM_PROMPT = """你是 Agent Harness 的指令解析器。把用户的一句话翻译成“有序的工具调用计划”。

可用工具（名字 → 说明 → 参数）：
{spec}

只输出一个 JSON 对象，不要解释、不要代码块：
{{"intent": "一句话复述用户要做什么",
  "actions": [{{"tool": "工具名", "args": {{...}}, "confidence": 0.0~1.0,
               "missing": ["用户没说、但调用必须知道的参数"]}}],
  "unsupported": ["用户要求、但上面工具清单里没有对应工具的事"]}}

已知通讯录（名字 → 邮箱），收款方/收件人写的是名字时直接换成邮箱：
{contacts}

规则：
1. 一句话里可能有多个动作。用“并 / 然后 / 再 / 接着 / 同时 / 顺便 / ，”连接的独立动作，
   必须拆成多条 action，按执行顺序排列；每条 action 只做一件事。
2. 只是同一个动作的多个参数时不要拆（“给 A 转 100 元并备注 B”仍是一条 pay）。
3. 用户没说清楚的参数不要编造具体值，写进 missing。数值要换算（两千 → 2000，两万 → 20000）。
4. 意图明确但参数不全时仍然给出 tool，把缺的写进 missing——不要因为缺参数就放弃。
5. **工具清单里没有的能力，不要硬塞进某个 tool，也不要悄悄丢掉**：写进 unsupported。
   例如“订机票”在清单里有 book_flight 就用它；没有就写进 unsupported。
6. confidence 表示“这句话是否唯一对应这一个动作”：说法含糊、目标可能有多个时给低分（<0.6）。
7. 表名、路径、邮箱保持用户原话，不要翻译成英文。日期一律换算成 YYYY-MM-DD：
   今天是 {today}，“明天”= {tomorrow}，“后天”= 再往一天，不要留“明天”这种相对说法。
8. 最多 5 条 action；超过就取前 5 条，并在 intent 里注明“用户一次要求了更多动作”。
""".strip()


@dataclass
class Parse:
    """一次解析的结果。action 为 None 表示没解析出可执行动作。"""

    action: dict | None = None
    confidence: float = 0.0
    missing: list = field(default_factory=list)
    intent: str = ""
    source: str = ""          # llm / rule
    error: str = ""
    raw: str = ""
    notes: list = field(default_factory=list)   # 实体消歧等说明，给人看的

    def ok(self) -> bool:
        return bool(self.action and self.action.get("tool"))

    def blocking(self) -> list:
        """真正卡住的缺参数（必填项），用来决定要不要抬风险、要不要提示用户补充。"""
        req = REQUIRED.get((self.action or {}).get("tool", ""), [])
        return [m for m in self.missing if any(str(m).startswith(r) for r in req)]

    def describe(self) -> str:
        if not self.action:
            return self.intent or "（没解析出动作）"
        a = self.action
        args = ", ".join(f"{k}={v!r}" for k, v in (a.get("args") or {}).items())
        return f"{a['tool']}({args})" if args else f"{a['tool']}(参数未定)"


@dataclass
class Plan:
    """一句话解析出来的有序动作列表。一句话干两件事是常态，不能只认第一件。"""

    actions: list = field(default_factory=list)
    intent: str = ""
    error: str = ""
    source: str = ""
    cached: bool = False          # True = 命中磁盘缓存，本次没有发新请求
    raw: str = ""
    skipped: list = field(default_factory=list)      # [(工具名, 原因)] 解析出来但跑不了
    unsupported: list = field(default_factory=list)  # 用户要了、但系统没有这个能力

    def ok(self) -> bool:
        return any(p.ok() for p in self.actions)

    def describe(self) -> str:
        return " → ".join(p.describe() for p in self.actions) or (self.error or "（没解析出动作）")


class NLParser:
    """规则优先、模型兜底。规则只认"结构明确的输入"，模糊的一律交给模型。"""

    def __init__(self, judge=None) -> None:
        self.judge = judge

    # -- 对外 --
    def analyse_plan(self, text: str) -> Plan:
        """主入口：一句话 → 有序动作列表。命令行/JSON 直接解析，其余问模型。"""
        s = (text or "").strip()
        if not s:
            return Plan(error="空输入")

        if s.startswith("{"):
            d = _try_json(s)
            if d:
                return Plan([Parse(d, 1.0, [], "", "rule")], source="rule")
            return Plan(error="JSON 写错了，检查引号和逗号")

        head, _, rest = s.partition(" ")
        if head in TOOL_SPEC and "=" in rest:
            args = {}
            for chunk in re.split(r"[,\s]+", rest.strip()):
                if "=" in chunk:
                    k, _, v = chunk.partition("=")
                    args[k.strip()] = _coerce(v.strip())
            return Plan([Parse({"tool": head, "args": args}, 1.0, [], "", "rule")], source="rule")

        if self.judge is not None and getattr(self.judge, "available", False):
            p = self.parse_llm(s)
            if p.ok():
                return p
            rule = parse_rule(s)
            if rule.ok():
                rule.error = p.error or ""
                rule.confidence = min(rule.confidence, 0.55)
                rule.intent = rule.intent or p.intent
                return Plan([rule], p.intent, p.error, "rule")
            return p

        return Plan([parse_rule(s)], source="rule")

    def analyse(self, text: str) -> Parse:
        """兼容旧接口：只要第一步。"""
        p = self.analyse_plan(text)
        return p.actions[0] if p.actions else Parse(error=p.error, intent=p.intent, raw=p.raw)

    def parse_llm(self, text: str) -> Plan:
        spec = "\n".join(
            f'- {name}：{desc}；参数：{", ".join(args) if args else "无"}'
            for name, (desc, args) in TOOL_SPEC.items())
        try:
            from directory import CONTACTS
            contacts = "；".join(f"{k} → {v[0]}" for k, v in CONTACTS.items())
        except Exception:
            contacts = "（无）"
        from datetime import date, timedelta
        today = date.today()
        system = SYSTEM_PROMPT.format(spec=spec, contacts=contacts,
                                      today=today.isoformat(),
                                      tomorrow=(today + timedelta(days=1)).isoformat())
        user = f"用户的指令：\n{text}"
        raw, cached, err = self.judge.chat(system, user, max_tokens=520, cache_ns="nlu")
        if err:
            return Plan(error=err, raw=raw)
        plan = parse_plan_json(raw, text)
        plan.source = "llm"
        plan.cached = bool(cached)
        return plan

    # 兼容旧名字
    parse = analyse


def parse_plan_json(raw: str, text: str = "") -> Plan:
    """把模型返回的文本变成有序动作列表；兼容旧的单动作 {"tool": ...} 写法。"""
    t = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
    i, k = t.find("{"), t.rfind("}")
    if i < 0 or k < 0:
        return Plan(error=f"模型没返回 JSON：{t[:60]}", raw=raw)
    try:
        d = json.loads(t[i:k + 1])
    except json.JSONDecodeError as e:
        return Plan(error=f"模型 JSON 解析失败：{e}", raw=raw)

    intent = str(d.get("intent", ""))[:100]
    items = d.get("actions")
    if not isinstance(items, list):
        items = [d] if d.get("tool") else []
    items = [it for it in items if isinstance(it, dict)]

    actions, skipped = [], []
    for it in items[:5]:
        p = _one_action(it, intent, raw)
        if p.ok():
            actions.append(p)
        else:
            skipped.append((str(it.get("tool") or "?"), p.error or "解析失败"))

    unsupported = [str(u) for u in (d.get("unsupported") or []) if str(u).strip()][:5]

    if not actions:
        if skipped:
            reason = f"{skipped[0][0]}：{skipped[0][1]}"
        elif unsupported:
            reason = "系统没有这个能力：" + "、".join(unsupported)
        else:
            reason = str(d.get("reason") or "模型没给出可执行动作")
        return Plan(error=reason, intent=intent, raw=raw,
                    skipped=skipped, unsupported=unsupported)
    return Plan(actions, intent, raw=raw, skipped=skipped, unsupported=unsupported)


def resolve_entities(tool: str, args: dict) -> tuple[dict, list]:
    """把"小明""公司账户"换成真实邮箱。查得到就消歧，查不到才留给下一层当不确定性。"""
    try:
        from directory import resolve_contact
    except Exception:
        return args, []

    notes = []
    if tool in ("pay", "send_email") and "to" in args and "@" not in str(args["to"]):
        hit = resolve_contact(str(args["to"]))
        if hit:
            notes.append(f"to: {args['to']} → {hit[0]}（通讯录：{hit[1]}）")
            args["to"] = hit[0]
    return args, notes


def _one_action(d: dict, intent: str, raw: str) -> Parse:
    tool = d.get("tool") or None
    if not tool:
        return Parse(error="动作里没有 tool", raw=raw)
    if tool not in TOOL_SPEC:
        return Parse(error=f"模型给了不存在的工具 {tool}", raw=raw)

    args = d.get("args") if isinstance(d.get("args"), dict) else {}
    allowed = set(TOOL_SPEC[tool][1])
    args = {k: v for k, v in args.items() if k in allowed and v not in ("", None)}
    args, extra = normalize(tool, args)
    args, notes = resolve_entities(tool, args)

    missing = [str(m) for m in (d.get("missing") or [])] + extra
    if notes:   # to 已经由通讯录补齐，就不再算缺参数
        missing = [m for m in missing if not str(m).startswith("to")]
    for r in REQUIRED.get(tool, []):
        if r not in args and not any(str(m).startswith(r) for m in missing):
            missing.append(r)

    try:
        conf = max(0.0, min(1.0, float(d.get("confidence", 0.6))))
    except (TypeError, ValueError):
        conf = 0.6
    if notes:
        conf = max(conf, 0.85)   # 名字落到了真实邮箱，这一步的可信度反而更高

    return Parse({"tool": tool, "args": args}, conf, missing, intent, "llm", raw=raw, notes=notes)


def parse_model_json(raw: str, text: str = "") -> Parse:
    """兼容旧调用：只取计划里的第一步。"""
    plan = parse_plan_json(raw, text)
    return plan.actions[0] if plan.actions else Parse(error=plan.error, intent=plan.intent, raw=raw)


ENV_ALIASES = {
    "prod": {"prod", "prd", "production", "生产", "生产环境", "线上", "正式", "正式环境"},
    "staging": {"staging", "stage", "stg", "test", "测试", "测试环境", "预发", "沙箱", "灰度"},
}


def normalize(tool: str, args: dict) -> tuple[dict, list]:
    """把模型给的自由文本参数收敛成工具能吃的值。

    模型经常把 env 写成"生产环境"、把金额写成"两千"，直接塞给门控会变成
    "unknown_env" 的假警报。这一步是解析层和门控层之间的适配器：
    收敛得了就收敛，收敛不了就老老实实报缺参数。
    """
    args = dict(args)
    extra: list = []

    if tool == "deploy" and "env" in args:
        v = str(args["env"]).strip().lower()
        hit = next((k for k, al in ENV_ALIASES.items() if v in al), None)
        if hit:
            args["env"] = hit
        else:
            extra.append(f"env（{args['env']} 不是 prod/staging）")

    for key in ("amount",):
        if key in args and isinstance(args[key], str):
            try:
                args[key] = float(re.sub(r"[^\d.]", "", args[key]))
            except ValueError:
                extra.append(key)
                args.pop(key, None)

    if tool in ("pay", "send_email") and "to" in args:
        if "@" not in str(args["to"]):
            extra.append("to（要邮箱地址，不是人名）")

    if "count" in args and isinstance(args["count"], str):
        m = re.search(r"\d+", args["count"])
        args["count"] = int(m.group(0)) if m else 0

    if tool == "book_flight" and "date" in args:
        raw = str(args["date"])
        d = _norm_date(raw)
        if d:
            args["date"] = d
        else:
            extra.append(f"date（{raw} 认不出是哪天）")
            args.pop("date", None)

    return args, extra


def _norm_date(s: str) -> str:
    """把"明天""下周三""11月7日"落成 YYYY-MM-DD。

    模型不该猜今天的日期——它没有时钟。相对日期的换算放在这里做，
    既确定又免费；模型只负责认出"这是个相对日期"。
    """
    from datetime import date, timedelta

    t = (s or "").strip()
    if not t:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        return t

    today = date.today()
    rel = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2, "大后天": 3}
    for k, n in rel.items():
        if k in t:
            return (today + timedelta(days=n)).isoformat()

    m = re.fullmatch(r"(\d{1,2})[月/-](\d{1,2})[日号]?", t)
    if m:
        try:
            d = date(today.year, int(m.group(1)), int(m.group(2)))
            if d < today:                      # 说"1月5日"但已经过了 → 指明年
                d = date(today.year + 1, int(m.group(1)), int(m.group(2)))
            return d.isoformat()
        except ValueError:
            return ""

    m = re.search(r"(下)?(?:周|星期|礼拜)([一二三四五六日天])", t)
    if m:
        ch = m.group(2)[0]
        idx = 6 if ch in ("日", "天") else "一二三四五六".find(ch)
        if m.group(1):                      # 下周三 = 下一个自然周的周三
            monday = today + timedelta(days=7 - today.weekday())
            return (monday + timedelta(days=idx)).isoformat()
        return (today + timedelta(days=(idx - today.weekday()) % 7)).isoformat()

    return ""


def parse_rule(s: str) -> Parse:
    """没有模型时的兜底：只认最直白的说法。宁可说"看不懂"，也不要猜错。"""
    addr = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+", s)
    money = re.search(r"(\d+(?:\.\d+)?)\s*(万|千|百)?\s*(?:元|块|人民币)?", s)
    idents = re.findall(r"[A-Za-z_][A-Za-z0-9_:.\-]*", s)

    def amount() -> float | None:
        if not money:
            return None
        v = float(money.group(1))
        return v * {"万": 10000, "千": 1000, "百": 100}.get(money.group(2) or "", 1)

    if addr and re.search(r"转|付|打款|汇款|报销", s) and amount():
        return Parse({"tool": "pay", "args": {"amount": amount(), "to": addr.group(0)}},
                     0.7, [], s, "rule")

    if re.search(r"删|清理|清掉", s):
        cnt = re.search(r"(\d+)\s*(?:条|行|个|笔)", s)
        missing = [] if (idents and cnt) else \
            [x for x in ("table" if not idents else "", "count" if not cnt else "") if x]
        return Parse({"tool": "delete_records",
                      "args": {"table": idents[0] if idents else "",
                               "count": int(cnt.group(1)) if cnt else 0}},
                     0.5 if missing else 0.7, missing, s, "rule")

    if addr and re.search(r"邮件|发信|寄|发给|抄送", s):
        body = s.split(addr.group(0), 1)[1].strip(" ，。:：") if addr.group(0) in s else ""
        return Parse({"tool": "send_email", "args": {"to": addr.group(0), "body": body}},
                     0.6, [], s, "rule")

    if "部署" in s or "deploy" in s.lower():
        env = "prod" if ("生产" in s or "prod" in s.lower()) else "staging"
        return Parse({"tool": "deploy", "args": {"env": env}}, 0.6, [], s, "rule")

    if "发布" in s:
        return Parse({"tool": "publish_post", "args": {"title": s}}, 0.5, [], s, "rule")

    if "读取" in s or "打开" in s or "看看" in s:
        path = re.search(r"[\w./\-]+\.(?:txt|csv|json|md|py)", s)
        if path:
            return Parse({"tool": "read_file", "args": {"path": path.group(0)}}, 0.6, [], s, "rule")

    return Parse(error="没听懂这句话")


def _try_json(s: str) -> dict | None:
    try:
        d = json.loads(s)
    except json.JSONDecodeError:
        return None
    tool = d.get("tool") or d.get("action")
    if not tool:
        return None
    return {"tool": tool, "args": d.get("args") or d.get("params") or {}}


def _coerce(v: str):
    v = v.strip().strip('"\'')
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    return v


if __name__ == "__main__":
    # 不联网的自检：规则层能不能兜住最直白的说法，以及多动作能不能拆开
    cases = [
        "pay amount=120 to=vendor@external.com",
        '{"tool": "deploy", "args": {"env": "prod"}}',
        "给 billing@partner.com 转 80 元",
        "把 users 表里 500 条测试账号删了",
        "部署到生产环境",
        "删除数据库",
    ]
    for c in cases:
        p = NLParser().analyse(c)
        print(f"{c:<42} → {p.describe():<52} 置信 {p.confidence:.2f} 缺 {p.missing} {p.error}")

    # 离线验证多动作拆分：不走模型，直接喂模型该返回的 JSON
    demo = ('{"intent": "先删库再付款", "actions": ['
            '{"tool": "delete_records", "args": {}, "confidence": 0.4, "missing": ["table"]},'
            '{"tool": "pay", "args": {"amount": 9999, "to": "小明"}, "confidence": 0.6}]}')
    plan = parse_plan_json(demo)
    print("\n多动作：", plan.describe())
    for i, p in enumerate(plan.actions, 1):
        print(f"  {i}. {p.describe():<44} 缺 {p.blocking()} 置信 {p.confidence:.2f}")
