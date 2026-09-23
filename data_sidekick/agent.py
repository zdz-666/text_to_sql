from langgraph.graph import END, START, StateGraph

import config
from data_sidekick.db import execute_query, get_schema_summary
from data_sidekick.llm import extract_json, get_llm
from data_sidekick.rag import retrieve
from data_sidekick.state import AgentState


# ----------------------------- 节点 -----------------------------

def route_node(state: AgentState) -> dict:
    """先判定本轮到底要不要查库：闲聊/概念解释直接回答，省掉检索和执行。"""
    llm = get_llm()
    resp = llm.invoke(_build_route_prompt(state))
    parsed = extract_json(resp.content)
    # 解析失败时按"需要查库"处理：多跑一次查询，总好过漏答一个取数问题
    return {"needs_sql": bool(parsed.get("needs_sql", True))}


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
    parsed = extract_json(resp.content)
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
    """生成最终自然语言回答；
    澄清态直接返回澄清问题，未走查库的非取数问题走闲聊式回答。"""
    if state.get("clarify_needed"):
        return {"answer": state.get("clarification", "请补充信息。")}
    builder = _build_answer_prompt if state.get("needs_sql", True) else _build_chat_prompt
    return {"answer": get_llm().invoke(builder(state)).content}


# ----------------------------- 路由 -----------------------------

def route_after_route(state: AgentState) -> str:
    return "retrieve" if state.get("needs_sql", True) else "answer"


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

_CHAT_SYSTEM = "你是一名严谨、友善的数据分析助手，正在与业务用户对话。"


def _format_history(history: list | None) -> str:
    """把最近 k 条会话历史压成一段紧凑文本。

    内容已在 memory.recent_messages() 里截断过，这里只负责排版。
    """
    if not history:
        return ""
    lines = []
    for msg in history:
        role = "用户" if msg.get("role") == "user" else "助手"
        lines.append(f"{role}：{msg.get('content', '')}")
    return "\n".join(lines)


def _history_block(state: AgentState) -> str:
    return _format_history(state.get("history")) or "（这是本轮对话的第一句话）"


def _build_route_prompt(state: AgentState) -> str:
    return f"""你是一个问数助手的意图路由。判断用户最新这句话是否需要查询数据库才能回答。

【最近对话】
{_history_block(state)}

【用户最新提问】
{state['question']}

判定规则：
1. 要取数才能回答的（算指标、看排名/趋势、查明细、对比数据）→ needs_sql 为 true。
2. 不需要查库的（打招呼、问你能力范围、解释业务概念、感谢、与数据无关的闲聊）→ false。
3. 追问里省略了主语但意图仍是取数（如"那 5 月呢"）→ true；这类必须结合最近对话判断。
4. 拿不准时按 true 处理。

只返回一个 JSON 对象（不要任何多余文字），格式：
{{"needs_sql": true, "reason": "简要说明判断依据"}}"""


def _build_generate_prompt(state: AgentState) -> str:
    error_hint = state.get("error") or ""
    error_block = (
        f"\n【上一轮 SQL 执行报错，请定位并修正，不要重复同样的错误】\n{error_hint}"
        if error_hint
        else ""
    )
    return f"""{_SYSTEM}

【最近对话（本轮问题若有省略或指代，以此为准）】
{_history_block(state)}

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


def _build_chat_prompt(state: AgentState) -> str:
    """路由判定无需查库时使用：直接凭常识和历史对话回答。"""
    return f"""{_CHAT_SYSTEM}

本轮问题经判定无需查询数据库，请直接回答，不要输出 SQL，也不要编造任何查询结果或数字。

【最近对话】
{_history_block(state)}

【用户问题】
{state['question']}

回答要求：自然连贯地接住上一轮话题，直接给结论，简短即可。"""


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

【最近对话】
{_history_block(state)}

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
    g.add_node("route", route_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("generate", generate_node)
    g.add_node("execute", execute_node)
    g.add_node("answer", answer_node)

    g.add_edge(START, "route")
    g.add_conditional_edges(
        "route",
        route_after_route,
        {"retrieve": "retrieve", "answer": "answer"},
    )
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


def run_query(question: str, history: list | None = None) -> dict:
    """纯函数：给定问题 + 最近 k 条对话历史，跑图并返回完整状态 dict。

    文件的读写（创建会话、读历史、追加消息）全部交给调用方（backend/server.py）处理，
    本函数不产生任何副作用，方便直接在 REST 端点和未来的异步 / 流式调用里复用。
    """
    initial: AgentState = {
        "question": question,
        "history": history or [],
        "attempt": 0,
    }
    return build_graph().invoke(initial)