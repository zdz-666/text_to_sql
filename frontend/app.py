"""
前后端分离的 Streamlit 前端。
启动：streamlit run frontend/app.py
前提：后端 uvicorn backend.server:app --port 8000 已在运行。
"""

import sys
from pathlib import Path

# 让 frontend/app.py 也能 import 项目根目录的 config.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st
import requests

import config

API = config.BACKEND_URL.rstrip("/")


def _api(path: str, method: str = "GET", json: dict | None = None) -> dict | list | None:
    """Helper：调后端 REST 接口，网络异常时返回 None。"""
    url = f"{API}{path}"
    try:
        if method == "GET":
            resp = requests.get(url, timeout=30)
        elif method == "POST":
            resp = requests.post(url, json=json, timeout=60)
        elif method == "DELETE":
            resp = requests.delete(url, timeout=30)
        else:
            raise ValueError(f"unsupported method: {method}")
    except requests.RequestException:
        return None
    if not resp.ok:
        return None
    return resp.json()


# ------------------------- 初始化 session -------------------------

st.set_page_config(page_title="DataSidekick", layout="wide")

# ------------------------- 全局 CSS -------------------------

st.markdown(
    """
<style>
    /* ===== 侧栏 ===== */
    [data-testid="stSidebar"] {
        min-width: 280px !important;
        max-width: 320px !important;
    }
    [data-testid="stSidebar"] > div:first-child {
        padding: 22px 14px 8px;
    }

    /* 侧栏标题 */
    [data-testid="stSidebar"] h4 {
        font-size: 15px !important;
        margin-bottom: 8px;
        color: #374151;
    }

    /* 侧栏正文 */
    [data-testid="stSidebar"] .stMarkdown,
    [data-testid="stSidebar"] .stCaption {
        font-size: 14px !important;
    }

    /* 侧栏按钮 */
    [data-testid="stSidebar"] button {
        font-size: 14px !important;
        border-radius: 8px !important;
        padding: 6px 12px !important;
    }

    /* ===== 主区域 ===== */
    .main .block-container {
        max-width: 860px !important;
        padding: 24px 32px !important;
    }

    /* 页面标题 */
    [data-testid="stAppViewContainer"] h1 {
        font-size: 22px !important;
    }
    [data-testid="stAppViewContainer"] h3 {
        font-size: 18px !important;
    }

    /* ===== 聊天消息 ===== */
    [data-testid="stChatMessage"] {
        font-size: 16px !important;
        line-height: 1.7 !important;
    }
    [data-testid="stChatMessage"] > div:first-child {
        padding: 14px 20px !important;
        border-radius: 10px !important;
    }
    .stChatMessage p {
        font-size: 16px !important;
        line-height: 1.7 !important;
    }
    .stChatMessage li {
        font-size: 16px !important;
        line-height: 1.7 !important;
    }
    .stChatMessage code {
        font-size: 14px !important;
    }

    /* ===== 输入框 ===== */
    [data-testid="stChatInput"] textarea {
        font-size: 16px !important;
        min-height: 52px !important;
        padding: 14px 16px !important;
    }

    /* ===== expander ===== */
    [data-testid="stExpander"] summary {
        font-size: 14px !important;
    }

    /* ===== 提示文字 ===== */
    .stCaption {
        font-size: 15px !important;
    }

    /* ===== 分隔线 ===== */
    hr {
        margin: 16px 0 !important;
    }

    /* ===== dataframe ===== */
    [data-testid="stDataFrame"] {
        font-size: 13px !important;
    }

    /* ===== 滚动条美化 ===== */
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
</style>
""",
    unsafe_allow_html=True,
)

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "pending" not in st.session_state:
    st.session_state.pending = None


# ------------------------- 侧栏：会话管理 + 提问示例 -------------------------

with st.sidebar:
    st.markdown("#### 对话列表")

    if st.button("➕ 新建对话", use_container_width=True):
        data = _api("/api/conversations", method="POST")
        if data:
            st.session_state.conversation_id = data["id"]
            st.session_state.pending = None
            st.rerun()

    cs = _api("/api/conversations", method="GET") or []
    if not cs:
        st.caption("还没有对话记录，点击上方新建。")

    for conv in cs:
        active = conv["id"] == st.session_state.conversation_id
        msg_count = conv.get("message_count", 0)

        open_col, del_col = st.columns([0.78, 0.22], gap="small")

        label = conv.get("title", "新对话")
        if msg_count:
            label += f"  ({msg_count})"

        if open_col.button(
            label,
            key=f"open_{conv['id']}",
            type="primary" if active else "secondary",
            use_container_width=True,
        ):
            st.session_state.conversation_id = conv["id"]
            st.rerun()

        if del_col.button(
            "✕",
            key=f"del_{conv['id']}",
            use_container_width=True,
        ):
            _api(f"/api/conversations/{conv['id']}", method="DELETE")
            if active:
                st.session_state.conversation_id = None
            st.rerun()

    st.divider()

    st.markdown("#### 试试这样问")
    examples = [
        "2024 年 6 月的 GMV 是多少？",
        "各城市的销售额排名",
        "复购率是多少？",
        "退款率是多少？",
    ]
    for example in examples:
        if st.button(example, key=f"example_{hash(example)}", use_container_width=True):
            st.session_state.pending = example


# ------------------------- 主区：渲染当前会话 -------------------------

conversation_id = st.session_state.conversation_id

if not conversation_id:
    st.markdown("### 👋 欢迎使用 DataSidekick")
    st.markdown(
        "在左侧**新建 / 选择一个对话**，或直接在下方输入框里问你的第一个问题，"
        "系统会自动为你创建一个新对话。"
    )
else:
    detail = _api(f"/api/conversations/{conversation_id}", method="GET")
    if detail:
        st.markdown(f"### {detail.get('title', '新对话')}")
        for msg in detail.get("messages", []):
            with st.chat_message(msg.get("role", "assistant")):
                st.markdown(msg.get("content", ""))
    else:
        st.caption("该会话已被删除。")

# 输入：手动打优先，其次 sidebar 示例按钮
question = st.chat_input("输入你的数据问题，按 Enter 发送…")
if st.session_state.pending and not question:
    question = st.session_state.pending
    st.session_state.pending = None

if question:
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("判断意图并检索口径..."):
            data = _api(
                "/api/query",
                method="POST",
                json={"question": question, "conversation_id": conversation_id},
            )

        if not data:
            st.error("后端未响应，请确认 uvicorn backend.server:app 已启动。")
        else:
            st.markdown(data.get("answer", ""))

            if data.get("matched_metrics"):
                st.caption("命中指标模板：" + "、".join(data["matched_metrics"]))
            if data.get("schema_mode") == "retrieved":
                st.caption("schema 注入：检索式子集（库较大，仅注入相关表）")

            if data.get("sql"):
                with st.expander("查看执行的 SQL"):
                    st.code(data["sql"], language="sql")

            rows = data.get("rows") or []
            columns = data.get("columns") or []
            if rows:
                table = [
                    {columns[i]: row[i] for i in range(len(columns))}
                    for row in rows
                ]
                with st.expander("查看原始结果"):
                    st.dataframe(table, use_container_width=True)

    if data:
        st.session_state.conversation_id = data.get("conversation_id")