# 导入内置库
import hashlib      # 用来做哈希，生成摘要（这里md5）
import json         # 读写json账本文件
from datetime import datetime  # 获取当前时间，记录时间戳

# 常量：账本文件名，所有幂等记录都存在这个json文件里
LEDGER_FILE = "side_effect_ledger.json"


def generate_idempotency_key(action: str, path: str, content: str) -> str:
    """
    生成【幂等键 idempotency key】
    作用：相同动作+目标路径+相同内容，算出同一个key，用来判断是不是重复执行
    :param action: 动作类型，比如 "write_file" / "delete" / "run_command"
    :param path: 操作目标路径，比如 "./test.txt"
    :param content: 操作的内容（写入的文本、指令等）
    :return: 拼接好的幂等字符串key
    """
    # 把content转utf8字节，md5哈希，转16进制字符串，取前8位缩短长度
    content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()[:8]
    # 组装key格式：动作:路径:内容哈希
    # 相同action、相同path、相同content → key一模一样
    return f"{action}:{path}:{content_hash}"


def _load_ledger() -> dict:
    """
    【内部私有函数】安全读取账本
    处理几种异常：文件不存在、文件为空、json格式损坏
    返回账本字典 {幂等key: 记录信息}
    """
    try:
        # 只读模式打开账本文件
        with open(LEDGER_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()   # 读取全部内容，去掉首尾空白
            if not content:              # 文件是空字符串（空文件）
                return {}                # 返回空字典，代表空账本
            return json.loads(content)   # json字符串 → python字典
    except (FileNotFoundError, json.JSONDecodeError):
        # 捕获两种异常：
        # 1. FileNotFoundError：账本文件还没创建
        # 2. JSONDecodeError：文件内容不是合法json（乱码、半截json）
        return {}  # 出错时视为空账本


def check_ledger(idem_key: str) -> bool:
    """
    查询账本：这个幂等key对应的动作【是否已经成功执行过】
    :param idem_key: generate_idempotency_key生成的key
    :return: True=已经执行完成；False=没记录 / 记录状态不是executed
    """
    ledger = _load_ledger()
    # 判断：key存在，并且status字段等于"executed"
    return idem_key in ledger and ledger[idem_key].get("status") == "executed"


def record_to_ledger(key: str, status: str, metadata: dict):
    """
    向账本写入一条记录
    :param key: 幂等键
    :param status: 状态，比如 "executed" / "pending" / "failed"
    :param metadata: 额外自定义信息，比如 {"note": "xxx"}
    """
    ledger = _load_ledger()   # 先读取当前账本
    ledger[key] = {
        "status": status,
        "timestamp": datetime.now().isoformat(),  # ISO标准时间字符串，方便后续解析
        **metadata  # 解包，把metadata里面所有k-v塞进来
    }
    # 写回json文件，indent=2格式化换行，ensure_ascii=False支持中文正常保存
    with open(LEDGER_FILE, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)

