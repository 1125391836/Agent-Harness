from secure_tools import write_summary

print("=== 测试 A：直接尝试越权路径 ===")
print(write_summary.invoke({
    "content": "Hacked", 
    "output_path": "../../etc/passwd"
}))

print("\n=== 测试 B：尝试写入危险后缀 ===")
print(write_summary.invoke({
    "content": "import os", 
    "output_path": "./outputs/evil.py"
}))

print("\n=== 测试 C：尝试覆盖已有文件 ===")
print(write_summary.invoke({
    "content": "覆盖测试", 
    "output_path": "./outputs/test_result.txt" # 这是刚才模型成功写入的文件
}))
