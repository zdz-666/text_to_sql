"""FastAPI 后端：把 agent + memory 包装成 REST API，供 Streamlit 前端调用。

启动：uvicorn backend.server:app --reload --port 8000
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让 backend/server.py 也能 import 项目根目录下的 data_sidekick 和 config
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from data_sidekick import memory
from data_sidekick.agent import run_query
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="DataSidekick API", version="0.1.0")


# ------------------------- 请求 / 响应模型 -------------------------

class QueryRequest(BaseModel):
    question: str
    conversation_id: str | None = None  # 留空则新建


class QueryResponse(BaseModel):
    answer: str
    sql: str | None = None
    rows: list[list] | None = None
    columns: list[str] | None = None
    needs_sql: bool | None = None
    reasoning: str | None = None
    # 命中的预写指标模板标题，便于前端展示"这次用的是哪个口径"
    matched_metrics: list[str] | None = None
    # schema 注入模式："full"（小库全量）或 "retrieved"（大库检索子集）
    schema_mode: str | None = None
    conversation_id: str


class ConversationSummary(BaseModel):
    id: str
    title: str
    updated_at: float
    message_count: int


class ConversationDetail(BaseModel):
    id: str
    title: str
    created_at: float
    updated_at: float
    messages: list[dict]


# ------------------------- /api/query -------------------------

@app.post("/api/query")
def query(req: QueryRequest) -> QueryResponse:
    """一次问答：创建 / 追加会话文件，跑 LangGraph 图，落盘并返回结果。"""
    conversation_id = req.conversation_id or memory.create_conversation()["id"]
    history = memory.recent_messages(conversation_id)
    result = run_query(req.question, history=history)

    memory.append_message(conversation_id, "user", req.question)
    memory.append_message(conversation_id, "assistant", result.get("answer", ""))

    return QueryResponse(
        answer=result.get("answer", ""),
        sql=result.get("sql"),
        rows=result.get("rows"),
        columns=result.get("columns"),
        needs_sql=result.get("needs_sql"),
        reasoning=result.get("reasoning"),
        matched_metrics=[
            m.get("title", m.get("name", "")) for m in result.get("matched_metrics") or []
        ],
        schema_mode=result.get("schema_mode"),
        conversation_id=conversation_id,
    )


# ------------------------- /api/conversations -------------------------

@app.get("/api/conversations")
def list_conversations() -> list[ConversationSummary]:
    return [
        ConversationSummary(
            id=c["id"],
            title=c["title"],
            updated_at=c.get("updated_at", 0),
            message_count=c.get("count", 0),
        )
        for c in memory.list_conversations()
    ]


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> ConversationDetail | dict:
    conv = memory.get_conversation(conversation_id)
    if not conv:
        return {"detail": "not found"}
    return ConversationDetail(
        id=conv["id"],
        title=conv["title"],
        created_at=conv["created_at"],
        updated_at=conv["updated_at"],
        messages=conv.get("messages", []),
    )


@app.post("/api/conversations")
def create_conversation() -> ConversationDetail:
    conv = memory.create_conversation()
    return ConversationDetail(
        id=conv["id"],
        title=conv["title"],
        created_at=conv["created_at"],
        updated_at=conv["updated_at"],
        messages=conv.get("messages", []),
    )


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict:
    memory.delete_conversation(conversation_id)
    return {"status": "ok"}


# ------------------------- 健康检查 -------------------------

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}