import time

# ==========================================
# 模拟 ①：硬门控 (Hard Gate)
# ==========================================
def hard_gate(tool_name, args):
    print(f"  [① 硬门控] 检查工具: {tool_name}, 参数: {args}")
    if tool_name == "write_summary":
        output_path = args.get("output_path", "")
        # 静态路径检查
        if not output_path.startswith("./outputs/"):
            return False, "路径越权：不在允许的 outputs 目录内"
        # 后缀检查
        if not output_path.endswith((".md", ".txt")):
            return False, "类型越权：只允许写入 .md 或 .txt"
    return True, "Pass"

# ==========================================
# 模拟 ②：PreVal 评分层 (概率性风险评分)
# ==========================================
def preval_scoring(tool_name, args):
    # 模拟一个不完美的评分器：根据路径深度和操作类型给分
    score = 0.1
    output_path = args.get("output_path", "")
    
    if "tmp" in output_path:
        score += 0.4 # 写临时目录，感觉有点可疑
    if "delete" in tool_name:
        score += 0.8 # 删除操作，风险极高
    
    print(f"  [② PreVal] 风险评分: {score:.2f}")
    return score

# ==========================================
# 模拟 ③：调度层 (基于预算和分数的决策)
# ==========================================
def scheduler(risk_score, remaining_budget):
    print(f"  [③ 调度器] 当前剩余预算: {remaining_budget}, 风险评分: {risk_score:.2f}")
    
    if risk_score > 0.8:
        decision = "deny"
    elif risk_score > 0.5 and remaining_budget > 2.0:
        decision = "fork" # 高风险且预算够，走隔离试探
    elif risk_score > 0.3:
        decision = "dry-run" # 中风险，干运行
    else:
        decision = "allow" # 低风险，直接放行
        
    print(f"  => 调度决策: 【{decision.upper()}】")
    return decision

# ==========================================
# 串联整个流程的主函数
# ==========================================
def process_request(prompt, tool_name, args, budget=10.0):
    print(f"\n{'='*60}")
    print(f"📥 收到 Agent 动作请求: {prompt}")
    print(f"{'='*60}")
    
    # 1. 硬门控
    passed, msg = hard_gate(tool_name, args)
    if not passed:
        print(f"  ❌ 硬门控物理拦截！原因: {msg}")
        print("  => 动作终止，未产生任何物理副作用。")
        return
        
    # 2. PreVal
    risk_score = preval_scoring(tool_name, args)
    
    # 3. 调度器
    action = scheduler(risk_score, budget)
    
    # 4. 执行
    if action == "allow":
        print("  ✅ 直接放行执行...")
    elif action == "fork":
        print("  🔀 触发 Fork 隔离试探，在内存中生成多条路径比对... (耗时 2 秒)")
        time.sleep(2)
    elif action == "dry-run":
        print("  🏃 执行 Dry-run 模拟，不产生真实副作用...")
    elif action == "deny":
        print("  ⛔ 调度器直接拒绝，拦截动作。")

# ==========================================
# 运行测试用例 (这就是你之前缺失的执行入口)
# ==========================================
if __name__ == "__main__":
    # 用例 1：正常低风险请求 (应该被 Allow)
    process_request(
        prompt="保存正常摘要",
        tool_name="write_summary",
        args={"output_path": "./outputs/summary.md"}
    )
    
    # 用例 2：路径越权请求 (应该被硬门控直接拦截)
    process_request(
        prompt="尝试写入系统文件",
        tool_name="write_summary",
        args={"output_path": "../../etc/passwd"}
    )
    
    # 用例 3：中风险请求 (比如写到临时目录，应该走 Dry-run)
    process_request(
        prompt="保存到临时目录",
        tool_name="write_summary",
        args={"output_path": "./outputs/tmp_test.md"}
    )
    
    # 用例 4：高风险操作 (模拟删除文件，预算充足应该走 Fork)
    process_request(
        prompt="清理旧文件",
        tool_name="delete_file",
        args={"file_path": "./outputs/old.md"}
    )
    
    # 用例 5：高风险且预算不足 (应该被 Deny)
    process_request(
        prompt="清理旧文件",
        tool_name="delete_file",
        args={"file_path": "./outputs/old.md"},
        budget=1.0 # 预算不足
    )