"""检索层：用 Chroma 持久化两类索引，供智能体在写 SQL 前取用。

1. 指标索引（metric_definitions）——结构化指标字典，命中的指标带着预写 SQL 模板
   和所需 join 一起返回，避免 LLM 现场推理聚合表达式与连接路径（扇出陷阱的来源）。
2. 表索引（schema_tables）——库很大时用来按问题召回相关表，替代"全量塞 schema"。

Chroma 的 metadata 只支持标量，所以结构化指标整体 JSON 序列化后存在 payload 字段；
document 字段存渲染后的自然语言，仅供向量检索命中。
"""

import json

import chromadb

import config

_client = None
_metric_col = None
_schema_col = None


def _get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=config.CHROMA_DIR)
    return _client


def _get_collection():
    global _metric_col
    if _metric_col is None:
        _metric_col = _get_client().get_or_create_collection(name=config.COLLECTION_NAME)
    return _metric_col


def _get_schema_collection():
    global _schema_col
    if _schema_col is None:
        _schema_col = _get_client().get_or_create_collection(
            name=config.SCHEMA_COLLECTION_NAME
        )
    return _schema_col


# ----------------------------- 指标索引 -----------------------------

def _metric_document(metric: dict) -> str:
    """把指标渲染成用于向量检索的自然语言。"""
    parts = [
        metric.get("title", ""),
        "别名：" + "、".join(metric.get("aliases", [])),
        metric.get("description", ""),
    ]
    return "\n".join(p for p in parts if p)


def upsert_metrics(items: list[dict]) -> None:
    """写入结构化指标：文本用于检索，完整对象存进 metadata。"""
    if not items:
        return
    _get_collection().upsert(
        documents=[_metric_document(m) for m in items],
        ids=[m["name"] for m in items],
        metadatas=[{"payload": json.dumps(m, ensure_ascii=False)} for m in items],
    )


def retrieve_metrics(query: str, k: int = 3) -> list[dict]:
    """检索与问题最相关的结构化指标。空库或解析失败时返回空列表。"""
    col = _get_collection()
    if col.count() == 0:
        return []
    res = col.query(query_texts=[query], n_results=min(k, col.count()))
    out = []
    for meta in res.get("metadatas", [[]])[0]:
        payload = (meta or {}).get("payload")
        if not payload:
            continue
        try:
            out.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return out


# ----------------------------- 表索引（大库 schema 检索用） -----------------------------

def upsert_schema(tables: list[dict]) -> None:
    """把每张表压成一条文档（表名 + 全部列名），用于按问题召回相关表。"""
    if not tables:
        return
    docs, ids, metas = [], [], []
    for t in tables:
        cols = ", ".join(c["name"] for c in t["columns"])
        docs.append(f"{t['table']}\n{cols}")
        ids.append(t["table"])
        metas.append({"table": t["table"]})
    _get_schema_collection().upsert(documents=docs, ids=ids, metadatas=metas)


def retrieve_tables(query: str, k: int) -> list[tuple[str, float]]:
    """召回与问题最相关的表，返回 [(表名, 距离)]，距离越小越相关。

    距离是原始向量距离，量纲取决于 embedding 模型，调用方需要用它做相关性门控——
    跨语言时（中文问题 vs 英文标识符）距离会挤在一起，"最近邻"往往只是噪声。
    """
    col = _get_schema_collection()
    if col.count() == 0:
        return []
    res = col.query(query_texts=[query], n_results=min(k, col.count()))
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    out = []
    for meta, dist in zip(metas, dists):
        name = (meta or {}).get("table")
        if name:
            out.append((name, float(dist)))
    return out


# ----------------------------- 维护 -----------------------------

def reset() -> None:
    """清空两套索引。重新灌数据前调用，避免旧条目（如无 payload 的历史格式）残留。"""
    global _client, _metric_col, _schema_col
    client = chromadb.PersistentClient(path=config.CHROMA_DIR)
    for name in (config.COLLECTION_NAME, config.SCHEMA_COLLECTION_NAME):
        try:
            client.delete_collection(name=name)
        except Exception:  # noqa: BLE001 - 集合本就不存在时无需处理
            pass
    _client = None
    _metric_col = None
    _schema_col = None