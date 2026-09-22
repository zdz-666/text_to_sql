import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict, total=False):
    question: str
    # 检索到的业务口径定义（RAG 输出）
    retrieved_definitions: str
    # 数据库 schema 摘要
    schema_summary: str
    # 生成/执行的 SQL
    sql: str
    reasoning: str
    # 查询结果
    columns: list
    rows: list
    # 执行错误信息（用于回喂重写）
    error: str
    # 已生成 SQL 的次数
    attempt: Annotated[int, operator.add]
    # 需要向用户澄清的标记与问题
    clarify_needed: bool
    clarification: str
    # 最终自然语言回答
    answer: str