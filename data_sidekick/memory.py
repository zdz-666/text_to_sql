"""会话历史（短期记忆）：一次对话一个 JSON 文件，回答问题时读出来回注给模型。

目录布局：
    conversations/<conversation_id>.json
    {
      "id": "a1b2c3d4",
      "title": "2024 年 6 月的 GMV 是多少",   # 取自首条用户提问，供侧栏展示
      "created_at": 1730000000.0,
      "updated_at": 1730000000.0,
      "messages": [{"role": "user"/"assistant", "content": "...", "ts": 1730000000.0}]
    }

每次问答结束后把 user / assistant 两条消息追加落盘，下次提问时只取最近 k 条回注，
用来支撑"那 5 月呢"这类省略式追问。多个对话各自一个文件，互不干扰。
"""

import json
import re
import time
import uuid
from pathlib import Path

import config

# 会话 id 会被拼进文件名，只允许这套字符，避免路径穿越
_ID_PATTERN = re.compile(r"[0-9a-zA-Z_-]{1,64}")

_TITLE_MAX_LEN = 20


# ------------------------- 路径与底层读写 -------------------------

def _dir() -> Path:
    d = Path(config.CONVERSATIONS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(conversation_id: str) -> Path | None:
    """拼出会话文件路径；id 会被拼进文件名，不合规范就返回 None，挡住路径穿越。"""
    if not _ID_PATTERN.fullmatch(conversation_id or ""):
        return None
    return _dir() / f"{conversation_id}.json"


def _read(conversation_id: str) -> dict | None:
    """读单个会话。id 非法、文件缺失或内容损坏一律返回 None，调用方当作新会话处理。"""
    p = _file(conversation_id)
    if p is None or not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _write(conv: dict) -> None:
    # id 一律由 create_conversation 生成，必然合法
    _file(conv["id"]).write_text(
        json.dumps(conv, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _make_title(text: str) -> str:
    title = re.sub(r"\s+", " ", (text or "").strip())
    return title[:_TITLE_MAX_LEN] or "新对话"


# ------------------------- 会话管理 -------------------------

def create_conversation(title: str = "新对话") -> dict:
    """新建一个空会话并落盘，返回会话 dict。"""
    now = time.time()
    conv = {
        "id": uuid.uuid4().hex[:12],
        "title": title,
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }
    _write(conv)
    return conv


def list_conversations() -> list[dict]:
    """列出全部会话的摘要，按最近更新倒序；文件名不合规范或内容损坏的直接跳过。"""
    items = []
    for p in _dir().glob("*.json"):
        conv = _read(p.stem)
        if not conv:
            continue
        items.append(
            {
                "id": conv.get("id", p.stem),
                "title": conv.get("title") or "新对话",
                "updated_at": conv.get("updated_at", 0),
                "count": len(conv.get("messages") or []),
            }
        )
    return sorted(items, key=lambda c: c["updated_at"], reverse=True)


def get_conversation(conversation_id: str) -> dict | None:
    return _read(conversation_id)


def get_messages(conversation_id: str) -> list[dict]:
    """按写入顺序取出全部消息，供前端渲染。"""
    conv = _read(conversation_id)
    return list(conv.get("messages") or []) if conv else []


def delete_conversation(conversation_id: str) -> None:
    p = _file(conversation_id)
    if p is not None and p.exists():
        p.unlink()


# ------------------------- 追加与读取 -------------------------

def append_message(conversation_id: str, role: str, content: str) -> dict | None:
    """追加一条消息并落盘，返回更新后的会话。

    首条用户提问会顺带把会话标题补上，这样侧栏不用再额外调模型起名。
    """
    conv = _read(conversation_id)
    if conv is None:
        return None

    now = time.time()
    conv.setdefault("messages", []).append(
        {"role": role, "content": content, "ts": now}
    )
    conv["updated_at"] = now
    if role == "user" and conv.get("title") in (None, "", "新对话"):
        conv["title"] = _make_title(content)

    _write(conv)
    return conv


def recent_messages(conversation_id: str, limit: int | None = None) -> list[dict]:
    """取最近 k 条消息，只保留 role / content，并逐条截断。

    助手的回答里带 SQL 和推理过程，整段塞进 prompt 太费 token，所以按
    HISTORY_MAX_CHARS 截断——保留开头的结论部分，足够支撑指代消解。
    """
    msgs = get_messages(conversation_id)
    k = limit or config.HISTORY_MESSAGES
    out = []
    for msg in msgs[-k:]:
        content = str(msg.get("content") or "").strip().replace("\n", " ")
        out.append(
            {"role": msg.get("role", "user"), "content": content[: config.HISTORY_MAX_CHARS]}
        )
    return out