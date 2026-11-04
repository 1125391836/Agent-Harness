from typing import TypedDict, List
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI

# 1. 初始化模型
llm = ChatOpenAI(model="qwen-local", base_url="http://127.0.0.1:8080/v1", api_key="dummy", temperature=0)

# 2. 定义状态
class SummaryState(TypedDict):
    raw_text: str
    chunks: List[str]
    current_index: int
    chunk_summaries: List[str]
    final_summary: str

# 3. 定义节点
def load_and_split(state: SummaryState):
    print("\n[Node] 载入与切分...")
    text = state["raw_text"]
    chunks = [text[i:i+800] for i in range(0, len(text), 800)]
    return {"chunks": chunks, "current_index": 0, "chunk_summaries": []}

def summarize_chunk(state: SummaryState):
    idx = state["current_index"]
    if idx >= len(state["chunks"]):
        return {"current_index": idx}
        
    chunk = state["chunks"][idx]
    print(f"[Node] 正在摘要第 {idx + 1}/{len(state['chunks'])} 块...")
    resp = llm.invoke(f"请将以下文本摘要成简短的一段话：\n{chunk}")
    new_summaries = state["chunk_summaries"] + [resp.content]
    return {"chunk_summaries": new_summaries, "current_index": idx + 1}

def reduce_summaries(state: SummaryState):
    print("\n[Node] 合并所有摘要...")
    all_summaries = "\n---\n".join(state["chunk_summaries"])
    resp = llm.invoke(f"将以下多段摘要合并成一段连贯的最终摘要，去除冗余：\n{all_summaries}")
    return {"final_summary": resp.content}

# 4. 构建纯净版图 (无 write 节点)
workflow = StateGraph(SummaryState)

workflow.add_node("load", load_and_split)
workflow.add_node("summarize", summarize_chunk)
workflow.add_node("reduce", reduce_summaries)

workflow.set_entry_point("load")
workflow.add_edge("load", "summarize")

def check_loop(state: SummaryState):
    if state["current_index"] < len(state["chunks"]):
        return "summarize"
    return "reduce"

workflow.add_conditional_edges("summarize", check_loop, {
    "summarize": "summarize", 
    "reduce": "reduce"
})

# 🚨 关键：reduce 直接指向 END
workflow.add_edge("reduce", END)

# 挂载 MemorySaver
memory = MemorySaver()
app = workflow.compile(checkpointer=memory)
