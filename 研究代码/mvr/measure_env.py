"""测你自己机器上的常数项，把 CostModel 从"拍脑袋"换成"实测"。

    python -m mvr.measure_env

输出直接抄进 harness/snapshot.py 的 CostModel 初值。论文里那条
C = c_s·S + c_t·T + c_l·L + c_h·H 的系数，必须来自这张表，不能事后调。
"""
from __future__ import annotations

import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

from .harness.backends import DirSnapshotter, measure_fork_cost, write_cow


def make_workspace(root: Path, files: int = 200, kb: int = 20) -> Path:
    live = root / "live"
    (live / "src").mkdir(parents=True, exist_ok=True)
    blob = "x" * (kb * 1024)
    for i in range(files):
        (live / "src" / f"f{i:03d}.txt").write_text(blob, encoding="utf-8")
    return live


def main() -> None:
    print(f"Python   : {sys.version.split()[0]}")
    print(f"平台     : {platform.platform()}")
    print(f"处理器   : {platform.processor() or platform.machine()}")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "bench"
        root.mkdir(parents=True, exist_ok=True)
        live = make_workspace(root)
        size_mb = sum(p.stat().st_size for p in live.rglob("*") if p.is_file()) / 1024 / 1024
        print(f"工作区   : 200 文件 / {size_mb:.1f} MB")

        m = measure_fork_cost(live.parent, repeats=7)
        print(f"fork 中位 : {m['median_ms']:.1f} ms  (min {m['min_ms']:.1f} / max {m['max_ms']:.1f}, 模式 {m['modes']})")

        snap = DirSnapshotter(root)
        r = snap.fork()
        target = r.path / "live" / "src" / "f000.txt"
        ts = []
        for _ in range(20):
            t0 = time.perf_counter()
            write_cow(target, "branch")
            ts.append((time.perf_counter() - t0) * 1000)
        print(f"写时复制 : {statistics.median(ts):.2f} ms/次")
        snap.release(r.path)

        per_mb = m["median_ms"] / max(size_mb, 0.01)
        print(f"\n建议 CostModel: per_ms 按 {per_mb:.2f} ms/MB 折算；"
              f"fork_base ≈ {m['median_ms']:.1f} ms 的固定开销")


if __name__ == "__main__":
    main()
