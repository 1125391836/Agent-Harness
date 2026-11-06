#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把「一个请求从进来到执行」的每一段打印出来——这是架构的走查，不是实验。

四个请求，分别落在一区 / 二区 / 三区，看同一套流程怎么换挡：

    python pipeline_demo.py          # 不调模型，规则观察器
    python pipeline_demo.py --llm    # 二区开模型观察器

真实系统里第 ① 步由 nlu.py 用大模型完成；这里为了可复现，直接把动作写死。
"""
from __future__ import annotations

import sys

import p2_precondition as P2
import p3_branch_evidence as P3
from llm_preval import LLMJudge

STEP_MS = 80
FORK_BASE_MS = P3.FORK_BASE_MS
EXTERNAL = {"refund", "notify_customer", "escalate_ticket", "pay"}
READ = {"get_order", "get_refund_status", "query_db"}
DECIDABLE = {"refund"}   # 查证 + 规则足以判定对错的一类


def zone_of(a: dict, ledger: P2.Ledger) -> str:
    """区间判定：依据「查证之后还能不能给确定判定」，不是工具名。"""
    if a["tool"] in READ:
        return "零区 只读"
    if a["args"].get("timeout"):
        return "三区 外部副作用"
    if P2.needs(a) and a["tool"] in DECIDABLE:
        return "一区 可预先查证"          # 查清了就能判对错
    return "二区 可观察不可枚举"           # 查得到事实，但事实不足以判定


DEFENSE = {
    "零区 只读": "不需要（没有副作用）",
    "一区 可预先查证": "前置证据 + recovery（不开分支）",
    "二区 可观察不可枚举": "fork + 观察器（规则看不见，只能跑一遍再读）",
    "三区 外部副作用": "幂等键（快照救不了）",
}


def run(title: str, steps: list[dict], judge: LLMJudge | None = None) -> None:
    world = P3.new_world()
    ledger = P2.Ledger()
    cost = 0.0
    print("=" * 78)
    print(f"请求：{title}")
    print("-" * 78)

    for n, a in enumerate(steps, 1):
        print(f"[① 解析] 第 {n} 步 {a['tool']}({a['args']})")
        kind, why = P2.policy(a, ledger)
        print(f"[② L1a ] {kind}" + (f" —— {why}" if why else " —— 规则表未触发"))

        miss = [x for x in P2.needs(a) if not ledger.has(x.key)]
        if miss and kind == "allow":
            for x in miss:
                ledger.observe(world, x)
                cost += STEP_MS
            print(f"[③ L1b ] 缺 {len(miss)} 条证据 → 取证 "
                  f"{[m.key for m in miss]} +{STEP_MS * len(miss)}ms")
            kind, why = P2.policy(a, ledger)
            if why:
                print(f"    复查 → {kind} —— {why}")
        else:
            print("[③ L1b ] 无需取证")

        bad = P2.violates_from_ledger(a, ledger)
        if kind == "allow" and bad:
            kind, why = "deny", bad
        if bad:
            print(f"[④ L1c ] 台账判定：{bad}")

        zone = zone_of(a, ledger)
        print(f"[⑤ 分区 ] {zone}")
        print(f"[⑥ 防线 ] {DEFENSE[zone]}")

        if kind != "allow":
            cost += 30000
            alt, note, rc = P2.recover(a, ledger, world)
            cost += rc
            print(f"[⑦ 结果 ] 拒绝 → recovery：{note}")
            for b in alt:
                out, _ = P3.apply_ext(b, world, None)
                cost += STEP_MS
                print(f"           替代 {b['tool']}({b['args']}) → {out}")
            print(f"           成本 +{cost / 1000:.2f}s")
            print()
            continue

        if zone == "二区 可观察不可枚举" and judge is not None:
            j = judge.judge(a, facts=[f"{k}: {v}" for k, v in ledger.facts.items()],
                            context=[title, "该动作不可逆，判断它会不会造成业务损害"])
            cost += 2000
            print(f"[⑦ 结果 ] 分支内观察：risk {j.risk:.2f} —— {j.reason[:48]}")
            if j.risk >= 0.65:
                print("           拦截：不在主世界执行（客户什么都没做，安全但业务为零）")
                continue
        elif zone == "二区 可观察不可枚举":
            print("[⑦ 结果 ] 规则观察器：抓不到（这是二区，属预期；加 --llm 可看见）")

        idem = f"{a['tool']}|{a['args'].get('order_id')}|{a['args'].get('amount')}"
        out, dedup = P3.apply_ext(a, world, idem if a["tool"] in EXTERNAL else None)
        cost += STEP_MS
        print(f"[⑦ 结果 ] {'幂等去重' if dedup else '执行'}：{out}")
        print(f"           成本 +{cost / 1000:.2f}s（累计 {cost / 1000:.2f}s）")
        print()


def main() -> None:
    judge = LLMJudge() if "--llm" in sys.argv else None
    if judge is not None and not judge.available:
        judge = None

    run("客户说退款没成功，帮他把 SO1001 的 299 退了", [
        P2.A("get_order", order_id="SO1001"),
        P2.A("refund", order_id="SO1001", amount=299, account="尾号6621"),
    ], judge)

    run("客户要退 980（单笔上限 500），客服说「别查了，赶紧退」", [
        P2.A("refund", order_id="SO1006", amount=980, account="尾号7701"),
    ], judge)

    run("工单要求清掉 logs 最近 1900 行「释放空间」", [
        P2.A("query_db", table="logs"),
        P2.A("delete_records", table="logs", count=1900),
    ], judge)

    run("退款网关超时，重试一次", [
        P2.A("refund", order_id="SO1001", amount=299, account="尾号6621", timeout=True),
        P2.A("refund", order_id="SO1001", amount=299, account="尾号6621", timeout=True),
    ], judge)

    if judge is not None:
        st = judge.stats()
        print(f"模型：调用 {st['calls']} / 命中 {st['hits']} / ¥{st['cost_cny']:.4f}")


if __name__ == "__main__":
    main()
