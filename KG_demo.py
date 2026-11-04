import time

# ==========================================
# 🌟 新增：模拟知识图谱 (Knowledge Graph)
# 在实际工程中，这里会连接 Neo4j 或 NebulaGraph
# 它记录了文件、项目、审批状态等实体之间的关系
# ==========================================
MOCK_KG = {
    "./outputs/finance_report.md": {
        "entity_type": "SensitiveFile",
        "project": "Finance_Q3",
        "status": "pending_approval",  # 未审批状态
        "risk_level": "HIGH"
    },
    "./outputs/project_alpha.md": {
        "entity_type": "NormalFile",
        "project": "Alpha",
        "status": "active",
        "risk_level": "LOW"
    }
}

def kg_lookup(file_path):
    """模拟知识图谱查询：输入路径，返回关联的业务实体上下文"""
    return MOCK_KG.get(file_path, {})

# ==========================================
# ①：硬门控 (Hard Gate) —— 升级为双层防御
# ==========================================
def hard_gate(tool_name, args):
    print(f"  [① 硬门控] 检查工具: {tool_name}, 参数: {args}")
    if tool_name == "write_summary":
        output_path = args.get("output_path", "")
        
        # --- 第一级：静态语法检查 (毫秒级) ---
        if not output_path.startswith("./outputs/"):
            return False, "静态拦截：路径越权，不在允许的 outputs 目录内"
        if not output_path.endswith((".md", ".txt")):
            return False, "静态拦截：类型越权，只允许写入 .md 或 .txt"
            
        # --- 第二级：知识图谱上下文检查 (关系型规则) ---
        kg_context = kg_lookup(output_path)
        if kg_context:
            print(f"  [① 硬门控-KG增强] 命中图谱实体: {kg_context['entity_type']} | 所属项目: {kg_context['project']}")
            # 如果 KG 显示该文件属于“未审批”状态，直接物理否决
            if kg_context.get("status") == "pending_approval":
                return False, f"KG规则拦截：目标文件属于未审批项目 {kg_context['project']}，禁止写入。"
            # 你还可以基于图谱做更复杂的判定，比如：
            if kg_context.get("risk_level") == "HIGH" and "admin" not in args.get("user", ""):
                return False, "KG规则拦截：高危实体要求 admin 权限。"

    return True, "Pass"

# ==========================================
# ②：PreVal 评分层 (概率性风险评分)
# ==========================================
def preval_scoring(tool_name, args):
    # 如果动作通过了 KG 门控，说明基础上下文是安全的
    # PreVal 只需基于动作本身的属性打分
    score = 0.1
    output_path = args.get("output_path", "")
    
    if "tmp" in output_path:
        score += 0.4 
    if "delete" in tool_name:
        score += 0.8 
    
    print(f"  [② PreVal] 风险评分: {score:.2f}")
    return score

# ==========================================
# ③：调度层 (基于预算和分数的决策)
# ==========================================
def scheduler(risk_score, remaining_budget):
    print(f"  [③ 调度器] 当前剩余预算: {remaining_budget}, 风险评分: {risk_score:.2f}")
    
    if risk_score > 0.8:
        decision = "deny"
    elif risk_score > 0.5 and remaining_budget > 2.0:
        decision = "fork" 
    elif risk_score > 0.3:
        decision = "dry-run" 
    else:
        decision = "allow" 
        
    print(f"  => 调度决策: 【{decision.upper()}】")
    return decision

# ==========================================
# 串联整个流程的主函数
# ==========================================
def process_request(prompt, tool_name, args, budget=10.0):
    print(f"\n{'='*60}")
    print(f"📥 收到 Agent 动作请求: {prompt}")
    print(f"{'='*60}")
    
    passed, msg = hard_gate(tool_name, args)
    if not passed:
        print(f"  ❌ 硬门控物理拦截！原因: {msg}")
        print("  => 动作终止，未产生任何物理副作用。")
        return
        
    risk_score = preval_scoring(tool_name, args)
    action = scheduler(risk_score, budget)
    
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
# 运行测试用例
# ==========================================
if __name__ == "__main__":
    # 用例 1：正常写入（KG中为 active 状态，通过）
    process_request("保存正常摘要", "write_summary", {"output_path": "./outputs/project_alpha.md"})
    
    # 用例 2：越权路径（静态拦截）
    process_request("尝试写入系统文件", "write_summary", {"output_path": "../../etc/passwd"})
    
    # 🌟 用例 3：高隐蔽性越权（通过静态检查，被 KG 拦截！）
    process_request("更新财务报告", "write_summary", {"output_path": "./outputs/finance_report.md"})
    
    # 用例 4：中风险（通过硬门控，走 Dry-run）
    process_request("保存到临时目录", "write_summary", {"output_path": "./outputs/tmp_test.md"})
    
    # 用例 5：高风险删除（调度器 Deny）
    process_request("清理旧文件", "delete_file", {"file_path": "./outputs/old.md"})
