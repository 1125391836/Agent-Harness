# ============================================================
# 只有fork，模拟工具调用和LLM推理的延迟分解
# ============================================================
import time                                      # 标准库：用来计时(perf_counter)和模拟耗时(sleep)
from pathlib import Path                         # 标准库：处理文件路径（本脚本里暂未实际用到）
from typing import TypedDict, NotRequired        # 标准库：定义"状态字典"的结构和可选字段

from langgraph.graph import StateGraph, START, END   # LangGraph 核心：建图类 + 起点/终点常量
from langgraph.checkpoint.memory import MemorySaver  # LangGraph 内置存档器：把 checkpoint 存在内存里
from langchain_openai import ChatOpenAI              # LangChain 的 OpenAI 兼容客户端，用来调本地 llama-server


# ============================================================
# 状态定义：描述节点之间传递的"数据包"长什么样
# ============================================================
class State(TypedDict):                          # 用 TypedDict 声明一个带固定键的字典类型
    topic: NotRequired[str]                      # "话题"字段，字符串，初始可以不存在
    tool_result: NotRequired[str]                # "工具结果"字段，字符串，初始可以不存在
    llm_result: NotRequired[str]                 # "LLM 输出"字段，字符串，初始可以不存在
    summary: NotRequired[str]                    # "总结"字段，字符串，初始可以不存在
    _timing: NotRequired[dict]                   # 收集各节点内部耗时的字典，用来做逐段分解


# ============================================================
# LLM 客户端：指向本地 llama.cpp 的 llama-server
# ============================================================
llm = ChatOpenAI(
    model="qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",                    # 对应启动 llama-server 时的 --alias
    base_url="http://localhost:8080/v1",   # llama-server 的 OpenAI 兼容根路径
    api_key="no-key",                     # llama.cpp 不验证 key，但占位符必须非空
    temperature=0,                        # 固定为 0，减少计时波动
    max_tokens=64,
)


# ============================================================
# 节点函数：每个节点都是"收状态 → 干活 → 返回要更新的字段"
# ============================================================
def prepare(state: State):                       # 节点1：准备阶段，接收当前状态
    return {"topic": "socks in the dryer"}       # 往状态里塞入 topic="袜子去哪了"，LangGraph 会自动合并


def _create_tool_session():                      # 辅助函数：模拟"fork 后重建工具会话"
    time.sleep(0.01)                             # 故意等 10 毫秒，模拟建立连接/握手的过程
    return {"connected": True, "endpoint": "local"}  # 返回一个会话对象（真实场景可能是 requests.Session / DB 连接）


def tool_call(state: State):                     # 节点2：工具调用，接收当前状态
    t0 = time.perf_counter()                     # 开始计时：会话重建
    session = _create_tool_session()             # 模拟 fork 后重新连接服务
    session_ms = (time.perf_counter() - t0) * 1000  # 会话重建耗时（毫秒）

    t1 = time.perf_counter()                     # 开始计时：真正的工具执行
    time.sleep(0.05)                             # 故意等 50 毫秒，模拟真实工具 I/O（查库/调API）
    result = f"result for {state['topic']}"      # 读当前 topic，生成结果
    exec_ms = (time.perf_counter() - t1) * 1000  # 工具执行耗时（毫秒）

    timing = {**state.get("_timing", {}),        # 取出已有的计时字典（首次为空）
              "session_rebuild_ms": session_ms,  # 加入"会话重建"耗时
              "tool_exec_ms": exec_ms}           # 加入"工具执行"耗时
    return {"tool_result": result, "_timing": timing}  # 把结果和计时一起写回状态


def llm_call(state: State):                      # 节点3：调用本地 LLM 推理
    t0 = time.perf_counter()                     # 开始计时：LLM 推理等待
    prompt = f"把下面这句话翻译成英文，只输出译文：{state['tool_result']}"  # 用工具结果构造 prompt
    response = llm.invoke(prompt)                # 同步调用 llama-server，阻塞直到返回
    llm_ms = (time.perf_counter() - t0) * 1000   # LLM 推理耗时（毫秒）
    print(f"  [llm_call internal] {llm_ms:.2f} ms")  # 立即打印，方便观察单次推理延迟

    timing = {**state.get("_timing", {}),        # 取出已有的计时字典
              "llm_ms": llm_ms}                  # 加入"LLM 推理"耗时
    return {"llm_result": response.content, "_timing": timing}  # 把 LLM 输出和计时写回状态


def summarize(state: State):                     # 节点4：总结，接收当前状态
    return {"summary": f"summary: {state.get('llm_result', state['tool_result'])}"}
    # 上面这行：优先用 LLM 的输出做总结；如果 LLM 没跑（例如旧流程），回退到 tool_result


# ============================================================
# 建图函数：把四个节点组装成一条流水线，并绑定存档器
# ============================================================
def build_graph(checkpointer):                   # 参数是存档器，方便以后换成 ProcessForkCheckpointer
    g = StateGraph(State)                        # 创建一个以 State 为状态结构的图对象
    g.add_node("prepare", prepare)               # 把 prepare 函数注册成名为 "prepare" 的节点
    g.add_node("tool_call", tool_call)           # 把 tool_call 函数注册成名为 "tool_call" 的节点
    g.add_node("llm_call", llm_call)             # 把 llm_call 函数注册成名为 "llm_call" 的节点
    g.add_node("summarize", summarize)           # 把 summarize 函数注册成名为 "summarize" 的节点
    g.add_edge(START, "prepare")                 # 画箭头：从起点 → prepare
    g.add_edge("prepare", "tool_call")           # 画箭头：prepare → tool_call
    g.add_edge("tool_call", "llm_call")          # 画箭头：tool_call → llm_call
    g.add_edge("llm_call", "summarize")          # 画箭头：llm_call → summarize
    g.add_edge("summarize", END)                 # 画箭头：summarize → 终点
    return g.compile(checkpointer=checkpointer)  # 编译成可运行对象，并告诉它用哪个存档器


# ============================================================
# 计时工具：通用包装器，量任何函数跑多久
# ============================================================
def timed(fn, *args, **kwargs):                  # fn 是要计时的函数，*args/**kwargs 原样转发给 fn
    t0 = time.perf_counter()                     # 开始计时（高精度单调时钟，适合测短代码块）
    out = fn(*args, **kwargs)                    # 执行目标函数，这里会阻塞直到跑完
    ms = (time.perf_counter() - t0) * 1000       # 结束计时，差值乘 1000 换算成毫秒
    return out, ms                               # 返回：函数结果 + 耗时（毫秒）


# ============================================================
# 主流程
# ============================================================
def main():
    # ---------- 准备阶段 ----------
    store_path = Path.home() / ".processfork"    # 拼出 ~/.processfork 路径（当前用 MemorySaver，暂未用到）
    checkpointer = MemorySaver()                 # 创建内存版存档器：checkpoint 存内存，程序关了就没了
    graph = build_graph(checkpointer)            # 用这个存档器把图建好

    config = {"configurable": {"thread_id": "demo-fork"}}  # 给这一局起名，存档都挂在这个 thread_id 下

    # ---------- 第一段：从头完整跑一遍，并计时 ----------
    first_result, first_invoke_ms = timed(graph.invoke, {}, config)
    # 上面这行：timed 包装 graph.invoke，{} 是空输入（prepare 自己生成 topic），config 指定线程
    # 结果：first_result 是最终状态，first_invoke_ms 是全程耗时（毫秒）
    print(f"[first invoke] total: {first_invoke_ms:.2f} ms")   # 打印首次执行总耗时

    # ---------- 第二段：翻存档，找到 fork 点 ----------
    history = list(graph.get_state_history(config))            # 取出这一局的所有存档，转成列表
    before_tool = next(s for s in history if s.next == ("tool_call",))
    # 上面这行：在每个存档里找"下一步是 tool_call"的那个 → 即 prepare 做完、tool_call 未开始的瞬间
    print(f"[fork point] next={before_tool.next}")             # 打印这个存档的下一步（应为 ('tool_call',)）
    print(f"[fork point] checkpoint_id={before_tool.config['configurable']['checkpoint_id']}")
    # 上面这行：打印该存档的唯一编号，确认找到的是对的存档

    # ---------- 第三段：复制存档 + 改条件，造出 fork ----------
    fork_config, update_state_ms = timed(
        graph.update_state,                      # 要计时的函数：update_state（基于存档创建新分支）
        before_tool.config,                      # 参数1：在哪个存档上分叉
        {"topic": "chickens"},                   # 参数2：把 topic 改成 "chickens"
    )
    # 结果：fork_config 是新分支的地址；update_state_ms 是分叉创建耗时
    print(f"[update_state] fork creation: {update_state_ms:.2f} ms")   # 打印分叉创建耗时

    # ---------- 第四段：从新分支接着跑 ----------
    fork_result, fork_invoke_ms = timed(graph.invoke, None, fork_config)
    # 上面这行：invoke 的第一个参数是 None，表示"不用给新输入，状态都在存档里"
    # 图会从 tool_call 开始，跑 tool_call → llm_call → summarize
    print(f"[fork invoke] to summarize done: {fork_invoke_ms:.2f} ms")  # 打印分支继续跑的总耗时
    print(f"[fork result] summary={fork_result['summary']!r}")
    # 上面这行：打印分支最终结果，应包含 "chickens"，这是 fork 成功的证据

    # ---------- 第五段：逐段延迟分解 ----------
    timing = fork_result.get("_timing", {})      # 从 fork 分支的最终状态里取出计时字典
    session_ms = timing.get("session_rebuild_ms", 0)  # 工具会话重建耗时
    tool_ms = timing.get("tool_exec_ms", 0)      # 工具执行耗时
    llm_ms = timing.get("llm_ms", 0)             # LLM 推理耗时
    node_total = session_ms + tool_ms + llm_ms   # 三个节点内部的耗时之和

    print("\n========== fork 后逐段延迟分解 ==========")
    print(f"[④ 工具会话重建] {session_ms:.2f} ms")
    print(f"[   工具执行 I/O ] {tool_ms:.2f} ms")
    print(f"[⑤ LLM 推理等待] {llm_ms:.2f} ms")
    print(f"[   节点内部合计 ] {node_total:.2f} ms")
    print(f"[   外层总耗时   ] {fork_invoke_ms:.2f} ms")
    print(f"[③ 引擎+存档开销 ] {fork_invoke_ms - node_total:.2f} ms")
    # 上面这行：fork 后 invoke 总耗时减去节点内部耗时，剩下的就是引擎调度 + checkpoint 写入开销

    # ---------- 第六段：首次执行的 checkpoint 开销（近似） ----------
    first_timing = first_result.get("_timing", {})  # 首次执行各节点的计时
    first_node_total = sum(first_timing.values())   # 首次执行节点内部总耗时
    checkpoint_overhead_total = first_invoke_ms - first_node_total  # 总耗时减节点耗时 = 存档+引擎开销
    print("\n========== 首次执行 checkpoint 开销（近似） ==========")
    print(f"[checkpoint] total overhead (approx): {checkpoint_overhead_total:.2f} ms")
    print(f"[checkpoint] per checkpoint (approx): {checkpoint_overhead_total / 5:.2f} ms")
    # 上面这行：现在有 5 个存档（START + 4 个节点后各一个），所以除以 5


# ============================================================
# 程序入口：只有直接运行这个文件时才执行 main()
# ============================================================
if __name__ == "__main__":                       # 如果本文件是被直接运行的（不是被 import 的）
    main()                                       # 就调用 main() 跑起来
