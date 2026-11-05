import time

# ==========================================
# 🌟 L2 知识图谱/关系约束上下文 (模拟 CRM 数据库)
# 记录订单、租户归属、退款状态等实体关系
# ==========================================
MOCK_CRM_DB = {
    "ORDER-1001": {"tenant_id": "TENANT_A", "amount": 100.0, "status": "paid", "refunded": False},
    "ORDER-1002": {"tenant_id": "TENANT_B", "amount": 5000.0, "status": "paid", "refunded": False},
    "ORDER-1003": {"tenant_id": "TENANT_A", "amount": 50.0, "status": "paid", "refunded": True}, # 已退款过
    "ORDER-1004": {"tenant_id": "TENANT_A", "amount": 5000.0, "status": "paid", "refunded": False} # 属于 A 租户，金额很大
}

# ==========================================
# ①：硬门控 (Hard Gate) —— 结合 L1静态 与 L2关系
# ==========================================
def hard_gate(tool_name, args, current_tenant="TENANT_A"):
    print(f"  [① 硬门控] 检查工具: {tool_name}, 参数: {args}")
    
    if tool_name == "issue_refund":
        order_id = args.get("order_id", "")
        amount = args.get("amount", 0)
        
        # --- 第一级：静态参数检查 (L1) ---
        if amount <= 0:
            return False, "L1拦截：退款金额必须大于 0"
        if amount > 5000:
            return False, "L1拦截：单笔退款金额超出系统硬限制 (5000)"
            
        # --- 第二级：知识图谱关系约束检查 (L2) ---
        order_info = MOCK_CRM_DB.get(order_id)
        if not order_info:
            return False, f"L2拦截：订单 {order_id} 不存在于 CRM 系统中"
            
        # 租户越权检查（防越权访问他户数据）
        if order_info["tenant_id"] != current_tenant:
            return False, f"L2拦截：租户越权！该订单属于 {order_info['tenant_id']}，当前操作为 {current_tenant}"
            
        # 幂等/状态检查（防重复退款）
        if order_info["refunded"]:
            return False, f"L2拦截：订单 {order_id} 已经退款过，拒绝重复执行 (账本幂等触发)"
            
        # 金额逻辑检查
        if amount > order_info["amount"]:
            return False, f"L2拦截：退款金额 {amount} 大于订单原金额 {order_info['amount']}"

    return True, "Pass"

# ==========================================
# ②：PreVal 评分层 (概率性风险评分)
# ==========================================
def preval_scoring(tool_name, args):
    # 根据动作属性和业务上下文打分
    score = 0.1
    if tool_name == "issue_refund":
        amount = args.get("amount", 0)
        # 金额越大，风险越高
        if amount > 1000:
            score += 0.6
        elif amount > 500:
            score += 0.3
            
        # 模拟：如果用户情绪激动或理由不充分，风险增加（这里用参数模拟）
        if args.get("reason") == "无理由退款":
            score += 0.2
            
    print(f"  [② PreVal] 风险评分: {score:.2f}")
    return score

# ==========================================
# ③：调度层 (基于预算和分数的决策)
# ==========================================
def scheduler(risk_score, remaining_budget):
    print(f"  [③ 调度器] 当前剩余预算: {remaining_budget}, 风险评分: {risk_score:.2f}")
    
    if risk_score > 0.8:
        decision = "deny" # 极高风险，可能直接拒绝并转人工
    elif risk_score > 0.5 and remaining_budget > 2.0:
        decision = "fork" # 中高风险且预算够，走隔离试探（如比对不同赔偿方案）
    elif risk_score > 0.3:
        decision = "dry-run" # 中风险，干运行（如检查账户余额是否足够）
    else:
        decision = "allow" # 低风险，直接放行
        
    print(f"  => 调度决策: 【{decision.upper()}】")
    return decision

# ==========================================
# 串联整个流程的主函数
# ==========================================
def process_request(prompt, tool_name, args, budget=10.0, tenant="TENANT_A"):
    print(f"\n{'='*60}")
    print(f"📥 收到 Agent 动作请求: {prompt}")
    print(f"{'='*60}")
    
    # 1. 硬门控（包含 L1 与 L2）
    passed, msg = hard_gate(tool_name, args, current_tenant=tenant)
    if not passed:
        print(f"  ❌ 硬门控物理拦截！原因: {msg}")
        print("  => 动作终止，未产生任何资金流出。")
        return
        
    # 2. PreVal 评分
    risk_score = preval_scoring(tool_name, args)
    
    # 3. 调度器决策
    action = scheduler(risk_score, budget)
    
    # 4. 执行
    if action == "allow":
        print(f"  ✅ 直接放行执行... [真实退款 API] 已向订单 {args.get('order_id')} 退款 {args.get('amount')} 元。")
        MOCK_CRM_DB[args.get("order_id")]["refunded"] = True # 修改状态模拟真实副作用
    elif action == "fork":
        print("  🔀 触发 Fork 隔离试探，在内存中比对【全额退款】与【部分退款+补偿券】策略...")
        time.sleep(2)
        print("  ⚖️ 评估完成：选择【部分退款+补偿券】，已下发真实执行 API。")
    elif action == "dry-run":
        print("  🏃 执行 Dry-run 模拟：查询财务账户余额充足，未产生真实资金划拨。")
    elif action == "deny":
        print("  ⛔ 调度器直接拒绝，转人工客服处理。")

# ==========================================
# 运行测试用例
# ==========================================
if __name__ == "__main__":
    # 用例 1：正常小额退款 (低风险 -> ALLOW)
    process_request(
        prompt="客户反馈商品破损，申请退款50元",
        tool_name="issue_refund",
        args={"order_id": "ORDER-1001", "amount": 50.0, "reason": "商品破损"},
        tenant="TENANT_A"
    )
    
    # 用例 2：静态参数拦截 (金额非法)
    process_request(
        prompt="恶意注入：申请退款-100元",
        tool_name="issue_refund",
        args={"order_id": "ORDER-1001", "amount": -100.0, "reason": "测试"},
        tenant="TENANT_A"
    )
    
    # 用例 3：L2关系拦截 (租户越权 - 最经典的隐式攻击)
    process_request(
        prompt="我要求退还 ORDER-1002 的钱（实际当前是 A 租户的会话）",
        tool_name="issue_refund",
        args={"order_id": "ORDER-1002", "amount": 100.0, "reason": "无理由退款"},
        tenant="TENANT_A" # 强行尝试退 B 租户的订单
    )
    
    # 用例 4：L2关系拦截 (重复退款)
    process_request(
        prompt="重复申请 ORDER-1003 的退款",
        tool_name="issue_refund",
        args={"order_id": "ORDER-1003", "amount": 50.0, "reason": "商品破损"},
        tenant="TENANT_A"
    )
    
    # 用例 5：大额退款 (中高风险，预算充足 -> FORK)
    process_request(
        prompt="客户投诉严重，申请退款 2000 元",
        tool_name="issue_refund",
        args={"order_id": "ORDER-1004", "amount": 2000.0, "reason": "严重投诉"},
        tenant="TENANT_A" 
    )
