from langgraph.graph import END, START, StateGraph

import config
from data_sidekick import semantics
from data_sidekick.db import execute_query, get_schema
from data_sidekick.llm import extract_json, get_llm, route_llm
from data_sidekick.rag import retrieve_metrics
from data_sidekick.schema import select as select_schema
from data_sidekick.state import AgentState


# ----------------------------- 节点 -----------------------------

def route_node(state: AgentState) -> dict:
    """先判定本轮到底要不要查库：闲聊/概念解释直接回答，省掉检索和执行。

    路由使用硅基流动 Kev-4b 快速决策模型（SystemOne API），
    比通用 chat 模型的意图判定更快、更省 token。
    同一次调用还顺带判定是否需要多步拆解（needs_plan），
    取不到时按 True 兜底（后续 plan 节点会把"其实只有 1 步"回流成单条路径，代价很低）。
    """
    verdict = route_llm(_build_route_state(state))
    return {
        "needs_sql": verdict["needs_sql"],
        "needs_plan": verdict.get("needs_plan", True),
    }


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


# ----------------------------- 多步链路节点 -----------------------------

def plan_node(state: AgentState) -> dict:
    """把复杂问题拆成若干"各自一条 SQL 就能跑完"的子步骤。

    拆解质量依赖 retrieve 已注入的口径模板与 schema，所以它排在 retrieve 之后。
    输出不可用（没解析出步骤 / 超过步数上限）时把 plan 置空并记录原因，
    由 route_after_plan 回流到单条 SQL 路径——不截断执行残缺链路。
    """
    resp = get_llm().invoke(_build_plan_prompt(state))
    parsed = extract_json(resp.content)
    reasoning = str(parsed.get("reasoning") or "").strip()
    raw_steps = parsed.get("steps")

    if isinstance(raw_steps, list) and len(raw_steps) > config.MAX_PLAN_STEPS:
        note = (
            f"planner 给出 {len(raw_steps)} 步，超过上限 {config.MAX_PLAN_STEPS}，"
            "退回单条 SQL 路径。"
        )
        return {
            "plan": [],
            "plan_reasoning": f"{note} 原拆解说明：{reasoning}" if reasoning else note,
            "current_step": 0,
        }

    plan = _normalize_plan(raw_steps)
    if not plan:
        note = "planner 输出不可用（未解析出有效步骤），退回单条 SQL 路径。"
        return {
            "plan": [],
            "plan_reasoning": f"{note} 原拆解说明：{reasoning}" if reasoning else note,
            "current_step": 0,
        }

    return {"plan": plan, "plan_reasoning": reasoning, "current_step": 0}


def step_prepare_node(state: AgentState) -> dict:
    """新步骤边界：准备本步的口径与 schema，清掉上一步残留，重试预算归零。

    这是唯一会重置 step_attempt 的地方。重试回炉走 step_validate / step_execute → step_generate，
    绕过本节点，于是计数只在新步骤边界归零、在同一步内持续累加——正是 per-step 重试预算。
    """
    plan = state.get("plan") or []
    idx = state.get("current_step", 0)
    step = plan[idx] if idx < len(plan) else {}
    names = step.get("metrics") or []

    metrics = _metrics_for(names)
    definitions = "\n\n".join(semantics.metric_for_prompt(m) for m in metrics)

    schema = state.get("schema_summary", "")
    mode = state.get("schema_mode", "")
    if mode == "retrieved":
        # 小库是全量注入，每步 schema 相同，没必要重检索；
        # 大库把「步骤描述 + planner 点名的表名」拼成 query，字面命中那一级会把表名直接认出来。
        query = " ".join([step.get("description", "")] + list(step.get("tables") or [])).strip()
        picked = select_schema(query or state["question"], metrics)
        schema, mode = picked["text"], picked["mode"]

    return {
        "step_definitions": definitions or "（本步未指派指标模板，SQL 依 schema 自行编写）",
        "step_schema": schema,
        "step_schema_mode": mode,
        "step_attempt": 0,
        "sql": "",
        "violations": [],
        "error": "",
        "columns": [],
        "rows": [],
    }


def step_generate_node(state: AgentState) -> dict:
    """为当前这一步生成 SQL。不返回 attempt——全局累加器在多步链路里完全不参与。"""
    resp = get_llm().invoke(_build_step_generate_prompt(state))
    parsed = extract_json(resp.content)
    return {
        "sql": parsed.get("sql", ""),
        "reasoning": parsed.get("reasoning", ""),
        "clarify_needed": bool(parsed.get("clarify_needed", False)),
        "clarification": parsed.get("clarification", ""),
        "step_attempt": state.get("step_attempt", 0) + 1,
    }


def step_validate_node(state: AgentState) -> dict:
    """独立节点但只做一行委派：这样 route_after_validate 可以逐字节保持不变。

    已知边界：validate_sql 是**单语句内**的检查，跨步骤的正确性它管不到。
    本方案不做 CTE 拼接、步骤之间只传文字摘要，跨步数据血缘并不存在，
    所以这类错误不会通过数据传递发生；真正的风险是模型"把前一步的数字再拿去聚合"，
    由 planner 规则 7 与 step_generate prompt 的要求 3 两道文案约束兜着，不额外加代码。
    """
    return validate_node(state)


def step_execute_node(state: AgentState) -> dict:
    """同上：委派 execute_node，换取 route_after_execute 不变。"""
    return execute_node(state)


def step_collect_node(state: AgentState) -> dict:
    """记录本步产出并推进到下一位。step_results 走 append-only reducer。"""
    plan = state.get("plan") or []
    idx = state.get("current_step", 0)
    step = plan[idx] if idx < len(plan) else {}
    rows = state.get("rows") or []
    record = {
        "id": step.get("id", idx + 1),
        "description": step.get("description", ""),
        "output_hint": step.get("output_hint", ""),
        "status": "ok",
        "sql": state.get("sql", ""),
        "columns": state.get("columns") or [],
        "rows": rows,
        "row_count": len(rows),
        "truncated": len(rows) >= config.MAX_ROWS,
        "attempts": state.get("step_attempt", 0),
        "error": "",
    }
    return {"step_results": [record], "current_step": idx + 1}


def step_fail_node(state: AgentState) -> dict:
    """中止整条链，把"哪一步、哪一类原因"固化下来，交给 step_answer 如实汇报。"""
    plan = state.get("plan") or []
    idx = state.get("current_step", 0)
    step = plan[idx] if idx < len(plan) else {}
    if state.get("clarify_needed"):
        kind = "clarify"
        reason = state.get("clarification") or "信息不足，无法确定这一步的口径。"
    elif state.get("violations"):
        kind = "fan_trap"
        reason = "\n".join(v["message"] for v in state["violations"])
    else:
        kind = "error"
        reason = state.get("error") or "SQL 执行失败。"

    step_id = step.get("id", idx + 1)
    record = {
        "id": step_id,
        "description": step.get("description", ""),
        "output_hint": step.get("output_hint", ""),
        "status": "failed",
        "sql": state.get("sql", ""),
        "columns": [],
        "rows": [],
        "row_count": 0,
        "truncated": False,
        "attempts": state.get("step_attempt", 0),
        "error": reason,
    }
    failure = {
        "step": step_id,
        "kind": kind,
        "description": step.get("description", ""),
        "reason": reason,
        "sql": state.get("sql", ""),
        "attempts": state.get("step_attempt", 0),
    }
    return {"step_failure": failure, "step_results": [record]}


def step_answer_node(state: AgentState) -> dict:
    """多步链路的收尾：澄清态直接返回澄清问题，否则按各步结果汇报。"""
    if state.get("clarify_needed"):
        return {"answer": state.get("clarification", "请补充信息。")}
    return {"answer": get_llm().invoke(_build_multistep_answer_prompt(state)).content}


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


# ---- 多步链路路由 ----

def route_after_retrieve(state: AgentState) -> str:
    """needs_plan=False 直接进 generate，与改动前的 retrieve → generate 等价。"""
    return "plan" if state.get("needs_plan", True) else "generate"


def route_after_plan(state: AgentState) -> str:
    """只有 1 步（或 planner 输出不可用）时回流单条 SQL 路径。"""
    return "step_prepare" if len(state.get("plan") or []) >= 2 else "generate"


def route_after_step_generate(state: AgentState) -> str:
    if state.get("clarify_needed"):
        return "step_fail"
    return "step_validate"


def route_after_step_validate(state: AgentState) -> str:
    """与 route_after_validate 同构，只是预算换成 per-step 的 step_attempt、终点换成 step_fail。"""
    if state.get("violations"):
        if state.get("step_attempt", 0) < config.MAX_RETRIES:
            return "step_generate"
        return "step_fail"
    return "step_execute"


def route_after_step_execute(state: AgentState) -> str:
    if state.get("error"):
        if state.get("step_attempt", 0) < config.MAX_RETRIES:
            return "step_generate"
        return "step_fail"
    return "step_collect"


def route_after_step_collect(state: AgentState) -> str:
    return "step_prepare" if state.get("current_step", 0) < len(state.get("plan") or []) else "step_answer"


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


# ----------------------------- 多步规划（planner）-----------------------------

def _known_tables() -> set[str]:
    """库里真实存在的表名，用于清洗 planner 点名的 tables。取不到就不清洗。"""
    try:
        return {t["table"] for t in get_schema()}
    except Exception:
        return set()


def _metrics_for(names: list) -> list:
    """把 planner 点名的口径 name 换成语义层里的指标对象（纯字典查表，无向量检索）。"""
    out = []
    for name in names or []:
        metric = semantics.get_metric(str(name))
        if metric:
            out.append(metric)
    return out


def _normalize_plan(raw) -> list:
    """清洗 planner 输出的 steps，返回 1..N 重排后的步骤列表。

    - 非 list → 空（调用方据此回流单条 SQL 路径）
    - 逐条要求是 dict 且 description 非空，否则丢弃
    - metrics 只留语义层里真实存在的 name
    - tables 只留 schema 里真实存在的表（schema 取不到时不清洗）
    - depends_on 只留小于自身 id 的整数（执行是严格顺序的，引用未来步骤无意义）
    """
    if not isinstance(raw, list):
        return []
    known_metrics = {m.get("name") for m in semantics.metrics()}
    known_tables = _known_tables()

    plan = []
    # 故意多取一条，让调用方能区分"刚好等于上限"与"超过上限"
    for item in raw[: config.MAX_PLAN_STEPS + 1]:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "").strip()
        if not description:
            continue

        step_id = len(plan) + 1
        depends = [
            d for d in (item.get("depends_on") or [])
            if isinstance(d, int) and not isinstance(d, bool) and 1 <= d < step_id
        ]
        metrics = [n for n in (item.get("metrics") or []) if n in known_metrics]
        tables = [
            t for t in (item.get("tables") or [])
            if not known_tables or t in known_tables
        ]
        plan.append({
            "id": step_id,
            "description": description,
            "depends_on": depends,
            "metrics": metrics,
            "tables": tables,
            "output_hint": str(item.get("output_hint") or "").strip(),
        })
    return plan


def _metrics_catalog() -> str:
    """列出全部预写口径的 name 与 title，供 planner 精确指派到某一步。"""
    items = semantics.metrics()
    if not items:
        return "（本库未定义任何预写口径）"
    return "\n".join(
        f"- {m.get('name')}：{m.get('title', '')}" for m in items
    )


def _build_plan_prompt(state: AgentState) -> str:
    return f"""{_SYSTEM}

请判断下面这个问题是否需要拆成多个"先后依赖"的子步骤来回答，并给出拆解方案。

【最近对话（本轮问题若有省略或指代，以此为准）】
{_history_block(state)}

【用户问题】
{state['question']}

【命中的指标模板（结构化口径）】
{state['retrieved_definitions']}

【可指派的口径清单（metrics 只能从这里选 name）】
{_metrics_catalog()}

【已声明的表关系（只允许使用这些连接，禁止臆造连接键）】
{semantics.joins_for_prompt()}

【全局约定】
{semantics.conventions_for_prompt()}

【数据库 schema】
{state['schema_summary']}

拆解规则：
1. 只有"后一步依赖前一步的结果"时才拆；一次查询（含 CTE 子查询）就能答完的，只输出 1 步。
2. 每一步必须是一条独立 SELECT / WITH 就能跑完的取数任务，不能是"再想想""校验一下"这类非取数动作。
3. 每步 description 必须自包含：时间范围、维度、过滤条件写全。
   系统不会把前序数据拼进本步 SQL，前序结果只是给你看的文字摘要。
4. 最多 {config.MAX_PLAN_STEPS} 步；能合并就合并。
5. metrics 只能从上面的口径清单里选 name，没有合适的就留空数组。
6. depends_on 只能填更早步骤的 id，第一步必须是空数组。
7. 严禁设计"把前一步算出的数字再参与聚合"的步骤（例如用上一步的 GMV 再算环比），
   这类计算必须在同一步 SQL 里完成。

只返回一个 JSON 对象（不要任何多余文字），格式：
{{"reasoning": "为什么这样拆", "steps": [{{"id": 1, "description": "第 1 步要取什么数", "depends_on": [], "metrics": ["gmv"], "tables": ["orders"], "output_hint": "这一步应产出什么（列名 + 形态 + 大致行数）"}}]}}"""


_STEP_ROW_MAX_CHARS = 200  # 防单行超宽吞掉整块摘要


def _clip(value, limit: int) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _format_one_step(result: dict, rows_limit: int, max_chars: int) -> str:
    lines = [f"第 {result.get('id')} 步：{result.get('description', '')}"]
    if result.get("output_hint"):
        lines.append(f"  应产出：{result['output_hint']}")

    if result.get("status") != "ok":
        lines.append(f"  状态：执行失败（{result.get('error') or '未说明原因'}）")
        text = "\n".join(lines)
    else:
        rows = result.get("rows") or []
        lines.append(f"  列名：{result.get('columns') or []}")
        if not rows:
            lines.append("  结果：0 行（查询成功但无数据）")
        else:
            lines.append(
                f"  结果：共返回 {len(rows)} 行（已按结果上限截断，真实总行数未知），"
                f"前 {min(rows_limit, len(rows))} 行："
            )
            for row in rows[:rows_limit]:
                lines.append("    " + _clip(row, _STEP_ROW_MAX_CHARS))
        if result.get("sql"):
            lines.append(f"  SQL：{_clip(result['sql'], max_chars)}")
        text = "\n".join(lines)

    if len(text) > max_chars:
        # 截断时先丢 SQL 尾巴而不是丢数据——下一步最需要"前一步给出了什么数、叫什么列"
        text = text[:max_chars] + "…（本步摘要已截断）"
    return text


def _format_step_results(
    results: list,
    rows_limit: int,
    max_chars: int,
    total_max_chars: int,
) -> str:
    """把已完成步骤压成紧凑文本，注入后续步骤 / 最终汇报的 prompt。

    总量超预算时从**最早的一步**整块丢弃：越靠近当前步的结果越可能被引用。
    """
    if not results:
        return ""
    blocks = [_format_one_step(r, rows_limit, max_chars) for r in results]

    dropped = 0
    while len(blocks) > 1 and sum(len(b) for b in blocks) > total_max_chars:
        blocks.pop(0)
        dropped += 1

    text = "\n\n".join(blocks)
    if dropped:
        text = f"（更早的 {dropped} 步摘要已省略）\n{text}"
    return text


def _build_step_generate_prompt(state: AgentState) -> str:
    """给多步链路里的"当前这一步"生成 SQL。

    与前序结果的唯一关系是文字摘要：本步 SQL 必须靠自己写全筛选条件，
    不能指望前序数据被拼进来。
    """
    plan = state.get("plan") or []
    idx = state.get("current_step", 0)
    step = plan[idx] if idx < len(plan) else {}
    prior = _format_step_results(
        state.get("step_results") or [],
        config.STEP_SUMMARY_ROWS,
        config.STEP_SUMMARY_MAX_CHARS,
        config.STEP_SUMMARY_TOTAL_MAX_CHARS,
    ) or "（这是第一步，没有前序结果）"
    depends = step.get("depends_on") or []
    depends_text = "、".join(f"第 {d} 步" for d in depends) if depends else "无"
    error_hint = state.get("error") or ""
    error_block = (
        f"\n【上一轮生成被驳回，请针对下面的问题修正，不要重复同样的错误】\n{error_hint}"
        if error_hint
        else ""
    )
    return f"""{_SYSTEM}

这是一个多步取数任务的其中一步，你只需要完成"当前这一步"。

【最近对话（本轮问题若有省略或指代，以此为准）】
{_history_block(state)}

【用户原始问题】
{state['question']}

【整体拆解思路】
{state.get('plan_reasoning') or '（未提供）'}

【当前是第 {step.get('id', idx + 1)} 步 / 共 {len(plan)} 步】
{step.get('description', '')}
本步应产出：{step.get('output_hint') or '（未指定）'}
本步依赖：{depends_text}

【前序步骤的结果摘要（仅供理解上下文与对齐口径，不要把这些数字再拿去聚合）】
{prior}

【本步骤适用的指标模板（结构化口径）】
{state.get('step_definitions') or '（本步未指派指标模板）'}

【已声明的表关系（只允许使用这些连接，禁止臆造连接键）】
{semantics.joins_for_prompt()}

【全局约定】
{semantics.conventions_for_prompt()}

【数据库 schema】
{state.get('step_schema', '')}
{error_block}
要求：
1. 只输出当前这一步的只读查询（SELECT 或 WITH ... SELECT），禁止 INSERT/UPDATE/DELETE/DROP 等写操作。
2. 只完成上面"当前步骤"的任务，不要顺手把后面几步一起写了。
3. 前序摘要里的数字只是参考信息，严禁把它们写成常量再参与聚合或过滤；
   需要复用前序结论时，请在本步 SQL 里重新用子查询 / CTE 算出来。
4. 若上方指派了指标模板，必须选最贴切的一个作为骨架直接采用：其聚合表达式与过滤条件不得改动，
   只允许替换占位符（如 {{date_filter}}），以及在需要按维度拆分时追加已声明的连接和 GROUP BY / ORDER BY。
5. 只能使用下方 schema 里列出的表和列。需要的表或列没有出现时，就说明无法确定，
   不要凭列名相似去猜——大库里猜错列比查不出更危险。
6. 严禁跨越"一对多"关系去聚合"一"侧的度量列：连接会把"一"侧每一行复制多份，SUM / AVG 会重复计数。
   需要跨粒度分析时，先在子查询或 CTE 里把"多"侧聚合到"一"侧粒度，再连接。
7. 统计订单数、客户数时，一旦连接了明细表，必须用 COUNT(DISTINCT ...) 而不是 COUNT(*)。
8. 本步 description 里的时间范围、维度、过滤条件要全部落到 SQL 里，不要因为它们出现在前序步骤就省略。
9. 结果行数较多时可加 LIMIT。
10. 仅当缺少必要信息导致本步无法唯一确定时，才把 clarify_needed 设为 true 并给出澄清问题；
    否则一律设为 false 并直接给出 SQL。

只返回一个 JSON 对象（不要任何多余文字），格式：
{{"clarify_needed": false, "clarification": "", "sql": "SELECT ...", "reasoning": "简要说明"}}"""


def _build_multistep_answer_prompt(state: AgentState) -> str:
    """多步链路结束（全部成功或被中止）后的最终汇报。"""
    failure = state.get("step_failure") or {}
    if failure:
        fail_block = (
            f"\n【中止信息】\n第 {failure.get('step')} 步「{failure.get('description')}」最终失败"
            f"（类型：{failure.get('kind')}，已尝试 {failure.get('attempts')} 次），"
            f"整条链在此中止，后续步骤没有执行。原因：{failure.get('reason')}\n"
            "汇报时必须如实说明哪一步失败、为什么失败、已完成部分能得出什么结论、"
            "哪些结论因为链断掉而不能给。"
        )
    else:
        fail_block = ""
    summary = _format_step_results(
        state.get("step_results") or [],
        20,
        12000,
        12000,
    ) or "（没有任何步骤产出结果）"
    return f"""{_SYSTEM}

请把下面的多步取数结果用自然、清晰的中文汇报给业务用户。

【最近对话】
{_history_block(state)}

【用户问题】
{state['question']}

【采用的指标模板】
{_metrics_titles(state)}

【依据的口径】
{state['retrieved_definitions']}

【拆解思路】
{state.get('plan_reasoning') or '（未提供）'}

【各步骤执行结果】
{summary}
{fail_block}

汇报要求：先给出结论，再按步骤说明每一步查了什么、得到什么，最后附上各步执行的 SQL。
不要在正文里堆原始表格，只挑与结论相关的关键数字。"""


# ----------------------------- 组装图 -----------------------------

def build_graph():
    g = StateGraph(AgentState)
    g.add_node("route", route_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("plan", plan_node)
    g.add_node("generate", generate_node)
    g.add_node("validate", validate_node)
    g.add_node("execute", execute_node)
    g.add_node("answer", answer_node)
    g.add_node("step_prepare", step_prepare_node)
    g.add_node("step_generate", step_generate_node)
    g.add_node("step_validate", step_validate_node)
    g.add_node("step_execute", step_execute_node)
    g.add_node("step_collect", step_collect_node)
    g.add_node("step_fail", step_fail_node)
    g.add_node("step_answer", step_answer_node)

    g.add_edge(START, "route")
    g.add_conditional_edges(
        "route",
        route_after_route,
        {"retrieve": "retrieve", "answer": "answer"},
    )
    # 原先的 retrieve → generate 变成条件边：needs_plan=False 时二者完全等价
    g.add_conditional_edges(
        "retrieve",
        route_after_retrieve,
        {"plan": "plan", "generate": "generate"},
    )
    g.add_conditional_edges(
        "plan",
        route_after_plan,
        {"step_prepare": "step_prepare", "generate": "generate"},
    )
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

    # 多步链路
    g.add_edge("step_prepare", "step_generate")
    g.add_conditional_edges(
        "step_generate",
        route_after_step_generate,
        {"step_fail": "step_fail", "step_validate": "step_validate"},
    )
    g.add_conditional_edges(
        "step_validate",
        route_after_step_validate,
        {"step_generate": "step_generate", "step_execute": "step_execute", "step_fail": "step_fail"},
    )
    g.add_conditional_edges(
        "step_execute",
        route_after_step_execute,
        {"step_generate": "step_generate", "step_collect": "step_collect", "step_fail": "step_fail"},
    )
    g.add_conditional_edges(
        "step_collect",
        route_after_step_collect,
        {"step_prepare": "step_prepare", "step_answer": "step_answer"},
    )
    g.add_edge("step_fail", "step_answer")
    g.add_edge("step_answer", END)

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
        "needs_plan": False,
        "plan": [],
        "current_step": 0,
        "step_attempt": 0,
        "step_results": [],
    }
    return build_graph().invoke(initial)