from summary_agent_graph import app  # 导入你刚才写好的图和 memory
import json

if __name__ == "__main__":
    # 定义会话 ID
    config = {"configurable": {"thread_id": "fork_experiment_01"}}

    print("=== 第一步：先正常跑一遍完整的图 ===")
    long_text = "人工智能（AI）是当前科技领域最热门的话题。" * 150
    
    # 运行一次，让图跑到底，生成所有 Checkpoint
    app.invoke({
        "raw_text": long_text,
        "output_path": "./outputs/main_branch.md"
    }, config=config)
    
    print("\n=== 第二步：查看历史存档 (Checkpoints) ===")
    # 获取所有历史状态（从最新到最旧）
    history = list(app.get_state_history(config))
    print(f"一共找到了 {len(history)} 个历史存档。")
    
    # 找到“准备写文件”之前的那个状态
    # 也就是当前节点执行完毕，下一个节点是 'write' 的那个状态
    target_checkpoint = None
    for state in history:
        # state.next 是一个元组，代表下一步要执行的节点
        if "write" in state.next:
            target_checkpoint = state
            break
            
    if not target_checkpoint:
        print("没找到写入前的状态，请检查图结构！")
        exit()

    print(f"\n找到目标存档点 ID: {target_checkpoint.config['configurable']['checkpoint_id']}")
    print(f"当前原分支的最终摘要: {target_checkpoint.values.get('final_summary')[:50]}...")

    print("\n=== 第三步：使用 update_state 进行 Fork（分叉） ===")
    # 我们在这个存档点上，强行修改状态！把摘要改掉，输出路径也改掉。
    # 这相当于在原有的时间线上，长出了一条新的平行时空。
    fork_config = app.update_state(
        target_checkpoint.config, # 定位到这个特定的存档
        {
            "final_summary": "【FORK分支的测试摘要】AI的发展十分迅速，未来可期。", 
            "output_path": "./outputs/fork_branch.md" # 换一个文件写，避免和主分支冲突
        }
    )
    
    print(f"Fork 成功！新分支的 config: {fork_config}")

    print("\n=== 第四步：在新分支上继续执行 ===")
    # 从刚刚修改的状态继续往下跑，此时它只会执行 'write' 节点
    # 注意：这里传入 None 作为输入，因为它会从 update_state 保存的地方接着走
    fork_result = app.invoke(None, config=fork_config)
    
    print("\n=== 实验结束 ===")
    print("现在去 outputs 目录下看看，是不是多了一个 fork_branch.md，并且里面的内容是你刚才强制修改的那句话？")
