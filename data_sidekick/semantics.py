"""语义层：声明式 join 关系 + 结构化指标字典。

解决的问题：真实仓库（尤其 Fivetran 复制的 Salesforce / ServiceNow）常常没有任何外键，
连接关系只存在于"某个人脑子里"。模型只能靠列名相似度猜，于是频繁踩到扇出陷阱（fan trap）：
一个订单对应多条明细，join 之后对订单级度量求和会把每一行重复计数——
SQL 合法、schema 合法、数字是错的，而且没人看得出来。这是最危险的一类失败。

本模块做三件事：
1. 加载 semantics.json 里显式声明的 join 关系（含基数）与结构化指标 {name, sql_template, requires_joins}；
2. 把这些声明渲染进 prompt，命中指标时直接给出预写 SQL，不让 LLM 现场推理聚合表达式与连接路径；
3. validate_sql() 用声明的关系静态检查候选 SQL 是否踩了扇出陷阱，把"静默算错"变成"可拦截的错误"。
"""

import json
import re
from functools import lru_cache

import config

# SQL 里的表引用：FROM t [AS] a / JOIN t [AS] a
_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_]\w*)(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?",
    re.IGNORECASE,
)
_AGG_RE = re.compile(r"\b(SUM|AVG|MIN|MAX|COUNT|TOTAL)\s*\(", re.IGNORECASE)
_SELECT_RE = re.compile(r"\bSELECT\b", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

# 别名位上出现这些词说明它其实是关键字，不是别名
_RESERVED = {
    "on", "where", "group", "order", "having", "limit", "join", "inner", "left",
    "right", "full", "outer", "cross", "union", "and", "or", "as", "select",
    "with", "case", "when", "then", "else", "end", "by", "not", "in", "is",
}

# 占位符缺省值：{date_filter} 未提供时退化为"不过滤时间"
_DEFAULT_PARAMS = {"date_filter": "1=1"}


@lru_cache(maxsize=1)
def load() -> dict:
    """读取并缓存 semantics.json。文件缺失或损坏时退化成空语义层，不阻断问数。"""
    try:
        with open(config.SEMANTICS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    for key in ("joins", "conventions", "metrics"):
        data.setdefault(key, [])
    return data


def joins() -> list[dict]:
    return load()["joins"]


def metrics() -> list[dict]:
    return load()["metrics"]


def conventions() -> list[str]:
    return load()["conventions"]


def get_metric(name: str) -> dict | None:
    for m in metrics():
        if m.get("name") == name:
            return m
    return None


def get_join(join_id: str) -> dict | None:
    for j in joins():
        if j.get("id") == join_id:
            return j
    return None


# ----------------------------- 渲染 -----------------------------

def render(metric: dict, params: dict | None = None) -> str:
    """把模板里的占位符替换成实际值，得到可直接执行的 SQL。

    只用于模板自检（seed_data 会逐条渲染并试跑）与需要确定性 SQL 的场景；
    常规问数由 LLM 在模板骨架上增补维度，这里不参与。
    """
    template = metric.get("sql_template") or ""
    values = dict(_DEFAULT_PARAMS)
    values.update(params or {})
    slots = set(_PLACEHOLDER.findall(template))
    return template.format(**{s: values.get(s, _DEFAULT_PARAMS.get(s, "1=1")) for s in slots})


def metric_for_prompt(metric: dict) -> str:
    lines = [
        f"指标：{metric.get('title', metric.get('name'))}（id={metric.get('name')}）",
        f"别名：{'、'.join(metric.get('aliases', [])) or '（无）'}",
        f"口径：{metric.get('description', '')}",
        f"结果粒度：{metric.get('grain', '未声明')}",
    ]
    used = metric.get("requires_joins") or []
    if used:
        lines.append("使用的已声明连接：" + "、".join(used))
    else:
        lines.append("使用的已声明连接：无（单表即可完成，不要额外连接）")
    lines.append("SQL 模板（必须直接采用其聚合表达式与过滤条件）：")
    lines.append((metric.get("sql_template") or "").strip())
    return "\n".join(lines)


def tables_in_sql(sql: str) -> set[str]:
    """从 SQL 文本里提取引用的表名。

    schema 检索要用它做"锚定"：命中某个指标模板时，模板里出现的表必须无条件
    出现在注入给模型的 schema 子集里，否则模板就没法用。
    """
    return set(_tables(sql).values())


def metric_tables(metric: dict) -> set[str]:
    """一个指标涉及的全部表：模板里引用的 + 其声明 join 的两端。"""
    names = tables_in_sql(metric.get("sql_template", "") or "")
    for join_id in metric.get("requires_joins") or []:
        join = get_join(join_id)
        if join:
            names.update({join["from_table"], join["to_table"]})
    return names


def joins_for_prompt() -> str:
    """把声明的 join 关系连同扇出风险渲染成 prompt 片段。"""
    rows = []
    for j in joins():
        one = _one_side(j)
        if one is None:
            risk = "无（一对一连接不产生行复制）。"
        else:
            risk = (
                f"*{j['from_table']}* 与 *{j['to_table']}* 同时出现时，"
                f"{one} 的每一行会被复制多份，对 {one} 的度量列做 SUM/AVG 会重复计数。"
            )
        rows.append(
            f"- {j['from_table']}.{j['from_column']} = {j['to_table']}.{j['to_column']}"
            f"（{_cardinality_text(j)}：{j.get('note', '')}）\n"
            f"  扇出风险：{risk}"
        )
    return "\n".join(rows) or "（未声明任何连接关系）"


def conventions_for_prompt() -> str:
    return "\n".join(f"- {c}" for c in conventions()) or "（无额外约定）"


# ----------------------------- 扇出检查 -----------------------------

def _one_side(join: dict) -> str | None:
    """返回 join 之后会被复制多份的那张表（基数中为「一」的一侧）。

    一对一连接不产生行复制，没有扇出一侧，返回 None，调用方应跳过该连接的扇出检查。
    基数未声明时保守处理，按「一对多」认定 from_table 会被复制（宁可多提醒一次）。
    """
    cardinality = join.get("cardinality")
    if cardinality == "many_to_one":
        return join["to_table"]
    if cardinality == "one_to_one":
        return None
    return join["from_table"]


def _cardinality_text(join: dict) -> str:
    return {
        "many_to_one": "多对一",
        "one_to_many": "一对多",
        "one_to_one": "一对一",
    }.get(join.get("cardinality", ""), join.get("cardinality", "未声明"))


def _depths(sql: str) -> list[int]:
    """逐字符记录括号深度，用于切分 SELECT 作用域。"""
    depth, out = 0, []
    for ch in sql:
        if ch == "(":
            out.append(depth)
            depth += 1
        elif ch == ")":
            depth -= 1
            out.append(max(depth, 0))
        else:
            out.append(depth)
    return out


def _scopes(sql: str) -> list[tuple[int, int]]:
    """切出每个 SELECT 的作用域 [start, end)。

    一个作用域的结束点是：同深度处的下一个 SELECT（UNION 的兄弟分支），
    或深度首次变浅的位置（跳出所在的括号）。
    """
    depths = _depths(sql)
    selects = [m.start() for m in _SELECT_RE.finditer(sql)]
    scopes = []
    for pos in selects:
        depth = depths[pos]
        end = len(sql)
        for other in selects:
            if other > pos and depths[other] == depth:
                end = other
                break
        for i in range(pos + 1, len(sql)):
            if depths[i] < depth:
                end = min(end, i)
                break
        scopes.append((pos, end))
    return scopes


def _own_text(sql: str, scope: tuple[int, int], scopes: list[tuple[int, int]]) -> str:
    """作用域自身的文本：挖掉嵌套子查询，避免把子查询里的聚合算到外层头上。

    这一步是避免误报的关键：WITH x AS (SELECT SUM(...) ...) SELECT ... FROM x
    里内层的聚合属于内层作用域，与外层的 join 无关。
    """
    start, end = scope
    nested = sorted((a, b) for a, b in scopes if a > start and b <= end)
    if not nested:
        return sql[start:end]
    pieces, cursor = [], start
    for a, b in nested:
        if a < cursor:
            continue
        pieces.append(sql[cursor:a])
        cursor = max(cursor, b)
    pieces.append(sql[cursor:end])
    return " ".join(pieces)


def _tables(text: str) -> dict[str, str]:
    """提取 别名 -> 表名 的映射。没有别名时用表名自己当别名。"""
    out: dict[str, str] = {}
    for m in _TABLE_RE.finditer(text):
        table, alias = m.group(1), m.group(2)
        if not alias or alias.lower() in _RESERVED:
            alias = table
        out[alias] = table
    return out


def _aggregates(text: str) -> list[tuple[str, str]]:
    """提取 (聚合函数名, 参数原文)，参数按括号配平截取。"""
    out = []
    for m in _AGG_RE.finditer(text):
        start = m.end() - 1  # 指向左括号
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    out.append((m.group(1).upper(), text[start + 1:i]))
                    break
    return out


def validate_sql(sql: str) -> list[dict]:
    """静态检查候选 SQL 的扇出陷阱，返回问题列表（空列表表示通过）。

    判定规则：在同一个 SELECT 作用域内，若某条已声明关系的两张表同时出现，
    而该作用域又对「一」侧表的列做了聚合，即为扇出陷阱。
    COUNT(DISTINCT x) 免疫行复制，视为安全。
    """
    if not sql or not joins():
        return []

    scopes = _scopes(sql)
    issues: list[dict] = []
    seen: set[tuple] = set()

    for scope in scopes:
        text = _own_text(sql, scope, scopes)
        alias_map = _tables(text)
        present = set(alias_map.values())
        if len(present) < 2:
            continue
        aggregates = _aggregates(text)

        for join in joins():
            if join["from_table"] not in present or join["to_table"] not in present:
                continue
            one = _one_side(join)
            if one is None:
                continue  # 一对一连接不产生行复制，不存在扇出
            one_aliases = [a for a, t in alias_map.items() if t == one]

            for func, arg in aggregates:
                if arg.strip().upper().startswith("DISTINCT"):
                    continue  # 去重计数不受行复制影响
                for alias in one_aliases:
                    if not re.search(rf"\b{re.escape(alias)}\s*\.", arg):
                        continue
                    key = (join["id"], one, func, alias)
                    if key in seen:
                        continue
                    seen.add(key)
                    issues.append({
                        "kind": "fan_trap",
                        "join": join["id"],
                        "table": one,
                        "aggregate": func,
                        "expression": f"{func}({arg.strip()})",
                        "message": (
                            f"扇出陷阱：查询同时使用了 {join['from_table']} 与 {join['to_table']}，"
                            f"却对「一」侧表 {one} 的度量列执行了 {func}({arg.strip()})。"
                            f"该连接会把 {one} 的每一行复制多份（{join.get('note', '')}），导致重复计数。"
                            f"请先在子查询或 CTE 中把「多」侧聚合到 {one} 的粒度再连接，"
                            f"或改用 COUNT(DISTINCT ...) 去重计数。"
                        ),
                    })
    return issues


def verify_templates() -> list[str]:
    """自检：逐条渲染模板的缺省形态并试跑，尽早发现预写 SQL 里的笔误。

    返回失败说明列表；空列表表示全部通过。由 seed_data 在建库后调用。
    """
    from data_sidekick.db import execute_query

    failures = []
    for metric in metrics():
        name = metric.get("name", "?")
        try:
            execute_query(render(metric))
        except Exception as e:  # noqa: BLE001 - 自检需要把任何异常报出来
            failures.append(f"{name}: {e}")
    return failures