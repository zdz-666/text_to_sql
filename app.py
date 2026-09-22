"""
运行：streamlit run app.py
"""

import streamlit as st

from data_sidekick import memory
from data_sidekick.agent import run_query

st.set_page_config(page_title="DataSidekick", layout="wide")
st.title("DataSidekick - 自然语言问数助手")
st.caption("LangGraph + Agentic RAG + Text-to-SQL，只读查询 + 口径先行 + 自纠错")

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "pending" not in st.session_state:
    st.session_state.pending = None

# ------------------------- 侧栏：会话管理 + 提问示例 -------------------------
with st.sidebar:
    st.markdown("### 对话")
    if st.button("新建对话"):
        st.session_state.conversation_id = memory.create_conversation()["id"]
        st.session_state.pending = None
        st.rerun()

    conversations = memory.list_conversations()
    if not conversations:
        st.caption("还没有对话，点上面新建一个。")

    for conv in conversations:
        active = conv["id"] == st.session_state.conversation_id
        open_col, del_col = st.columns([0.72, 0.28])
        if open_col.button(
            conv["title"],
            key=f"open_{conv['id']}",
            type="primary" if active else "secondary",
            help=f"共 {conv['count']} 条消息",
        ):
            st.session_state.conversation_id = conv["id"]
            st.rerun()
        if del_col.button("删除", key=f"del_{conv['id']}"):
            memory.delete_conversation(conv["id"])
            if active:
                st.session_state.conversation_id = None
            st.rerun()

    st.divider()
    st.markdown("### 可以这样问")
    for example in [
        "2024 年 6 月的 GMV 是多少？",
        "各城市的销售额排名",
        "复购率是多少？",
        "退款率是多少？",
    ]:
        if st.button(example):
            st.session_state.pending = example

# ------------------------- 主区：渲染当前会话 -------------------------
conversation_id = st.session_state.conversation_id
if conversation_id is None:
    st.caption("左侧可新建 / 切换对话；直接在这里提问会自动新建一个对话。")

# 历史一律从会话文件读，切换对话后自然就换了一套记录
for msg in memory.get_messages(conversation_id) if conversation_id else []:
    with st.chat_message(msg.get("role", "assistant")):
        st.markdown(msg.get("content", ""))

# 处理输入（手动输入优先，其次 sidebar 示例按钮）
question = st.chat_input("用自然语言问你的数据")
if st.session_state.pending and not question:
    question = st.session_state.pending
    st.session_state.pending = None

if question:
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("判断意图并检索口径..."):
            # agent 负责按会话读写文件：进来读最近 k 条历史，答完把本轮落盘
            result = run_query(question, conversation_id=conversation_id)

        st.markdown(result.get("answer", ""))

        if result.get("sql"):
            with st.expander("查看执行的 SQL"):
                st.code(result["sql"], language="sql")

        rows = result.get("rows") or []
        columns = result.get("columns") or []
        if rows:
            data = [
                {columns[i]: row[i] for i in range(len(columns))}
                for row in rows
            ]
            with st.expander("查看原始结果"):
                st.dataframe(data)

    # 本轮已落盘，记住会话 id 即可，下次 rerun 会从文件重新渲染
    st.session_state.conversation_id = result.get("conversation_id")