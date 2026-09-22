"""
运行：streamlit run app.py
"""

import streamlit as st

from data_sidekick.agent import run_query

st.set_page_config(page_title="DataSidekick", layout="wide")
st.title("DataSidekick - 自然语言问数助手")
st.caption("LangGraph + Agentic RAG + Text-to-SQL，只读查询 + 口径先行 + 自纠错")

# 提问示例，方便快速演示
st.sidebar.markdown("### 可以这样问")
for example in [
    "2024 年 6 月的 GMV 是多少？",
    "各城市的销售额排名",
    "复购率是多少？",
    "退款率是多少？",
]:
    if st.sidebar.button(example):
        st.session_state.pending = example

if "history" not in st.session_state:
    st.session_state.history = []
if "pending" not in st.session_state:
    st.session_state.pending = None

# 渲染历史
for msg in st.session_state.history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 处理输入（手动输入优先，其次 sidebar 示例按钮）
question = st.chat_input("用自然语言问你的数据")
if st.session_state.pending and not question:
    question = st.session_state.pending
    st.session_state.pending = None

if question:
    st.session_state.history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("检索口径并生成 SQL..."):
            result = run_query(question)

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

        st.session_state.history.append(
            {"role": "assistant", "content": result.get("answer", "")}
        )