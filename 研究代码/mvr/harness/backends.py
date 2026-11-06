"""快照后端的"可替换实现"：同一个接口，Windows 和 Linux 都能跑。

为什么需要它：
    组员在 Linux 上用 overlayfs / CRIU，你在 Windows 上没有这两个东西。
    但这不影响实验——论文里"快照"要的是一个**语义**：给定当前工作区，
    产出一个隔离的、可丢弃的分支，且成本可测。底层是 overlayfs 还是硬链接，
    只影响常数项，不影响 DIR / 成功率的结论。

约定（很重要，否则硬链接会互相污染）：
    分支里的任何写入都必须走 write_cow()——先写临时文件再 os.replace，
    replace 会把硬链接"断开"，于是只改分支自己。直接 open(path,'w') 会改到原文件。
"""
from __future__ import annotations

import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


def _link_or_copy(src: str, dst: str) -> str:
    try:
        os.link(src, dst)          # NTFS / ext4 都支持，几乎零成本
        return "link"
    except OSError:
        try:
            shutil.copy2(src, dst)  # exFAT、跨卷、权限不足时退化成拷贝
            return "copy"
        except OSError:
            return "skip"           # 被占用的文件（锁、临时文件）直接跳过


def write_cow(path: str | Path, content: str) -> None:
    """写时复制：先写临时文件，再原子替换，从而不触碰被硬链接的原始版本。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, p)


@dataclass
class ForkResult:
    path: Path
    cost_ms: float
    mode: str


class DirSnapshotter:
    """目录级快照：fork 一份硬链接树，测出的 cost_ms 直接喂给 CostModel。"""

    def __init__(self, root: str | Path, keep: bool = True) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.keep = keep
        self.created: list[Path] = []

    def fork(self, name: str | None = None) -> ForkResult:
        name = name or f"br_{uuid.uuid4().hex[:8]}"
        dst = self.root / name
        t0 = time.perf_counter()
        mode = "link"
        for src_dir, dirs, files in os.walk(self.root):
            rel = Path(src_dir).relative_to(self.root)
            if rel.parts and rel.parts[0] == name:      # 别把自己复制进去
                continue
            (dst / rel).mkdir(parents=True, exist_ok=True)
            for f in files:
                m = _link_or_copy(str(Path(src_dir) / f), str(dst / rel / f))
                mode = "copy" if m == "copy" else mode
        cost_ms = (time.perf_counter() - t0) * 1000
        self.created.append(dst)
        return ForkResult(dst, cost_ms, mode)

    def release(self, path: str | Path) -> None:
        if self.keep:
            return
        shutil.rmtree(path, ignore_errors=True)


def measure_fork_cost(src: str | Path, repeats: int = 5) -> dict:
    """给 CostModel 填真值用：测一次你机器上的 fork 成本（毫秒）。"""
    snap = DirSnapshotter(Path(src).parent, keep=False)
    ts, modes = [], set()
    for _ in range(repeats):
        r = snap.fork()
        ts.append(r.cost_ms)
        modes.add(r.mode)
        snap.release(r.path)
    ts.sort()
    return {"median_ms": ts[len(ts) // 2], "min_ms": ts[0], "max_ms": ts[-1], "modes": sorted(modes)}
