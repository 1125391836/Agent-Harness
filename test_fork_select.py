from summary_agent_graph1 import app, llm  # 注意这里改成了 graph1
from secure_tools import write_summary

if __name__ == "__main__":
    config = {"configurable": {"thread_id": "fork_select_01"}}
    long_text = "人工智能（AI）是当前科技领域最热门的话题。" * 150
    
    print("=== 第一步：主分支运行到 Reduce 结束（不写文件） ===")
    final_state = app.invoke({
        "raw_text": long_text
    }, config=config)
    
    history = list(app.get_state_history(config))
    
    # 找到 reduce 执行完之后的状态
    target_checkpoint = None
    for state in history:
        # 注意：metadata 的获取方式取决于版本，如果不生效，可以直接找 next 是空的状态
        if state.metadata.get("langgraph_node") == "reduce":
            target_checkpoint = state
            break
            
    # 兜底：如果没找到 metadata，就找历史里最早的那个
    if not target_checkpoint:
        print("未找到 reduce 节点的 metadata，尝试取最新状态的前一个状态...")
        target_checkpoint = history[0] 

    print(f"找到 Reduce 后的存档点。当前摘要长度: {len(target_checkpoint.values['final_summary'])}")

    print("\n=== 第二步：Fork 出两条内存分支，试探不同策略 ===")
    
    # 分支 A：学术风格
    print("正在生成 Fork 分支 A (学术风格)...")
    resp_a = llm.invoke(f"请用学术论文的口吻，重写以下摘要：\n{target_checkpoint.values['final_summary']}")
    summary_a = resp_a.content

    # 分支 B：通俗风格
    print("正在生成 Fork 分支 B (通俗风格)...")
    resp_b = llm.invoke(f"请用通俗易懂的儿童绘本风格，重写以下摘要：\n{target_checkpoint.values['final_summary']}")
    summary_b = resp_b.content

    print(f"\n分支 A (学术): {summary_a[:50]}...")
    print(f"\n分支 B (通俗): {summary_b[:50]}...")

    print("\n=== 第三步：调度器评估与择优 ===")
    # 这里简单模拟选择 B 作为胜者
    winning_summary = summary_b
    print("调度器决定：选择分支 B 作为最终执行结果。")

    print("\n=== 第四步：胜出分支执行真实不可逆副作用 ===")
    # 触发硬门控工具，产生真实文件
    result = write_summary.invoke({
        "content": winning_summary,
        "output_path": "./outputs/final_winner.md"
    })
    print(f"[门控返回] {result}")
