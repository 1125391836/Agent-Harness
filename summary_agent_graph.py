from typing import TypedDict, List
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI
from secure_tools import write_summary

# --------------------------
# 1. 初始化本地大模型
# --------------------------
# 对接本地兼容OpenAI接口的服务（ollama / llama.cpp等）
llm = ChatOpenAI(
    model="qwen-local",               # 模型别名，和后端启动--alias保持一致
    base_url="http://127.0.0.1:8080/v1", # 本地推理服务地址
    api_key="dummy",                 # 本地服务不需要真实key，随便填占位
    temperature=0                     # 温度0，输出稳定、随机性最低，适合摘要
)

# --------------------------
# 2. 定义图的状态 State
# TypedDict：约定整个工作流里共享的数据结构
# 所有节点读取/修改的公共数据都在这里
# --------------------------
class SummaryState(TypedDict):
    raw_text: str          # 原始输入长文本
    chunks: List[str]      # 切分之后的文本块列表
    current_index: int     # 当前正在处理的块下标，循环计数器
    chunk_summaries: List[str] # 每一块单独摘要的结果列表
    final_summary: str     # 所有小块摘要合并后的最终摘要
    output_path: str       # 摘要写入的目标文件路径

# --------------------------
# 3. 定义各个节点 Node
# 节点函数规则：接收state，返回字典，字典会增量合并进state
# --------------------------
def load_and_split(state: SummaryState):
    print("\n[Node] 载入与切分...")
    # 从状态取出原始长文本
    text = state["raw_text"]
    # 简单按固定800字符切分文本（字符切割，不是语义切割）
    chunks = [text[i:i+800] for i in range(0, len(text), 800)]
    # 更新state：写入切块列表、索引归零、清空摘要收集列表
    return {"chunks": chunks, "current_index": 0, "chunk_summaries": []}


def summarize_chunk(state: SummaryState):
    # 获取当前要处理的块序号
    idx = state["current_index"]
    # 边界判断：索引超出块总数，直接返回，不再处理
    if idx >= len(state["chunks"]):
        return {"current_index": idx}
        
    # 取出当前这一段文本
    chunk = state["chunks"][idx]
    print(f"[Node] 正在摘要第 {idx + 1}/{len(state['chunks'])} 块...")
    
    # 调用大模型，对单块文本做摘要
    resp = llm.invoke(f"请将以下文本摘要成简短的一段话：\n{chunk}")
    
    # 在原有摘要列表基础上追加本次结果（不直接原地修改state）
    new_summaries = state["chunk_summaries"] + [resp.content]
    # 更新状态：新摘要列表 + 索引+1，准备下一轮
    return {"chunk_summaries": new_summaries, "current_index": idx + 1}


def reduce_summaries(state: SummaryState):
    print("\n[Node] 合并所有摘要...")
    # 用分割线把每一块的摘要拼接在一起
    all_summaries = "\n---\n".join(state["chunk_summaries"])
    # 调用LLM做归约：把一堆小块摘要浓缩成一份连贯终稿
    resp = llm.invoke(f"将以下多段摘要合并成一段连贯的最终摘要，去除冗余：\n{all_summaries}")
    # 写入最终摘要字段
    return {"final_summary": resp.content}


def write_file_node(state: SummaryState):
    print("\n[Node] 准备写入文件，触发硬门控...")
    # 调用你之前写好的安全工具write_summary（带路径白名单、幂等账本校验）
    result = write_summary.invoke({
        "content": state["final_summary"],
        "output_path": state["output_path"]
    })
    # 打印门控返回信息：成功 / 越权拦截 / 幂等跳过
    print(f"[门控返回] {result}")
    # 返回空字典：本节点只做外部副作用（写文件），不修改state
    return {}

# --------------------------
# 4. 搭建LangGraph工作流图
# --------------------------
# 实例化状态图，绑定我们定义好的SummaryState
workflow = StateGraph(SummaryState)

# 注册节点：给每个节点起名字，绑定对应的函数
workflow.add_node("load", load_and_split)
workflow.add_node("summarize", summarize_chunk)
workflow.add_node("reduce", reduce_summaries)
workflow.add_node("write", write_file_node)

# 设置整个图的入口节点：从 load 开始执行
workflow.set_entry_point("load")

# 普通边：load执行完成后，固定流向 summarize
workflow.add_edge("load", "summarize")

# 定义条件判断函数：决定summarize跑完之后往哪里走
def check_loop(state: SummaryState):
    # 当前索引 < 总块数：还有文本块没处理，继续循环摘要
    if state["current_index"] < len(state["chunks"]):
        return "summarize"
    # 全部块处理完毕，流向合并节点
    return "reduce"

# 添加条件边：summarize节点结束后执行check_loop
# 字典映射：返回值 -> 目标节点名
workflow.add_conditional_edges("summarize", check_loop, {
    "summarize": "summarize", 
    "reduce": "reduce"
})

# 合并完成后，流向写文件节点
workflow.add_edge("reduce", "write")
# 文件写入完成，到达END，整张图结束
workflow.add_edge("write", END)

# 内存检查点：自动保存每一步状态快照，支持断点、回溯、fork分支
memory = MemorySaver()
# 编译图，绑定检查点；编译后得到可调用的app
app = workflow.compile(checkpointer=memory)

# --------------------------
# 5. 测试入口
# --------------------------
if __name__ == "__main__":
    # 构造测试长文本，重复拼接生成几千字文本
    # 读取本地txt文件
    with open("input.txt", "r", encoding="utf-8") as f:
        long_text = f.read()

     
    # checkpointer必须依靠thread_id区分不同会话流
    config = {"configurable": {"thread_id": "test_thread_001"}}
    
    print("开始运行长文本摘要 Agent...")
    # 启动图执行，传入初始state数据 + 会话配置
    final_state = app.invoke({
        "raw_text": long_text,
        "output_path": "./outputs/graph_summary_v2.md"
    }, config=config)
    
    # 执行结束，取出最终摘要打印
    print("\n=== 最终合并摘要 ===")
    print(final_state["final_summary"])

