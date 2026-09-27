import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict, total=False):
    question: str
    # 短期记忆：从会话文件里读出的最近 k 条消息，回注给模型
    history: list
    # 路由判定：本轮是否需要查库。false 时跳过检索与执行，直接回答
    needs_sql: bool
    # 命中的结构化指标（含 sql_template / requires_joins），来自语义层
    matched_metrics: list
    # 检索到的业务口径定义（供 prompt 与回答引用的文本形式）
    retrieved_definitions: str
    # 数据库 schema 摘要（小库为全量；大库为检索出的相关子集）
    schema_summary: str
    # schema 注入模式："full"（全量）或 "retrieved"（检索）
    schema_mode: str
    # 生成/执行的 SQL
    sql: str
    reasoning: str
    # 查询结果
    columns: list
    rows: list
    # 上一轮失败原因（执行报错或扇出陷阱），用于回喂重写
    error: str
    # 扇出陷阱等静态语义校验问题
    violations: list
    # 已生成 SQL 的次数
    attempt: Annotated[int, operator.add]
    # 需要向用户澄清的标记与问题
    clarify_needed: bool
    clarification: str
    # 最终自然语言回答
    answer: str

    # ---- 多步规划（planner）----
    # 路由判定：本轮是否需要拆解。由 route_llm 的第二个问题给出
    needs_plan: bool
    # 子步骤列表，元素 {id, description, depends_on, metrics, tables, output_hint}
    plan: list
    # 拆解说明；planner 输出不可用时记原因，便于展示与排查
    plan_reasoning: str
    # 当前步骤下标（0-based），由 step_collect 推进
    current_step: int
    # "当前这一步"已生成 SQL 的次数。
    # 刻意不使用 attempt：后者是 Annotated[int, operator.add] 全局累加器，
    # 多步下会跨步累积，导致后面的步骤一启动就没有重试预算。
    step_attempt: int
    # 当前步骤专用的口径 / schema 文本与注入模式
    step_definitions: str
    step_schema: str
    step_schema_mode: str
    # 各步执行记录（append-only）
    step_results: Annotated[list, operator.add]
    # 中止信息 {step, kind, description, reason, sql, attempts}
    step_failure: dict