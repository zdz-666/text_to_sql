import sqlite3
from contextlib import contextmanager

import config


def _readonly_uri() -> str:
    """SQLite 只读 URI：file 路径 + mode=ro。

    每次调用时现算，不在 import 期固化——否则切换 DB_PATH（换库、测试）会失效。
    Windows 下须把反斜杠换成正斜杠，否则 URI 会被当成转义序列导致打不开库。
    """
    return f"file:{config.DB_PATH.replace(chr(92), '/')}?mode=ro"


@contextmanager
def get_readonly_conn():
    conn = sqlite3.connect(_readonly_uri(), uri=True, timeout=5)
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


_schema_cache: list[dict] | None = None


def get_schema(force: bool = False) -> list[dict]:
    """结构化 schema 模型：[{"table": str, "columns": [{"name": str, "type": str}]}]。

    带进程内缓存：大库每轮问数都要 PRAGMA 上百次表结构，没必要反复做。
    DDL 变更后调用 clear_schema_cache()。
    """
    global _schema_cache
    if _schema_cache is None or force:
        with get_readonly_conn() as conn:
            tables = []
            for name in _list_tables(conn):
                cur = conn.execute(f'PRAGMA table_info("{name}")')
                tables.append({
                    "table": name,
                    "columns": [{"name": r[1], "type": r[2]} for r in cur.fetchall()],
                })
        _schema_cache = tables
    return _schema_cache


def clear_schema_cache() -> None:
    global _schema_cache
    _schema_cache = None


def schema_stats() -> tuple[int, int]:
    """返回 (表数, 列数)。"""
    model = get_schema()
    return len(model), sum(len(t["columns"]) for t in model)


def _render_table(t: dict) -> str:
    cols = ", ".join(f"{c['name']} {c['type']}" for c in t["columns"])
    return f"{t['table']}({cols})"


def get_schema_summary() -> str:
    """全量 schema 文本。

    小库（公开 benchmark 平均 6.8 表 / 72.5 列）直接用它就够了；
    大库请改用 schema.select()——全量注入不只是塞不下，即使塞得下，
    模型也会 attention 到无关列，进而幻觉出不存在的 join。
    """
    return "\n".join(_render_table(t) for t in get_schema())


def render_tables(names: list[str]) -> list[str]:
    """按给定顺序渲染指定表的定义（不在库中的名字会被跳过）。"""
    by_name = {t["table"]: t for t in get_schema()}
    return [_render_table(by_name[n]) for n in names if n in by_name]


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