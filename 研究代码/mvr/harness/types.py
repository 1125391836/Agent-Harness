"""核心数据结构：Action / Effect / Features / Decision。

整篇论文的所有实验都只认这五个东西，任何模块都不许绕过它们直接干活。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Effect(str, Enum):
    NONE = "none"                # 无副作用：只读
    REVERSIBLE = "reversible"    # 可回滚：沙箱内改文件
    COMPENSABLE = "compensable"  # 可补偿：有对账/退款/撤回 API
    IRREVERSIBLE = "irreversible"  # 不可逆：钱、删除、对外发送、发布


class Env(str, Enum):
    SANDBOX = "sandbox"  # 只影响沙箱文件系统
    REAL = "real"        # 触达外部世界


class Kind(str, Enum):
    ALLOW = "allow"
    FORK = "fork"
    DENY = "deny"
    CONFIRM = "confirm"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    effect: Effect
    env: Env
    compensable_by: str | None = None   # 补偿工具名，COMPENSABLE 时必填


@dataclass
class Action:
    tool: str
    args: dict[str, Any]
    task_id: str = ""
    step: int = 0
    branch: str = "main"


@dataclass
class Step:
    action: Action
    dangerous: bool = False   # 隐藏标签，只给 oracle 与评测用，运行时不可见
    reason: str = ""


@dataclass
class Task:
    id: str
    steps: list[Step]
    budget: float = 8.0       # 每任务的决策预算（成本归一化单位 CU）


@dataclass
class Features:
    score: float        # 0~1 危险评分
    effect: Effect
    blast: int          # 影响基数
    fidelity: float     # 该效果类型的分支信噪比（可观测性参数）
    confidence: float = 0.8
    note: str = ""      # 判定理由，写日志/写论文案例用


@dataclass
class Decision:
    kind: Kind
    branches: int = 1
    reason: str = ""

    @staticmethod
    def allow(why: str = "") -> "Decision":
        return Decision(Kind.ALLOW, 1, why)

    @staticmethod
    def fork(why: str = "", k: int = 1) -> "Decision":
        return Decision(Kind.FORK, k, why)
