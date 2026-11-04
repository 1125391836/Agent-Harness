from langchain_openai import ChatOpenAI
from langchain_classic.chains.summarize import load_summarize_chain
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

llm = ChatOpenAI(
    model="qwen2.5",
    base_url="http://127.0.0.1:8080/v1",
    api_key="dummy",
    temperature=0.3,
    max_tokens=800,   # 每块摘要的输出上限，必须留出输入空间
)

text = open("input.txt", encoding="utf-8").read()
splitter = RecursiveCharacterTextSplitter(
    chunk_size=2500,      # 字符，不是token；2500字约1600-2000 token
    chunk_overlap=250,     # 10%重叠，防止切断语义边界
)
docs = [Document(page_content=t) for t in splitter.split_text(text)]

chain = load_summarize_chain(llm, chain_type="map_reduce")
result = chain.invoke(docs)
print(result["output_text"])
print(f"[诊断] 原文长度: {len(text)} 字符, 分块数: {len(docs)}")
