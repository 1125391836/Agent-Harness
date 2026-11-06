"""沙箱执行层：判定通过之后，把动作真的落下去（模拟账户，不连真实系统）。

为什么单独一层：判定层只输出 allow / fork / confirm / deny，执行是另一件事。
两者混在一起就会出经典问题——"判了没做""做了没记""重试变重复付款"。
沙箱记录每一次真正发生的副作用，并保留幂等键，供审计和重复检测使用。

这一版是**模拟执行**：不连支付、不发真邮件、不订真机票。
副作用落在内存里，用来验证"判定→执行"的链路是通的。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field


@dataclass
class Effect:
    idx: int
    tool: str
    detail: str
    reversible: bool
    duplicate_of: int = 0
    ts: float = 0.0


@dataclass
class Sandbox:
    effects: list = field(default_factory=list)
    seen: dict = field(default_factory=dict)   # 幂等键 → 第几条

    def key(self, action: dict) -> str:
        a = action.get("args") or {}
        return f"{action.get('tool')}|{json.dumps(a, sort_keys=True, ensure_ascii=False)}"

    def execute(self, action: dict) -> tuple[bool, str]:
        """执行一个动作。返回 (是否执行, 人类可读的结果)。

        重复动作照样执行，但会标出来——真实系统里"重试变成重复付款"就是这么来的，
        静默跳过反而掩盖问题。
        """
        tool = action.get("tool") or "?"
        a = action.get("args") or {}
        k = self.key(action)
        dup = self.seen.get(k, 0)

        detail, reversible = _describe(tool, a)
        if tool == "find_contact":          # 只读查询要真的把结果拿出来
            try:
                from directory import find_contact
                hits = find_contact(str(a.get("query", "")))
                detail = ("查到 " + "；".join(
                    f"{h['name']} <{h['email']}> {h['dept']}·{h['role']}" for h in hits)
                    if hits else f"通讯录里没有匹配「{a.get('query', '')}」的人")
            except Exception as e:
                detail = f"查通讯录失败：{e}"
        e = Effect(len(self.effects) + 1, tool, detail, reversible, dup, time.time())
        self.effects.append(e)
        self.seen.setdefault(k, e.idx)

        msg = detail
        if dup:
            msg += f"（与第 {dup} 条后果完全相同——真实系统里这就是重复操作）"
        return True, msg

    def show(self) -> None:
        if not self.effects:
            print("      沙箱里还没有任何副作用。")
            return
        for e in self.effects:
            tag = "可逆" if e.reversible else "不可逆"
            print(f"      #{e.idx:<2} {tag}  {e.detail}")


def _describe(tool: str, a: dict) -> tuple[str, bool]:
    """副作用长什么样 + 能不能撤销。判错的代价不对称，所以宁可按不可逆算。"""
    if tool == "pay":
        return f"付款 ¥{a.get('amount', '?')} → {a.get('to', '?')}（已扣款）", False
    if tool == "send_email":
        return f"邮件已投递 → {a.get('to', '?')}（不可撤回）", False
    if tool == "book_flight":
        return f"已出票 {a.get('date', '?')} {a.get('from_city', '?')}→{a.get('to_city', '?')}", True
    if tool == "delete_records":
        return f"已删除 {a.get('table', '?')} 表 {a.get('count', '?')} 条记录", False
    if tool == "write_db":
        return f"已写入 {a.get('table', '?')} 表 {a.get('rows', '?')} 行（覆盖写不可撤回）", False
    if tool == "publish_post":
        return f"已发布到 {a.get('channel', '?')}（对外可见）", False
    if tool == "deploy":
        return f"已部署 {a.get('env', '?')}", True
    if tool == "read_file":
        return f"读取 {a.get('path', '?')}", True
    if tool in ("http_get", "search"):
        return f"{tool} 完成", True
    try:                                    # 目录里的 40 多个工具：按副作用等级生成
        from catalog import describe as _cdesc
        return _cdesc(tool, a)
    except Exception:
        return f"{tool} 执行完成", True


if __name__ == "__main__":
    box = Sandbox()
    for act in [
        {"tool": "pay", "args": {"amount": 50000, "to": "finance@company.com"}},
        {"tool": "book_flight", "args": {"date": "2026-11-07", "from_city": "北京", "to_city": "上海"}},
        {"tool": "pay", "args": {"amount": 50000, "to": "finance@company.com"}},
    ]:
        print(box.execute(act))
    box.show()
