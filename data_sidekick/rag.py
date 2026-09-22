"""口径检索层：用 Chroma 持久化业务"指标字典"，供智能体在写 SQL 前查口径。

这是本项目的差异化核心——把普通的 Text-to-SQL 升级成"口径先行"的 Agentic 流程：
先把问题向量化检索相关指标定义（如 GMV 只统计 paid 订单），再带着口径去生成 SQL，
避免 LLM 瞎猜字段口径。
"""

import chromadb

import config

_client = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=config.CHROMA_DIR)
        _collection = _client.get_or_create_collection(name=config.COLLECTION_NAME)
    return _collection


def upsert_definitions(texts: list[str], ids: list[str]) -> None:
    col = _get_collection()
    col.upsert(documents=texts, ids=ids)


def retrieve(query: str, k: int = 3) -> str:
    """检索与问题最相关的指标定义，拼接成文本返回。空库时返回空串。"""
    col = _get_collection()
    if col.count() == 0:
        return ""
    n = min(k, col.count())
    res = col.query(query_texts=[query], n_results=n)
    docs = res.get("documents", [[]])[0]
    return "\n\n".join(docs)