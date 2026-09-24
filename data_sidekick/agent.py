from langgraph.graph import END, START, StateGraph

import config
from data_sidekick import semantics
from data_sidekick.db import execute_query
from data_sidekick.llm import extract_json, get_llm, route_llm
from data_sidekick.rag import retrieve_metrics
from data_sidekick.schema import select as select_schema
from data_sidekick.state import AgentState


# ----------------------------- 节点 -----------------------------

def route_node(state: AgentState) -> dict:
    """先判定本轮到底要不要查库：闲聊/概念解释直接回答，省掉检索和执行。

    路由使用硅基流动 Kev-4b 快速决策模型（SystemOne API），
    比通用 chat 模型的意图判定更快、更省 token。
    """
    return {"needs_sql": route_llm(_build_route_state(state))["needs_sql"]}


def retrieve_node(state: AgentState) -> dict:
    """检索命中的结构化指标 + 按需检索 schema。

    schema 走 schema.select()：小库全量注入，大库只注入相关子集 +
    沿已声明 join 的闭包。指标模板涉及的表会被锚定，保证模板始终可用。
    """
    matched = retrieve_metrics(state["question"], k=config.SEMANTICS_TOP_K)
    definitions = "\n\n".join(semantics.metric_for_prompt(m) for m in matched)
    picked = select_schema(state["question"], matched)
    return {
        "matched_metrics": matched,
        "retrieved_definitions": definitions or "（未命中任何预写指标模板）",
        "schema_summary": picked["text"],
        "schema_mode": picked["mode"],
    }


def generate_node(state: AgentState) -> dict:
    """根据问题 + 指标模板 + 已声明 join + schema（+ 上轮错误）生成只读 SQL。"""
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


def validate_node(state: AgentState) -> dict:
    """用声明的 join 关系静态校验候选 SQL，拦截扇出陷阱。

    这是本项目最关键的守卫：扇出导致的重复计数不会报错、不会越界，
    结果看起来完全正常，只有数字是错的。必须在这一步拦住。
    """
    issues = semantics.validate_sql(state.get("sql", ""))
    if not issues:
        return {"violations": [], "error": ""}
    return {
        "violations": issues,
        "error": "\n".join(i["message"] for i in issues),
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
    return "validate"


def route_after_validate(state: AgentState) -> str:
    """有扇出陷阱就回炉重写；重试耗尽则不再执行，直接带着警告去回答。"""
    if state.get("violations"):
        if state.get("attempt", 0) < config.MAX_RETRIES:
            return "generate"
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


def _metrics_titles(state: AgentState) -> str:
    matched = state.get("matched_metrics") or []
    if not matched:
        return "（未命中预写模板，SQL 由模型依 schema 自行编写）"
    return "、".join(m.get("title", m.get("name", "")) for m in matched)


def _build_route_state(state: AgentState) -> str:
    """为 Kev-4b SystemOne API 拼装上下文。Kev-4b 的 state 字段接收自由文本，
    把历史对话、当前问题、判定规则全部放进去，由模型自行理解并输出 noul 分数。"""
    return f"""最近对话：
{_history_block(state)}

用户最新提问：
{state['question']}

判定规则：
1. 要取数才能回答的（算指标、看排名/趋势、查明细、对比数据）→ 需要查库。
2. 不需要查库的（打招呼、问能力范围、解释业务概念、感谢、与数据无关的闲聊）→ 不需要查库。
3. 追问里省略了主语但意图仍是取数（如"那 5 月呢"）→ 需要查库，必须结合最近对话判断。
4. 拿不准时按需要查库处理。"""


def _build_generate_prompt(state: AgentState) -> str:
    error_hint = state.get("error") or ""
    error_block = (
        f"\n【上一轮生成被驳回，请针对下面的问题修正，不要重复同样的错误】\n{error_hint}"
        if error_hint
        else ""
    )
    return f"""{_SYSTEM}

【最近对话（本轮问题若有省略或指代，以此为准）】
{_history_block(state)}

【用户问题】
{state['question']}

【命中的指标模板（结构化口径）】
{state['retrieved_definitions']}

【已声明的表关系（只允许使用这些连接，禁止臆造连接键）】
{semantics.joins_for_prompt()}

【全局约定】
{semantics.conventions_for_prompt()}

【数据库 schema】
{state['schema_summary']}
{error_block}
要求：
1. 只输出只读查询（SELECT 或 WITH ... SELECT），禁止 INSERT/UPDATE/DELETE/DROP 等写操作。
2. 若上方命中了指标模板，必须选最贴切的一个作为骨架直接采用：其聚合表达式与过滤条件不得改动，
   只允许替换占位符（如 {{date_filter}}），以及在需要按维度拆分时追加已声明的连接和 GROUP BY / ORDER BY。
   不要自己重新推导 GMV 之类的聚合口径。
3. 若未命中模板，才自行编写；但仍须严格遵守已声明的表关系。
4. 只能使用下方 schema 里列出的表和列。需要的表或列没有出现时，就说明无法确定，
   不要凭列名相似去猜——大库里猜错列比查不出更危险。
5. 严禁跨越"一对多"关系去聚合"一"侧的度量列：连接会把"一"侧每一行复制多份，SUM / AVG 会重复计数。
   需要跨粒度分析时，先在子查询或 CTE 里把"多"侧聚合到"一"侧粒度，再连接。
6. 统计订单数、客户数时，一旦连接了明细表，必须用 COUNT(DISTINCT ...) 而不是 COUNT(*)。
7. 仅当缺少必要信息导致无法唯一确定答案时，才把 clarify_needed 设为 true 并给出澄清问题；
   否则一律设为 false 并直接给出 SQL。
8. 结果行数较多时可加 LIMIT。

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
    violations = state.get("violations") or []
    if violations:
        body = (
            "本轮生成的 SQL 被语义校验拦下，没有执行——因为它会踩扇出陷阱，算出的数字是错的"
            "（比真实值偏大）。请如实告诉用户这个查询无法安全完成，并说明原因：\n"
            + "\n".join(v["message"] for v in violations)
        )
    elif error:
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

【采用的指标模板】
{_metrics_titles(state)}

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
    g.add_node("validate", validate_node)
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
        {"answer": "answer", "validate": "validate"},
    )
    g.add_conditional_edges(
        "validate",
        route_after_validate,
        {"generate": "generate", "execute": "execute", "answer": "answer"},
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