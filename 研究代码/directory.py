"""通讯录与账户目录：把"小明""公司账户"落到真实地址上。

解析层最容易被忽略的一步。模型把"发给小明"翻成 `to='小明'`，
门控和裁判拿到的是一个没有域的名字，只能判"未知收款方"——
可真实系统里这些名字是能查到的，查不到才该是未知。

所以这里单独一层：**先消歧，再判定**。消歧失败才把不确定性传给下一层。
"""

from __future__ import annotations

# 名字 → (邮箱, 身份说明)
CONTACTS: dict[str, tuple[str, str]] = {
    "小明": ("xiaoming@company.com", "同事／产品"),
    "张伟": ("zhangwei@company.com", "同事／后端"),
    "李娜": ("lina@company.com", "同事／财务"),
    "王强": ("wangqiang@company.com", "同事／测试"),
    "财务": ("finance@company.com", "财务组"),
    "财务部": ("finance@company.com", "财务组"),
    "公司账户": ("finance@company.com", "对公收款账户"),
    "公司账号": ("finance@company.com", "对公收款账户"),
    "公司帐号": ("finance@company.com", "对公收款账户"),
    "acme": ("billing@acme.com", "供应商"),
    "partner": ("billing@partner.com", "供应商"),
}

# 模型可能给出带修饰的说法，先归一
_STRIP = ("的", "那个", "这个", "一下", "账户", "账号", "帐号")


def resolve_contact(name: str) -> tuple[str, str] | None:
    """名字 → (邮箱, 身份)。已经是邮箱就原样返回；查不到返回 None。"""
    s = (name or "").strip().strip("，。、 ")
    if not s:
        return None
    if "@" in s:
        return s, "直接给了邮箱"

    if s in CONTACTS:
        return CONTACTS[s]

    for k, v in CONTACTS.items():
        if k and (k in s or (len(s) >= 2 and s in k)):
            return v

    t = s
    for w in _STRIP:
        t = t.replace(w, "")
    if t != s:
        return resolve_contact(t)
    return None


if __name__ == "__main__":
    for q in ["小明", "公司账户", "给小明", "财务部", "张三", "billing@acme.com", ""]:
        print(f"{q!r:<16} → {resolve_contact(q)}")

# ---------------------------------------------------------------------
# 可搜索的花名册：以前只能靠 CONTACTS 精确命中，用户问"查一下张"就没办法了。
# 真实系统里这是 LDAP / HR 系统的一次查询；这里用一张表模拟同样的接口。
# ---------------------------------------------------------------------
ROSTER: list[tuple[str, str, str, str]] = [   # (姓名, 邮箱, 职位, 部门)
    ("小明", "xiaoming@company.com", "产品经理", "产品部"),
    ("张伟", "zhangwei@company.com", "后端工程师", "研发部"),
    ("张敏", "zhangmin@company.com", "数据分析师", "数据部"),
    ("李娜", "lina@company.com", "财务专员", "财务部"),
    ("李强", "liqiang@company.com", "销售经理", "销售部"),
    ("王强", "wangqiang@company.com", "测试工程师", "研发部"),
    ("王芳", "wangfang@company.com", "HRBP", "人力资源部"),
    ("赵磊", "zhaolei@company.com", "销售总监", "销售部"),
    ("陈静", "chenjing@company.com", "法务顾问", "法务部"),
    ("刘洋", "liuyang@company.com", "运维工程师", "基础架构部"),
    ("孙悦", "sunyue@company.com", "前端工程师", "研发部"),
    ("周涛", "zhoutao@company.com", "采购主管", "采购部"),
    ("吴倩", "wuqian@company.com", "客服主管", "客服部"),
    ("郑凯", "zhengkai@company.com", "CTO", "管理层"),
    ("财务组", "finance@company.com", "对公收款/报销", "财务部"),
    ("IT 支持", "it-support@company.com", "账号与权限", "基础架构部"),
    ("客服组", "support@company.com", "对外工单", "客服部"),
    ("采购组", "procurement@company.com", "供应商对接", "采购部"),
]


def find_contact(query: str, limit: int = 8) -> list[dict]:
    """按姓名 / 邮箱 / 职位 / 部门模糊查人。查不到返回空列表（不瞎猜）。"""
    q = (query or "").strip().lower().strip("，。、 ")
    if not q:
        return []
    hits: list[dict] = []
    for name, mail, role, dept in ROSTER:
        hay = f"{name} {mail} {role} {dept}".lower()
        if q in hay:
            hits.append({"name": name, "email": mail, "role": role, "dept": dept})
    return hits[:limit]
