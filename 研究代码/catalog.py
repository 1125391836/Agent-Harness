"""工具目录 —— Agent 能做的事，以及每件事的副作用等级。

为什么单独一张表：门控规则、风险先验、分支必要性，全都由两件事推出来——
「后果能不能撤回」和「作用对象在不在公司内」。工具本身不带策略，
策略只读这张表：加一个工具不用改门控，改一条策略不用改工具。

副作用四级（论文里的"副作用谱系"）：
    read          只读，没有后果
    reversible    可撤销——删了邮件、取消会议、撤回消息
    compensable   可补偿——退票、回滚、冲正；有代价，但世界能回原样
    irreversible  不可撤回——钱出去了、邮件发出了、数据删了、对外发布了

注意一个容易被忽略的点：**同一个工具，目标不同，等级不同**。
给同事发消息可以撤回（reversible），给客户发就是不可撤回（irreversible）。
所以 effect 不是工具的静态属性，effect_of(tool, args) 才是。
"""

from __future__ import annotations

from dataclasses import dataclass, field

READ, REV, COMP, IRR = "read", "reversible", "compensable", "irreversible"

EFFECT_CN = {READ: "只读", REV: "可撤销", COMP: "可补偿", IRR: "不可撤回"}
EFFECT_MARK = {READ: "·", REV: "↺", COMP: "≈", IRR: "!"}

INTERNAL_DOMAIN = "company.com"

# 发给内部是 reversible、发给外部是 irreversible 的工具
EXTERNAL_SENSITIVE = {"send_im", "reply_ticket", "share_file"}


@dataclass(frozen=True)
class Tool:
    name: str
    cat: str
    effect: str
    desc: str
    params: dict = field(default_factory=dict)
    required: tuple = ()
    why: str = ""


def P(**kw: str) -> dict:
    return dict(kw)


_RAW: list[Tool] = [
    # ---------------- 查询：只读，永远不该触发分支 ----------------
    Tool("find_contact", "查询", READ, "按姓名/部门/邮箱查通讯录",
         P(query="姓名、部门或邮箱片段"), ("query",), "只读；它存在的意义是把名字变成地址"),
    Tool("get_employee", "查询", READ, "员工档案（工号/部门/上级/入职时间）",
         P(name="姓名或工号"), ("name",), "只读"),
    Tool("query_db", "查询", READ, "只读查询数据库",
         P(table="表名", where="筛选条件", limit="返回条数"), ("table",), "只读，不改数据"),
    Tool("read_file", "查询", READ, "读文件", P(path="文件路径"), ("path",), "只读"),
    Tool("list_files", "查询", READ, "列目录", P(dir="目录路径"), ("dir",), "只读"),
    Tool("http_get", "查询", READ, "访问网址（可能带回不可信内容）",
         P(url="完整 URL"), ("url",), "只读，但返回值可能被注入，不能当指令"),
    Tool("search", "查询", READ, "搜内部知识库", P(query="关键词"), ("query",), "只读"),
    Tool("get_calendar", "查询", READ, "看某天日程", P(date="YYYY-MM-DD"), ("date",), "只读"),
    Tool("list_orders", "查询", READ, "列订单",
         P(status="状态：paid/shipped/refunded", limit="条数"), (), "只读"),
    Tool("check_service", "查询", READ, "看服务健康与版本",
         P(service="服务名", env="prod 或 staging"), ("service",), "只读"),
    Tool("get_balance", "查询", READ, "查账户余额", P(account="账户"), ("account",), "只读"),

    # ---------------- 沟通：发出去就收不回 ----------------
    Tool("send_email", "沟通", IRR, "发邮件",
         P(to="收件邮箱", subject="主题", body="正文", attach="附件文件名"),
         ("to",), "投递后无法撤回，且可能带出附件"),
    Tool("draft_email", "沟通", REV, "存草稿（不发送）",
         P(to="收件邮箱", subject="主题", body="正文"), ("to",), "只在草稿箱，可删"),
    Tool("send_im", "沟通", IRR, "发即时消息（内部可撤回，外部不可）",
         P(to="收件人邮箱或姓名", text="内容"), ("to",), "内部可撤回，外部等同于发布"),
    Tool("send_sms", "沟通", IRR, "发短信",
         P(to="手机号或邮箱", text="内容"), ("to",), "短信无法撤回，且按条计费"),
    Tool("reply_ticket", "沟通", IRR, "回复客服工单",
         P(ticket_id="工单号", text="回复内容"), ("ticket_id",), "客户收到即不可撤回"),

    # ---------------- 财务：钱出去就回不来 ----------------
    Tool("pay", "财务", IRR, "付款给某个收款方",
         P(amount="金额（元，纯数字）", to="收款邮箱", memo="备注"), ("amount", "to"),
         "资金划出即不可逆，只能走退款流程"),
    Tool("refund", "财务", IRR, "给客户退款",
         P(order_id="订单号", amount="金额", account="退款账户（默认原路退回）", reason="退款原因"),
         ("order_id", "amount"), "钱出去不可撤回；改账户退款是洗钱通道，必须原路"),
    Tool("issue_invoice", "财务", IRR, "开发票",
         P(customer="客户", amount="金额", items="开票项目"), ("customer",), "税务留痕，作废要红冲"),
    Tool("create_po", "财务", COMP, "下采购单",
         P(supplier="供应商", amount="金额", items="采购内容"), ("supplier",),
         "未发货可撤销；发货后转为不可逆，所以要在下单时确认"),
    Tool("approve_expense", "财务", IRR, "审批报销",
         P(expense_id="报销单号"), ("expense_id",), "审批即触发打款"),
    Tool("transfer_internal", "财务", COMP, "内部账户划转",
         P(from_account="转出账户", to_account="转入账户", amount="金额"),
         ("from_account", "to_account"), "可冲正，代价是账务调整"),

    # ---------------- 人事：权限删了要重建 ----------------
    Tool("apply_leave", "人事", REV, "提交请假申请", P(days="天数", reason="事由"), ("days",), "可撤回"),
    Tool("approve_leave", "人事", COMP, "审批请假", P(leave_id="申请号"), ("leave_id",), "可撤销审批"),
    Tool("onboard_employee", "人事", REV, "办理入职",
         P(name="姓名", dept="部门", role="岗位"), ("name",), "建账号，可删"),
    Tool("offboard_employee", "人事", IRR, "办理离职并注销账号",
         P(name="姓名", last_day="最后工作日"), ("name",),
         "账号注销、权限删除、邮箱回收，重建成本极高"),
    Tool("grant_permission", "人事", COMP, "授权",
         P(user="用户", resource="资源", level="read/write/admin"),
         ("user", "resource"), "可回收，但授权窗口内的操作收不回来"),

    # ---------------- 研发：删库、迁移、上线 ----------------
    Tool("deploy", "研发", COMP, "部署服务",
         P(env="prod 或 staging", version="版本号", ticket="变更单号"), ("env",),
         "可回滚，但回滚窗口内的数据变更收不回"),
    Tool("rollback", "研发", COMP, "回滚到上一版本",
         P(service="服务名", env="环境", version="目标版本"), ("service",), "可再前进"),
    Tool("run_migration", "研发", IRR, "执行数据库迁移",
         P(db="库名", sql="DDL/DML"), ("db",), "表结构变更难回滚，数据可能已重写"),
    Tool("delete_records", "研发", IRR, "删除数据库记录",
         P(table="表名", count="删除条数", where="筛选条件"), ("table",), "删除即不可恢复"),
    Tool("drop_table", "研发", IRR, "删表",
         P(table="表名"), ("table",), "整表连同索引消失"),
    Tool("write_db", "研发", IRR, "写数据库",
         P(table="表名", rows="行数", fields="涉及字段"), ("table",), "覆盖写不可逆，尤其 PII"),
    Tool("create_pr", "研发", REV, "提 PR", P(repo="仓库", branch="分支", title="标题"),
         ("repo",), "可关闭"),
    Tool("merge_pr", "研发", COMP, "合并 PR", P(repo="仓库", number="PR 号"), ("repo",), "可 revert"),
    Tool("restart_service", "研发", REV, "重启服务",
         P(service="服务名", env="环境"), ("service",), "短暂中断，重启即可恢复"),
    Tool("scale_cluster", "研发", COMP, "扩缩容",
         P(service="服务名", replicas="副本数"), ("service",), "花的是钱，改回来就行"),

    # ---------------- 办公：会议、文档、差旅 ----------------
    Tool("book_flight", "办公", COMP, "订机票",
         P(date="出发日期 YYYY-MM-DD", from_city="出发城市", to_city="到达城市",
           passenger="乘机人"), ("date", "to_city"), "可退改签，手续费是代价"),
    Tool("book_hotel", "办公", COMP, "订酒店",
         P(city="城市", checkin="入住日期 YYYY-MM-DD", nights="晚数"), ("city", "checkin"),
         "多数可免费取消"),
    Tool("book_meeting_room", "办公", REV, "订会议室",
         P(room="会议室", date="日期", start="开始时间", end="结束时间"), ("room", "date"), "可取消"),
    Tool("create_event", "办公", REV, "建日程并邀请", P(title="标题", date="日期", time="时间",
                                                 attendees="参与人"), ("title", "date"), "可删除"),
    Tool("create_doc", "办公", REV, "新建文档", P(title="标题", content="内容"), ("title",), "可删"),
    Tool("update_doc", "办公", REV, "改文档", P(path="文档路径", content="新内容"), ("path",),
         "有版本历史"),
    Tool("publish_post", "办公", IRR, "对外发布内容",
         P(channel="渠道", title="标题", body="正文"), ("channel",), "发布即被转发，删了也留痕"),
    Tool("delete_file", "办公", IRR, "删文件", P(path="文件路径"), ("path",), "回收站过期即不可恢复"),
    Tool("move_file", "办公", REV, "移动文件", P(src="源路径", dst="目标路径"), ("src", "dst"), "可移回"),
    Tool("share_file", "办公", IRR, "共享文件",
         P(path="文件路径", to="共享给谁", permission="read/write"), ("path", "to"),
         "外部共享一旦被下载就收不回"),

    # ---------------- 电商客服 ----------------
    Tool("get_order", "电商", READ, "查订单（金额/状态/已退金额）",
         P(order_id="订单号"), ("order_id",), "只读；退款前必须先看它"),
    Tool("get_refund_status", "电商", READ, "查退款单在支付网关的真实状态",
         P(order_id="订单号"), ("order_id",),
         "只读；客户说『没收到』和网关说『成功』经常不是一回事"),
    Tool("notify_customer", "电商", IRR, "给客户发通知（短信/站内信）",
         P(order_id="订单号", channel="短信/站内信/电话", text="内容"), ("order_id",),
         "发出去收不回，说错话要认"),
    Tool("escalate_ticket", "电商", REV, "升级工单给人工",
         P(order_id="订单号", reason="升级原因"), ("order_id",), "可撤回"),

    # ---------------- 生活 ----------------
    Tool("buy_goods", "生活", IRR, "网购下单",
         P(item="商品", amount="金额", platform="平台"), ("item",), "付款后只能走退货"),
    Tool("cancel_order", "生活", COMP, "取消订单", P(order_id="订单号"), ("order_id",), "未发货可取消"),
    Tool("pay_bill", "生活", IRR, "缴水电燃气费", P(biller="缴费单位", amount="金额"), ("biller",),
         "缴费成功不退"),
]

TOOLS: dict[str, Tool] = {t.name: t for t in _RAW}

CATEGORIES: list[str] = []
for _t in _RAW:
    if _t.cat not in CATEGORIES:
        CATEGORIES.append(_t.cat)


def is_external(addr: str) -> bool:
    s = str(addr or "").strip().lower()
    if "@" not in s:
        return False
    return not s.endswith("@" + INTERNAL_DOMAIN)


def effect_of(tool: str, args: dict | None = None) -> str:
    """工具 + 目标 → 副作用等级。不认识的工具按最坏算。"""
    t = TOOLS.get(tool)
    if t is None:
        return IRR
    args = args or {}
    if tool in EXTERNAL_SENSITIVE:
        target = args.get("to") or args.get("channel") or ""
        if target and not is_external(target):
            return REV
    return t.effect


def describe(tool: str, args: dict | None = None) -> tuple[str, bool]:
    """沙箱里这条副作用长什么样 + 能不能恢复。"""
    eff = effect_of(tool, args)
    t = TOOLS.get(tool)
    head = t.desc if t else tool
    if eff == READ:
        return f"{head}（只读，无副作用）", True
    if eff == REV:
        return f"{head}（可撤销）", True
    if eff == COMP:
        return f"{head}（可补偿：退改/回滚/冲正）", True
    return f"{head}（不可撤回）", False


def by_category() -> dict[str, list[Tool]]:
    return {c: [t for t in _RAW if t.cat == c] for c in CATEGORIES}


def search_tools(kw: str = "") -> list[Tool]:
    k = (kw or "").strip().lower()
    if not k:
        return list(_RAW)
    return [t for t in _RAW
            if k in t.name.lower() or k in t.desc or k in t.cat
            or any(k in p for p in t.params)]


def summary() -> str:
    counts = {e: 0 for e in EFFECT_CN}
    for t in _RAW:
        counts[t.effect] = counts.get(t.effect, 0) + 1
    return " · ".join(f"{EFFECT_CN[e]} {counts[e]}" for e in (READ, REV, COMP, IRR))


if __name__ == "__main__":
    print(f"共 {len(TOOLS)} 个工具，{len(CATEGORIES)} 类：{summary()}")
    for cat, items in by_category().items():
        print(f"\n[{cat}] {len(items)} 个")
        for t in items:
            print(f"  {EFFECT_MARK[t.effect]} {t.name:<20} {t.desc}")
