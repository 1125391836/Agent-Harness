"""平台相关测试：Windows 上 fork 的隔离性，以及 LLM 预判的离线降级。

这两条是"能不能在 Windows 上做这个课题"的技术答案：
    - 硬链接树 + 写时复制，可以在 NTFS 上做出和 overlayfs 等价的分支语义；
    - 没有 API key 时自动退回启发式，实验依然可跑、可复现。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mvr.harness.backends import DirSnapshotter, write_cow
from mvr.harness.layers import Ledger
from mvr.harness.preval_llm import LLMPreVal
from mvr.harness.types import Action
from mvr.harness.world import seed_world


class DirSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        live = self.root / "live"
        (live / "notes").mkdir(parents=True)
        (live / "notes" / "a.md").write_text("original", encoding="utf-8")
        self.snap = DirSnapshotter(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fork_copies_tree(self):
        r = self.snap.fork()
        self.assertTrue((r.path / "live" / "notes" / "a.md").exists())
        self.assertGreaterEqual(r.cost_ms, 0.0)

    def test_write_cow_does_not_touch_source(self):
        r = self.snap.fork()
        write_cow(r.path / "live" / "notes" / "a.md", "branch only")
        self.assertEqual((self.root / "live" / "notes" / "a.md").read_text(encoding="utf-8"), "original")
        self.assertEqual((r.path / "live" / "notes" / "a.md").read_text(encoding="utf-8"), "branch only")


class LLMFallbackTest(unittest.TestCase):
    def test_without_key_degrades_to_heuristic(self):
        p = LLMPreVal(api_key="", cache_dir=tempfile.mkdtemp())
        self.assertIsNone(p.client)
        f = p.score(Action("delete_records", {"table": "users", "count": 300}), seed_world(), Ledger())
        self.assertGreater(f.score, 0.4)   # 与启发式 PreVal 同量级

    def test_unknown_tool_scores_max(self):
        p = LLMPreVal(api_key="", cache_dir=tempfile.mkdtemp())
        f = p.score(Action("rm_rf", {}), seed_world(), Ledger())
        self.assertEqual(f.score, 1.0)


if __name__ == "__main__":
    unittest.main()
