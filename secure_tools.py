# 从 langchain_core 导入 tool 装饰器，用来把普通函数包装成 LangChain / LangGraph 可用的工具
from langchain_core.tools import tool
# Path 是 pathlib 里的路径工具，比 os.path 更优雅，支持路径拼接、解析、判断后缀等
from pathlib import Path
# 导入你上一段写的账本模块：生成幂等key、检查账本、写入账本记录（幂等防重复）
from runtime_ledger import generate_idempotency_key, check_ledger, record_to_ledger

# 定义写入白名单目录：./outputs，resolve() 拿到它的【绝对路径】，避免相对路径带来的绕过问题
ALLOWED_DIR = Path("./outputs").resolve()

# tool 装饰器：标记这是一个给大模型调用的工具
# LangGraph / LangChain 会自动读取函数签名、docstring，让LLM看懂这个工具怎么调用
@tool
def write_summary(content: str, output_path: str) -> str:
    """将最终摘要写入指定路径。仅允许写入 ./outputs/ 目录下的 .md 或 .txt 文件。"""
    try:
        # 把传入的 output_path 转为 Path 对象，并且 resolve() 转为绝对路径
        # 关键：消除 ../ 这类相对路径逃逸，方便后面判断是否越权
        p = Path(output_path).resolve()
        
        # ==========================================
        # 1. 静态硬门控：路径与文件类型检查
        # ==========================================
        # 判断：目标绝对路径是不是以允许目录开头，防止模型写外面（比如 /etc、../ 逃逸）
        if not str(p).startswith(str(ALLOWED_DIR)):
            return f"[HARD GATE DENY] 拦截成功：路径越权！{p} 不在允许的 {ALLOWED_DIR} 内。"
            
        # 检查文件后缀：只允许 .md 和 .txt，禁止模型写 py/sh/其他危险文件
        if p.suffix not in [".md", ".txt"]:
            return f"[HARD GATE DENY] 拦截成功：文件类型错误！只允许 .md 或 .txt，当前为 {p.suffix}。"
        # ==========================================
        # 2. 副作用账本：幂等性检查与防覆盖
        # ==========================================
        # 生成幂等键：动作类型 write_file + 文件绝对路径 + 文件内容
        # 相同路径+相同内容，得到同一个key，用来判断是不是重复写入
        idem_key = generate_idempotency_key("write_file", str(p), content)
        
        # 账本查询：这条记录之前已经成功执行过 → 直接拦截，不重复写
        if check_ledger(idem_key):
            return f"[LEDGER BLOCK] 拦截成功：该摘要已成功写入 {p}，已跳过重复执行。键: {idem_key}"
        # 额外一层保护：就算内容不一样，只要文件已经存在，也拒绝覆盖原有文件
        if p.exists():
            return f"[HARD GATE DENY] 拦截成功：目标文件 {p} 已存在，拒绝覆盖。"
        # ==========================================
        # 3. 执行真实副作用并记账
        # ==========================================
        # 自动创建父文件夹；parents=True 多级目录；exist_ok=True 目录已存在不会报错
        p.parent.mkdir(parents=True, exist_ok=True)
        # 把文本写入文件，utf-8编码，支持中文
        p.write_text(content, encoding="utf-8")
        
        # 写入账本：标记这个幂等key状态为 executed，附带元信息（路径、动作）
        record_to_ledger(idem_key, "executed", {"path": str(p), "action": "write_file"})
        # 返回成功信息给大模型，告知写入结果、文本长度
        return f"[SUCCESS] 成功写入 {p}，长度 {len(content)} 字符"
    except Exception as e:
        # 兜底捕获所有异常：权限、磁盘满、IO错误等
        # 把错误字符串返回给LLM，而不是直接抛出异常炸掉整个LangGraph流程
        return f"[RUNTIME ERROR] 写入失败: {str(e)}"

