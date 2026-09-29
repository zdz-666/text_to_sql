"""会话历史（短期记忆）：一次对话一个 JSON 文件，回答问题时读出来回注给模型。

目录布局：
    conversations/<conversation_id>.json
    {
      "id": "a1b2c3d4",
      "title": "2024 年 6 月的 GMV 是多少",   # 取自首条用户提问，供侧栏展示
      "created_at": 1730000000.0,
      "updated_at": 1730000000.0,
      "messages": [{"role": "user"/"assistant", "content": "...", "ts": 1730000000.0}],
      "summary": "早期对话摘要正文",          # 滑出窗口的旧消息被压缩成的摘要
      "summary_upto": 6                      # 摘要已覆盖 messages 的前多少条
    }

每次问答结束后把 user / assistant 两条消息追加落盘，下次提问时按"滑动窗口 + 记忆压缩"
回注：消息少时只取最近几条；攒够一定条数后，把滑出窗口的旧消息折进一份摘要，
摘要 + 最近几条一起回注（见 recent_messages）。用来支撑"那 5 月呢"这类省略式追问。
多个对话各自一个文件，互不干扰。
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
        # 记忆压缩的落盘位置：长对话才会被填上，短对话一直是空的
        "summary": "",
        "summary_upto": 0,
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


# ------------------------- 记忆压缩 -------------------------

_COMPRESS_PROMPT = """你在压缩一个数据分析助手的对话历史，供后续追问做指代消解。

已有摘要（可能为空）：
{previous}

需要并入摘要的新对话：
{pending}

把两者合并成一份更紧凑的摘要，要求：
1. 保留用户问过的指标与口径、时间范围、筛选条件（城市 / 品类 / 订单状态）。
2. 保留已经得出的结论数字，以及它对应的维度。
3. 保留用户提出的纠正与偏好（例如"不要算退款订单"）。
4. 丢弃寒暄、SQL 原文、推理过程、已经作废的中间步骤。
5. 用第三人称陈述，不要写成"用户问…助手答…"的流水账。
6. 直接输出摘要正文，不要标题、不要解释，控制在 300 字以内。"""


def _slim(msg: dict) -> dict:
    """把一条落盘消息压成回注用的 role / content：去掉 ts 并逐条截断。

    助手的回答里带 SQL 和推理过程，整段塞进 prompt 太费 token，所以按
    HISTORY_MAX_CHARS 截断——保留开头的结论部分，足够支撑指代消解。
    """
    content = str(msg.get("content") or "").strip().replace("\n", " ")
    return {"role": msg.get("role", "user"), "content": content[: config.HISTORY_MAX_CHARS]}


def _transcript(msgs: list[dict]) -> str:
    """把待压缩的消息排成"用户：…/助手：…"的纯文本，喂给压缩 prompt。"""
    lines = []
    for msg in msgs:
        slim = _slim(msg)
        role = "用户" if slim["role"] == "user" else "助手"
        lines.append(f"{role}：{slim['content']}")
    return "\n".join(lines)


def _summarize(previous: str, pending: list[dict]) -> str:
    """调大模型把 pending 折进已有摘要。失败返回空串，由调用方降级处理。"""
    from data_sidekick.llm import get_llm

    prompt = _COMPRESS_PROMPT.format(
        previous=previous or "（无）", pending=_transcript(pending)
    )
    try:
        text = (get_llm().invoke(prompt).content or "").strip()
    except Exception:  # noqa: BLE001 - 压缩是增强项，模型不可用不能阻断问数
        return ""
    return text[: config.HISTORY_SUMMARY_MAX_CHARS]


def _update_summary(conversation_id: str, boundary: int) -> str:
    """确保摘要覆盖到 messages[:boundary]，返回最新摘要（可能为空串）。

    只在两种情况下真的调大模型：还没有摘要，或窗口外又攒够了 COMPRESS_BATCH 条
    未压缩消息。其余请求直接复用落盘的摘要，零额外开销。
    """
    conv = _read(conversation_id)
    if conv is None:
        return ""
    summary = conv.get("summary") or ""
    upto = int(conv.get("summary_upto") or 0)
    if summary and boundary - upto < config.HISTORY_COMPRESS_BATCH:
        return summary

    pending = (conv.get("messages") or [])[upto:boundary]
    if not pending:
        return summary

    merged = _summarize(summary, pending)
    if not merged:
        return summary

    conv["summary"] = merged
    conv["summary_upto"] = boundary
    _write(conv)
    return merged


def recent_messages(conversation_id: str) -> list[dict]:
    """取回注给模型的历史：滑动窗口 + 记忆压缩。

    - 消息不足 HISTORY_COMPRESS_TRIGGER 条：不压缩，返回最近 HISTORY_RECENT_SHORT 条。
    - 达到阈值：返回 [早期对话摘要] + 最近 HISTORY_RECENT_COMPRESSED 条，
      摘要覆盖窗口之外的旧消息，在 _update_summary 里按增量折叠维护。
    - 压缩失败（如模型不可用）：退化成纯滑动窗口，仍给 HISTORY_RECENT_SHORT 条。

    摘要以 role="system" 的条目排在首位，由 agent._format_history 单独渲染。
    """
    msgs = get_messages(conversation_id)
    if not msgs:
        return []

    if len(msgs) < config.HISTORY_COMPRESS_TRIGGER:
        return [_slim(m) for m in msgs[-config.HISTORY_RECENT_SHORT:]]

    boundary = len(msgs) - config.HISTORY_RECENT_COMPRESSED
    summary = _update_summary(conversation_id, boundary)
    if not summary:
        # 没压出摘要就别把上下文砍到 4 条，退回短窗口的滑动窗口
        return [_slim(m) for m in msgs[-config.HISTORY_RECENT_SHORT:]]

    out = [{"role": "system", "content": f"早期对话摘要：{summary}"}]
    out.extend(_slim(m) for m in msgs[boundary:])
    return out