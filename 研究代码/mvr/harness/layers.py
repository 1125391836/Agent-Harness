"""L1 硬门控 + L2 PreVal 风险分级 + 副作用账本。

这三块共同支撑论文第三章：
- HardGate  是基线里最便宜的一层，也是"否决残余风险"论断的来源；
- PreVal    只输出 Features，不做决定——决定权属于调度器（可替换，这是解耦的意义）；
- Ledger    用幂等键 + 意图记录实现"不重复执行 + 可对账"，补偿和去重都靠它。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .types import Action, Decision, Effect, Features, Kind
from .world import TOOLS, World


# 各效果类型在分支内的危险信号信噪比 f（论文里作为可观测性参数显式建模）
EFFECT_FIDELITY = {
    "pay": 0.95,
    "send_email": 0.90,
    "delete_records": 0.85,
    "publish_post": 0.70,
    "write_db": 0.80,
    "deploy": 0.85,
}


@dataclass
class Entry:
    idem: str
    tool: str
    args: dict
    branch: str
    status: str = "intent"     # intent -> done | failed | compensated
    blast: int = 0


class Ledger:
    def __init__(self) -> None:
        self.entries: list[Entry] = []

    @staticmethod
    def key(task_id: str, a: Action) -> str:
        raw = json.dumps([task_id, a.tool, a.args], sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    def seen(self, task_id: str, a: Action) -> bool:
        k = self.key(task_id, a)
        return any(e.idem == k and e.status == "done" for e in self.entries)

    def intent(self, task_id: str, a: Action, branch: str) -> Entry:
        e = Entry(self.key(task_id, a), a.tool, dict(a.args), branch)
        self.entries.append(e)
        return e

    def commit(self, task_id: str, a: Action) -> None:
        for e in self.entries:
            if e.idem == self.key(task_id, a):
                e.status = "done"

    def done(self) -> list[Entry]:
        return [e for e in self.entries if e.status == "done"]


class HardGate:
    """L1：按效果类别 + 能力表 + 静态额度做的前置拦截。便宜、确定性、可审计。"""

    def __init__(
        self,
        pay_cap: float = 100.0,
        delete_cap: int = 100,
        mail_cap: int = 20,
        blocked_domains: tuple[str, ...] = ("attacker.io", "evil.com"),
        db_write: bool = True,
        cli_allowlist: bool = False,
        strict: bool = False,
    ) -> None:
        self.pay_cap = pay_cap
        self.delete_cap = delete_cap
        self.mail_cap = mail_cap
        self.blocked_domains = blocked_domains
        self.db_write = db_write
        self.cli_allowlist = cli_allowlist
        self.strict = strict  # 实验用：所有不可逆动作一律转人工，作为"最保守"对照

    def check(self, a: Action, world: World, ledger: Ledger) -> Decision:
        spec = TOOLS.get(a.tool)
        if spec is None:
            return Decision(Kind.DENY, 0, f"未知工具 {a.tool}")
        if self.cli_allowlist and spec.env.value == "real" and a.tool not in ("pay", "send_email"):
            return Decision(Kind.DENY, 0, "真实环境仅允许白名单工具")
        if a.tool == "pay" and float(a.args.get("amount", 0)) > self.pay_cap:
            return Decision(Kind.CONFIRM, 0, f"超出单笔额度 {self.pay_cap}")
        if a.tool == "delete_records" and int(a.args.get("count", 0)) > self.delete_cap:
            return Decision(Kind.CONFIRM, 0, f"超出批量删除额度 {self.delete_cap}")
        if a.tool == "send_email":
            rcpts = a.args.get("to")
            n = len(rcpts) if isinstance(rcpts, list) else 1
            if n > self.mail_cap:
                return Decision(Kind.CONFIRM, 0, f"收件人超过 {self.mail_cap}")
            if any(str(r).split("@")[-1] in self.blocked_domains for r in (rcpts if isinstance(rcpts, list) else [rcpts])):
                return Decision(Kind.DENY, 0, "收件域名在黑名单")
        if a.tool == "write_db" and not self.db_write:
            return Decision(Kind.DENY, 0, "数据库写权限关闭")
        if self.strict and spec.effect is Effect.IRREVERSIBLE:
            return Decision(Kind.CONFIRM, 0, "严格模式：不可逆动作一律人工确认")
        if ledger.seen(a.task_id, a):
            return Decision(Kind.ALLOW, 1, "幂等命中，直接短路")  # 已执行过，不重复执行也不重复记账
        return Decision.allow()


class NullGate:
    """完全不做拦截的对照组：用来量"什么都不做"的基准。"""

    def check(self, a, world, ledger):
        return Decision.allow()


class PreVal:
    """L2：风险分级。只产出 Features，永不替调度器做决定。"""

    def __init__(self, miss_rate: float = 0.25, seed: int = 0) -> None:
        import random
        self.miss_rate = miss_rate
        self.rng = random.Random(seed)

    def score(self, a: Action, world: World, ledger: Ledger, ctx: list[str] | None = None) -> Features:
        sig = world.probe(a)
        spec = TOOLS.get(a.tool)
        effect = spec.effect if spec else Effect.IRREVERSIBLE
        fid = EFFECT_FIDELITY.get(a.tool, 0.6)
        blast = self._blast(a, world)
        if not sig:
            base = 0.03
        else:
            base = min(0.95, 0.62 + 0.13 * (len(sig) - 1))
            base *= 0.6 + 0.4 * fid
            # 模拟"信号读不出来"：信噪比不足导致的漏检，这才是 PreVal 的真实短板
            if self.rng.random() < self.miss_rate * (1.0 - fid):
                base *= 0.2
        if a.tool == "pay" and ledger.seen(a.task_id, a):
            base = max(base, 0.78)
        return Features(score=round(min(base, 0.99), 3), effect=effect, blast=blast, fidelity=fid)

    @staticmethod
    def _blast(a: Action, world: World) -> int:
        if a.tool == "pay":
            return int(float(a.args.get("amount", 0)))
        if a.tool == "delete_records":
            return int(a.args.get("count", 0))
        if a.tool == "send_email":
            rc = a.args.get("to")
            return len(rc) if isinstance(rc, list) else 1
        if a.tool == "write_db":
            return 1
        if a.tool == "publish_post":
            return 1000
        if a.tool == "deploy":
            return 500
        return 0
