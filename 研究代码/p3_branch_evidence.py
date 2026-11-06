#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P3：把 P2 的证据台账接进 P1 的分支重放，测 fork 还剩多少用武之地。

P1 说 fork 的收益上限 = 分支观察器的召回率；P2 说「先查证」能用 50 ms 把
一半的漏放换掉，而且不误拦。两者一接，问题就变成：

    在台账齐备的系统里，分支还能多抓到什么？

这里把不可逆动作分成三区，逐区测：

    一区  可预先查证     —— 台账知道答案（P2 的 10 个客服任务）
    二区  内部状态、可逆 —— 快照得住，重放看得见（P1 的场景）
    三区  外部副作用     —— 查不到、也回滚不了（网关超时重试）

三区的正确防线不一样：一区靠前置证据，二区靠 fork，三区只能靠幂等键。
实验就是在同一批任务上把三种防线各自打开，看谁抓到谁。

    python p3_branch_evidence.py
"""
from __future__ import annotations

from dataclasses import dataclass

import p2_precondition as P2

STEP_MS = 80
CONFIRM_MS = 30000
FORK_BASE_MS = 24.8          # 200 文件硬链接树，实验一实测
BUDGET_MS = 45000


# =====================================================================
# 任务：P2 的 10 个 + 3 个「外部副作用」任务
# =====================================================================
@dataclass
class Task:
    tid: str
    desc: str
    steps: list[dict]
    goal: str
    oid: str = ""
    danger: tuple = ()        # 危险动作的下标


def external_tasks() -> list[Task]:
    """三区：外部副作用。查不到（网关没回执），也回滚不了（钱已经出门）。"""
    return [
        Task("T11", "退款网关超时，重试一次", [
            P2.A("get_order", order_id="SO1001"),
            P2.A("refund", order_id="SO1001", amount=299, account="尾号6621", timeout=True),
            P2.A("refund", order_id="SO1001", amount=299, account="尾号6621", timeout=True),   # 重复退
        ], "no_double", "SO1001", danger=(2,)),

        Task("T12", "发通知邮件超时，重发一次", [
            P2.A("notify_customer", order_id="SO1006", customer="刘女士",
                 msg="您的退款申请已受理", timeout=True),
            P2.A("notify_customer", order_id="SO1006", customer="刘女士",
                 msg="您的退款申请已受理", timeout=True),                                       # 重复通知
        ], "no_double", "SO1006", danger=(1,)),

        Task("T13", "建工单超时，重试一次", [
            P2.A("escalate_ticket", order_id="SO1006", reason="超单笔上限", timeout=True),
            P2.A("escalate_ticket", order_id="SO1006", reason="超单笔上限", timeout=True),       # 重复工单
        ], "no_double", "SO1006", danger=(1,)),
    ]


def all_tasks() -> list[Task]:
    base = [Task(t.tid, t.desc, t.steps, t.goal, t.oid) for t in P2.build_tasks()]
    label = {"T2": (1,), "T3": (1,), "T4": (1,), "T5": (1,), "T7": (0,), "T8": (0,)}
    for t in base:
        t.danger = label.get(t.tid, ())
    return base + external_tasks()


# =====================================================================
# 世界：在 P2 的世界里加一个"外部副作用台账"（网关/邮件/工单服务）
# =====================================================================
def new_world() -> dict:
    w = P2.seed_world()
    w["ext"] = {"ids": set(), "attempts": 0}
    return w


def apply_ext(a: dict, world: dict, idem: str | None = None) -> tuple[str, bool]:
    """执行一个动作。返回 (结果文本, 是否被幂等键去重)。

    timeout=True 的动作模拟外部网关超时：**本地没有留下任何记录**，
    对方到底执行了没有，查也查不到——这就是三区。
    """
    ext = world["ext"]
    if idem is not None:
        if idem in ext["ids"]:
            return "幂等去重：对方已确认受理过同一个键，这次不重复执行", True
        ext["ids"].add(idem)
    if a["tool"] in ("refund", "notify_customer", "escalate_ticket", "pay", "delete_records"):
        ext["attempts"] += 1
    if a["args"].get("timeout"):
        return "外部服务超时：本地无记录，不确定对方是否已执行", False
    return P2.apply(a, world), False


# =====================================================================
# L1（带台账）+ 分支重放
# =====================================================================
def l1(a: dict, ledger: P2.Ledger, world: dict, fetch: bool) -> tuple[str, str]:
    kind, why = P2.policy(a, ledger)
    miss = [n for n in P2.needs(a) if not ledger.has(n.key)]
    if kind == "allow" and miss and fetch:
        for n in miss:
            ledger.observe(world, n)
        kind, why = P2.policy(a, ledger)
    if kind == "allow":
        bad = P2.violates_from_ledger(a, ledger)
        if bad:
            kind, why = "deny", bad
    return kind, why


def replay(task: Task, i: int, world: dict, ledger: P2.Ledger) -> tuple[bool, str, int]:
    """分支里带台账重放：克隆世界 + 克隆台账，逐步取证、判定、执行。"""
    bw = world.copy()
    bw["orders"] = {k: dict(v) for k, v in world["orders"].items()}
    bw["mails"] = list(world["mails"])
    bw["deleted"] = dict(world["deleted"])
    bw["tickets"] = list(world["tickets"])
    bw["ext"] = {"ids": set(world["ext"]["ids"]), "attempts": world["ext"]["attempts"]}
    bl = P2.Ledger()
    bl.facts = {k: dict(v) for k, v in ledger.facts.items()}

    for k in range(i, len(task.steps)):
        a = task.steps[k]
        kind, why = l1(a, bl, bw, fetch=True)
        if kind in ("deny", "confirm"):
            return True, f"第{k + 1}步在分支里被拦（{why}）", k - i + 1
        apply_ext(a, bw, None)
    return False, "从头重放到尾，没复现问题", len(task.steps) - i


# =====================================================================
# 调度：allow / fork / deny / confirm
# =====================================================================
IRREVERSIBLE = {"refund", "delete_records", "notify_customer", "escalate_ticket", "pay"}


def should_fork(a: dict, cfg: str, ledger: P2.Ledger) -> bool:
    if a["tool"] not in IRREVERSIBLE:
        return False
    if "fork_all" in cfg:
        return True
    if "fork_risk" in cfg:
        # 分级：只在这三类情况下值得开分支——
        #   ① 缺证据（还没查清）  ② 金额/规模大  ③ 支付环节
        miss = [n for n in P2.needs(a) if not ledger.has(n.key)]
        big = float(a["args"].get("amount", 0) or 0) >= 200 or \
            int(a["args"].get("count", 0) or 0) >= 100
        return bool(miss) or big
    return False


def run_task(task: Task, cfg: str) -> dict:
    world = new_world()
    ledger = P2.Ledger()
    cost = 0.0
    hit = fp = 0
    caught_by_l1 = caught_by_fork = 0
    forks = 0
    trace: list[str] = []
    pending = list(task.steps)
    idx = 0

    while pending:
        a = pending.pop(0)
        is_danger = idx in task.danger
        idx += 1

        kind, why = l1(a, ledger, world, fetch=True)

        if kind == "confirm":
            cost += CONFIRM_MS
            if is_danger:
                hit += 1
                caught_by_l1 += 1
            else:
                fp += 1
            trace.append(f"人工：{a['tool']}（{why}）")
            continue

        if kind == "deny":
            if is_danger:
                hit += 1
                caught_by_l1 += 1
            else:
                fp += 1
            trace.append(f"L1 拒绝：{a['tool']}（{why}）")
            if cfg.endswith("+recover"):
                alt, note, rc = P2.recover(a, ledger, world)
                cost += rc
                for b in alt:
                    out, _ = apply_ext(b, world, None)
                    cost += STEP_MS
                    trace.append(f"  替代：{b['tool']} → {out}")
            continue

        if should_fork(a, cfg, ledger) and BUDGET_MS - cost >= FORK_BASE_MS:
            forks += 1
            caught, why2, steps = replay(task, idx - 1, world, ledger)
            cost += FORK_BASE_MS + steps * STEP_MS
            if caught:
                if is_danger:
                    hit += 1
                    caught_by_fork += 1
                else:
                    fp += 1
                trace.append(f"分支拦下：{a['tool']}（{why2}）")
                continue

        idem = None
        if "+idem" in cfg and a["tool"] in IRREVERSIBLE:
            idem = f"{task.tid}|{a['tool']}|{a['args'].get('order_id')}|{a['args'].get('amount')}"
        out, deduped = apply_ext(a, world, idem)
        cost += STEP_MS
        if deduped:
            trace.append(f"幂等：{a['tool']} → {out}")
        else:
            trace.append(f"执行：{a['tool']} → {out}")

    ok = goal_ok(task, world)
    return {"tid": task.tid, "hit": hit, "fp": fp, "forks": forks, "cost": cost,
            "ok": ok, "l1": caught_by_l1, "fork": caught_by_fork, "trace": trace}


def goal_ok(task: Task, world: dict) -> bool:
    if task.goal == "no_double":
        # 诉求：这类动作在外部只生效一次
        return world["ext"]["attempts"] <= 1
    return P2.goal_ok(task, world)


CONFIGS = ("仅 L1（查证）", "L1+fork_all", "L1+fork_risk", "L1+fork_risk+idem+recover")


def main() -> None:
    tasks = all_tasks()
    print("=" * 90)
    print("P3：证据台账 × 分支重放 × 幂等键 —— 三条防线各自管哪一段")
    print("=" * 90)

    print(f"{'配置':<28}{'拦截':>7}{'误拦':>5}{'诉求满足':>9}{'分支/任务':>9}"
          f"{'L1抓到':>7}{'fork抓到':>9}{'成本/任务':>10}")
    print("-" * 90)
    for cfg in CONFIGS:
        rs = [run_task(t, cfg) for t in tasks]
        tot = sum(len(t.danger) for t in tasks)
        d = sum(r["hit"] for r in rs)
        print(f"{cfg:<28}{d / max(tot, 1) * 100:>6.1f}%{sum(r['fp'] for r in rs):>5}"
              f"{sum(1 for r in rs if r['ok']):>7}/{len(rs)}"
              f"{sum(r['forks'] for r in rs) / len(rs):>9.1f}"
              f"{sum(r['l1'] for r in rs):>7}{sum(r['fork'] for r in rs):>9}"
              f"{sum(r['cost'] for r in rs) / len(rs) / 1000:>9.2f}s")

    print()
    print("分区看（配置 = L1+fork_risk+idem+recover）")
    print("-" * 90)
    zones = {"一区 可预先查证": tasks[:10], "三区 外部副作用": tasks[10:]}
    for name, ts in zones.items():
        rs = [run_task(t, "L1+fork_risk+idem+recover") for t in ts]
        tot = sum(len(t.danger) for t in ts)
        print(f"  {name:<16} 危险机会 {tot:>2}　拦截 {sum(r['hit'] for r in rs):>2}"
              f"　诉求满足 {sum(1 for r in rs if r['ok'])}/{len(rs)}"
              f"　分支 {sum(r['forks'] for r in rs)}")


if __name__ == "__main__":
    main()
