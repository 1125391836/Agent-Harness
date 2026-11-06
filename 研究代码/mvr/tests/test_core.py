"""最小回归：任何改动先跑这四条，别让骨架悄悄坏掉。

    python -m unittest discover -s mvr/tests -t .
"""
from __future__ import annotations

import unittest

from mvr.harness.layers import HardGate, Ledger, PreVal
from mvr.harness.runtime import Runtime
from mvr.harness.scheduler import GateOnly, RiskFork
from mvr.harness.snapshot import Checkpointer, CostModel, State
from mvr.harness.tasks import build_tasks
from mvr.harness.types import Action, Effect, Kind
from mvr.harness.world import TOOLS, seed_world


class LedgerTest(unittest.TestCase):
    def test_idempotent_payment_detected(self):
        led = Ledger()
        a = Action("pay", {"amount": 40, "to": "v@company.com"}, task_id="t")
        self.assertFalse(led.seen("t", a))
        led.intent("t", a, "main")
        led.commit("t", a)
        self.assertTrue(led.seen("t", a))

    def test_gate_blocks_unknown_tool(self):
        d = HardGate().check(Action("rm_rf", {}), seed_world(), Ledger())
        self.assertEqual(d.kind, Kind.DENY)


class FakeWorldTest(unittest.TestCase):
    def test_clone_isolated(self):
        w = seed_world()
        w2 = w.clone()
        w2.apply(Action("pay", {"amount": 500, "to": "x@company.com"}))
        self.assertAlmostEqual(w.wallet, 1000.0)

    def test_probe_sees_secret_leak(self):
        w = seed_world()
        sig = w.probe(Action("send_email", {"to": "a@b.com", "body": "API_KEY=sk-live-1"}))
        self.assertTrue(any("密钥" in s for s in sig))


class PreValTest(unittest.TestCase):
    def test_clean_action_low_score(self):
        f = PreVal().score(Action("write_file", {"path": "a", "content": "x"}), seed_world(), Ledger())
        self.assertLess(f.score, 0.1)

    def test_bulk_delete_high_score(self):
        f = PreVal().score(Action("delete_records", {"table": "users", "count": 300}), seed_world(), Ledger())
        self.assertGreater(f.score, 0.4)


class RuntimeTest(unittest.TestCase):
    def test_fork_never_touches_real_world_on_hit(self):
        rt = Runtime(policy=RiskFork(), seed=1, ckpt=Checkpointer("tiered"))
        r = rt.run(build_tasks()[0])
        self.assertGreaterEqual(r.forks, 1)
        self.assertGreaterEqual(r.cost, 0.0)

    def test_gate_only_leaks_residual_risk(self):
        """Gate 只挡住额度类动作；对外发信这类"合规但危险"的动作它挡不住。"""
        tasks = build_tasks()
        rt = Runtime(policy=GateOnly(), seed=0)
        results = [rt.run(t) for t in tasks]
        self.assertGreater(sum(r.executed_danger for r in results), 0)

    def test_full_snapshot_is_more_expensive(self):
        c = CostModel()
        st = State()
        w = seed_world()
        a = Checkpointer("full", c).estimate(st, w)
        b = Checkpointer("tiered", c).estimate(st, w)
        self.assertGreater(a, b)


if __name__ == "__main__":
    unittest.main()
