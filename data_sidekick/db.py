"""数据层：只读连接、schema 摘要、安全的 SQL 执行。

安全设计（面试常问）：
1. 用 SQLite 的只读 URI（mode=ro）打开连接——即便 SQL 里有写操作，数据库层面也会拒绝。
2. execute_query 之前再做一道白名单校验：只允许 SELECT / WITH 开头。
3. 限制返回行数（MAX_ROWS），避免一次性拉爆内存。
"""

import sqlite3
from contextlib import contextmanager

import config

# SQLite 只读 URI：file 路径 + mode=ro。
# Windows 下须把反斜杠换成正斜杠，否则 URI 会被当成转义序列导致打不开库。
_READONLY_URI = f"file:{config.DB_PATH.replace(chr(92), '/')}?mode=ro"


@contextmanager
def get_readonly_conn():
    conn = sqlite3.connect(_READONLY_URI, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _list_tables(conn) -> list[str]:
    cur = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [r[0] for r in cur.fetchall()]


def get_schema_summary() -> str:
    """把所有表名 + 列名 + 类型压成一段紧凑文本，供 LLM 理解库结构。"""
    with get_readonly_conn() as conn:
        parts = []
        for table in _list_tables(conn):
            cur = conn.execute(f'PRAGMA table_info("{table}")')
            cols = [f"{r[1]} {r[2]}" for r in cur.fetchall()]
            parts.append(f"{table}({', '.join(cols)})")
        return "\n".join(parts)


def is_safe_sql(sql: str) -> bool:
    stripped = sql.strip().lstrip().rstrip(";").lstrip()
    upper = stripped.upper()
    return upper.startswith("SELECT") or upper.startswith("WITH")


def execute_query(sql: str) -> tuple[list, list]:
    """执行只读查询，返回 (列名列表, 行列表)。"""
    if not is_safe_sql(sql):
        raise ValueError("仅允许 SELECT / WITH 只读查询")

    with get_readonly_conn() as conn:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchmany(config.MAX_ROWS)]
    return columns, rows