import json
import re

from langgraph.graph import END, START, StateGraph

import config
from data_sidekick.db import execute_query, get_schema_summary
from data_sidekick.llm import get_llm
from data_sidekick.rag import retrieve
from data_sidekick.state import AgentState


def _extract_json(text: str) -> dict:
    """从 LLM 输出里稳健地抽出 JSON（容忍 markdown 代码块围栏）。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except json.JSONDecodeError:
                pass
    return {}


# ----------------------------- 节点 -----------------------------

def retrieve_node(state: AgentState) -> dict:
    """检索口径 + 拉取 schema。"""
    question = state["question"]
    definitions = retrieve(question)
    return {
        "retrieved_definitions": definitions or "（未命中相关口径定义）",
        "schema_summary": get_schema_summary(),
    }


def generate_node(state: AgentState) -> dict:
    """根据问题 + 口径 + schema（+ 上轮错误）生成只读 SQL。"""
    llm = get_llm()
    prompt = _build_generate_prompt(state)
    resp = llm.invoke(prompt)
    parsed = _extract_json(resp.content)
    return {
        "sql": parsed.get("sql", ""),
        "reasoning": parsed.get("reasoning", ""),
        "clarify_needed": bool(parsed.get("clarify_needed", False)),
        "clarification": parsed.get("clarification", ""),
        # 每次生成 SQL 都算一次尝试，operator.add 累加
        "attempt": 1,
    }


def execute_node(state: AgentState) -> dict:
    """只读执行 SQL，成功返回结果，失败记录错误。"""
    sql = state.get("sql", "")
    try:
        columns, rows = execute_query(sql)
        return {"columns": columns, "rows": rows, "error": ""}
    except Exception as e:  # noqa: BLE001 - 需要捕获所有 DB 错误回喂重写
        return {"columns": [], "rows": [], "error": str(e)}


def answer_node(state: AgentState) -> dict:
    """生成最终自然语言回答；若处于澄清态则直接返回澄清问题。"""
    if state.get("clarify_needed"):
        return {"answer": state.get("clarification", "请补充信息。")}
    llm = get_llm()
    resp = llm.invoke(_build_answer_prompt(state))
    return {"answer": resp.content}


# ----------------------------- 路由 -----------------------------

def route_after_generate(state: AgentState) -> str:
    if state.get("clarify_needed"):
        return "answer"
    return "execute"


def route_after_execute(state: AgentState) -> str:
    if state.get("error") and state.get("attempt", 0) < config.MAX_RETRIES:
        return "generate"  # 自纠错：回喂错误重写
    return "answer"


# ----------------------------- Prompt -----------------------------

_SYSTEM = (
    "你是一名严谨的数据分析 SQL 助手。你必须严格依据给定的业务口径定义和数据库 "
    "schema 来编写只读查询，绝不臆造字段或口径。"
)


def _build_generate_prompt(state: AgentState) -> str:
    error_hint = state.get("error") or ""
    error_block = (
        f"\n【上一轮 SQL 执行报错，请定位并修正，不要重复同样的错误】\n{error_hint}"
        if error_hint
        else ""
    )
    return f"""{_SYSTEM}

【用户问题】
{state['question']}

【业务口径定义（若与问题相关必须遵守）】
{state['retrieved_definitions']}

【数据库 schema】
{state['schema_summary']}
{error_block}
要求：
1. 只输出只读查询（SELECT 或 WITH ... SELECT），禁止 INSERT/UPDATE/DELETE/DROP 等写操作。
2. 口径定义与问题相关时，务必按其过滤（例如 GMV 只统计 status='paid' 的订单）。
3. 仅当缺少必要信息导致无法唯一确定答案时，才把 clarify_needed 设为 true 并给出澄清问题；
   否则一律设为 false 并直接给出 SQL。
4. 结果行数较多时可加 LIMIT。

只返回一个 JSON 对象（不要任何多余文字），格式：
{{"clarify_needed": false, "clarification": "", "sql": "SELECT ...", "reasoning": "简要说明"}}"""


def _build_answer_prompt(state: AgentState) -> str:
    rows = state.get("rows", [])
    columns = state.get("columns", [])
    error = state.get("error") or ""
    if error:
        body = (
            f"查询执行最终失败（已重试 {state.get('attempt', 0)} 次）：{error}\n"
            "请向用户说明无法出数的原因，并给出可能的人为修正建议。"
        )
    elif not rows:
        body = "查询执行成功但结果为空。请说明可能原因（如时间范围无数据），并给出建议。"
    else:
        rows_preview = "\n".join(str(r) for r in rows[:20])
        body = (
            f"列名：{columns}\n查询结果（前 20 行）：\n{rows_preview}\n"
            f"结果共 {len(rows)} 行。"
        )
    return f"""{_SYSTEM}

请把下面的查询结果用自然、清晰的中文汇报给业务用户。

【用户问题】
{state['question']}

【依据的口径】
{state['retrieved_definitions']}

【实际执行的 SQL】
{state.get('sql', '')}

{body}

汇报要求：先给出结论，再说明口径与计算逻辑，最后附上执行的 SQL。"""


# ----------------------------- 组装图 -----------------------------

def build_graph():
    g = StateGraph(AgentState)
    g.add_node("retrieve", retrieve_node)
    g.add_node("generate", generate_node)
    g.add_node("execute", execute_node)
    g.add_node("answer", answer_node)

    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_conditional_edges(
        "generate",
        route_after_generate,
        {"answer": "answer", "execute": "execute"},
    )
    g.add_conditional_edges(
        "execute",
        route_after_execute,
        {"generate": "generate", "answer": "answer"},
    )
    g.add_edge("answer", END)

    return g.compile()


def run_query(question: str) -> dict:
    """对外入口：给它一个自然语言问题，返回完整状态 dict。"""
    graph = build_graph()
    initial: AgentState = {"question": question, "attempt": 0}
    return graph.invoke(initial)