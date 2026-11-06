"""快照与分支：全量快照 vs 副作用感知快照。

这是论文"成本结构"那一维的物理载体。真正的实现里：
- 全量 = 整机/容器 checkpoint（见 [4] 的六工具基准，2GiB 量级 1~7s）；
- 副作用感知 = 只冻结外部副作用契约（账本 + 幂等键 + 环境 overlay 引用），
  沙箱文件系统用 overlay upperdir 分层，上下文只做结构共享（copy-on-write dict）。

这里用 CostModel 把两者映射成同一个"决策预算"货币 CU，好让调度器在
同一把尺子上权衡 fork 成本与人工确认成本。
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field

from .layers import Ledger
from .world import World

FULL_EXTRA_KB = 64.0     # 全量快照额外要落盘的体积（模拟 200MB 内存镜像的折算）
FULL_EXTRA_MS = 120.0    # 全量快照的固定停顿


@dataclass
class State:
    ctx: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    ledger: Ledger | None = None

    def clone(self) -> "State":
        s = State(ctx=list(self.ctx), files=dict(self.files), ledger=copy.deepcopy(self.ledger))
        return s


@dataclass
class CostModel:
    per_kb: float = 0.05
    per_ms: float = 0.002
    fork_base: float = 0.80
    confirm: float = 5.0
    step: float = 1.0


class Checkpointer:
    def __init__(self, mode: str = "tiered", cost: CostModel | None = None) -> None:
        assert mode in ("full", "tiered")
        self.mode = mode
        self.cost = cost or CostModel()

    def estimate(self, st: State, world: World) -> float:
        ctx_b = len(json.dumps(st.ctx, ensure_ascii=False).encode())
        env_b = sum(len(v.encode()) for v in st.files.values())
        if self.mode == "full":
            kb = (ctx_b + env_b) / 1024 + FULL_EXTRA_KB
            ms = FULL_EXTRA_MS
        else:
            kb = (ctx_b * 0.3 + env_b * 0.1) / 1024
            ms = 3.0
        return round(self.cost.fork_base + self.cost.per_kb * kb + self.cost.per_ms * ms, 4)

    def fork(self, st: State, world: World, k: int = 1) -> list[tuple[State, World]]:
        out = []
        for i in range(max(1, k)):
            ns = st.clone()
            nw = world.clone()
            ns.ctx.append(f"[fork#{i}]")
            out.append((ns, nw))
        return out
