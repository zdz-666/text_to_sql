"""schema 注入层：库小就全量给，库大就改成按问题检索。

为什么必须改：公开 benchmark 平均 6.8 张表 / 72.5 列，全量塞进 prompt 没压力；
真实企业库常见 100+ 表 / 上千列，甚至 4 万列。全量注入的问题不只是"塞不进上下文"——
即使上下文装得下，模型也会 attention 到无关列，进而幻觉出不存在的 join。

选表按优先级分三级，逐级兜底：
1. 锚定（确定性）：命中指标模板直接引用到的表。命中 GMV 就必然带上 orders / order_items，
   否则模板根本没法用。这一级不看检索质量，保证"预写 SQL 永远可执行"。
2. 闭包（半确定性）：沿 semantics.json 里已声明的 join 关系扩展 N 跳。
   只走声明的边，所以天然不会把幻觉的 join 带进来。
3. 召回（概率性）：向量检索 + 标识符字面命中，补上问题里点到、但前两级没覆盖的表。
   向量召回做了相关性门控——跨语言时距离会挤成一团，"最近邻"往往纯属噪声，
   这种情况宁可一张不补，因为错的表比没有表更危险。

最后按预算截断；锚定集永不丢弃。
"""

import config
from data_sidekick import db, rag, semantics


def _lexical_hits(question: str, model: list[dict]) -> list[str]:
    """问题里字面出现了某张表名或某个列名的，算强命中。

    英文标识符（orders、discount）被直接写进中文问题时，这比向量相似度可靠得多，
    所以排在向量召回前面。
    """
    lowered = (question or "").lower()
    hits = []
    for t in model:
        names = [t["table"]] + [c["name"] for c in t["columns"]]
        for name in names:
            # 太短的名字（id、o）容易被误命中，要求至少 4 个字符
            if len(name) >= 4 and name.lower() in lowered:
                hits.append(t["table"])
                break
    return hits


def _join_closure(seeds: list[str], hops: int, known: set[str]) -> list[str]:
    """沿已声明的 join 关系做 N 跳扩展。"""
    if hops <= 0:
        return []
    adjacency: dict[str, list[str]] = {}
    for j in semantics.joins():
        a, b = j["from_table"], j["to_table"]
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)

    seen = set(seeds)
    frontier = list(seeds)
    for _ in range(hops):
        nxt = []
        for table in frontier:
            for neighbor in adjacency.get(table, []):
                if neighbor in known and neighbor not in seen:
                    seen.add(neighbor)
                    nxt.append(neighbor)
        frontier = nxt
    return [t for t in frontier]


def _metric_anchors(metrics: list[dict], known: set[str]) -> list[str]:
    """命中指标涉及的表，保持稳定顺序（先到的指标优先）。"""
    anchors, seen = [], set()
    for m in metrics or []:
        for table in sorted(semantics.metric_tables(m)):
            if table in known and table not in seen:
                seen.add(table)
                anchors.append(table)
    return anchors


def _recall(question: str, k: int) -> list[str]:
    """向量召回，带相关性门控。

    跨语言时这一步很容易变成噪声源：中文问题对英文标识符文档，
    embedding 距离会挤成一团（实测候选全部落在 1.6 以上，真正相关的 orders 排第 5），
    此时"最近邻"没有任何意义。宁可什么都不给——错的表比没有表更危险。

    门控条件：最相似的表必须足够近（绝对阈值），且其它候选要在它的相对带宽内。
    阈值与 embedding 模型强相关，换多语言模型后需要重新标定。
    """
    hits = rag.retrieve_tables(question, k=k)
    if not hits:
        return []
    hits.sort(key=lambda pair: pair[1])
    best = hits[0][1]
    if best > config.SCHEMA_RECALL_MAX_DISTANCE:
        return []
    limit = best * config.SCHEMA_RECALL_DISTANCE_RATIO
    return [name for name, dist in hits if dist <= limit]


def select(question: str, metrics: list[dict] | None = None) -> dict:
    """决定这轮把哪些表注入 prompt。返回 {"mode", "text", "tables", "stats"}。"""
    model = db.get_schema()
    if not model:
        return {"mode": "full", "text": "", "tables": [], "stats": _stats(0, 0, 0, 0, "full")}

    total_tables = len(model)
    total_columns = sum(len(t["columns"]) for t in model)

    # 小库：维持原来的全量行为，不引入检索噪声
    if (total_tables <= config.SCHEMA_FULL_MAX_TABLES
            and total_columns <= config.SCHEMA_FULL_MAX_COLUMNS):
        return {
            "mode": "full",
            "text": db.get_schema_summary(),
            "tables": [t["table"] for t in model],
            "stats": _stats(total_tables, total_columns, total_tables, total_columns, "full"),
        }

    known = {t["table"] for t in model}
    by_name = {t["table"]: t for t in model}

    planned: list[str] = []
    seen: set[str] = set()

    def add(names):
        for name in names:
            if name in known and name not in seen:
                seen.add(name)
                planned.append(name)

    # 1) 锚定：指标模板涉及的表，无条件保留
    anchors = _metric_anchors(metrics or [], known)
    add(anchors)
    # 2) 闭包：沿声明关系扩展
    add(_join_closure(anchors, config.SCHEMA_JOIN_HOPS, known))
    # 3) 召回：字面命中优先，再向量召回（带相关性门控）
    add(_lexical_hits(question, model))
    add(_recall(question, k=config.SCHEMA_TOP_K_TABLES))

    # 按预算截断：锚定表不参与淘汰（否则模板失效）
    anchor_set = set(anchors)
    kept, used_columns = list(anchors), sum(len(by_name[t]["columns"]) for t in anchors)
    for name in planned:
        if name in anchor_set:
            continue
        if len(kept) >= config.SCHEMA_TOP_K_TABLES:
            break
        if used_columns + len(by_name[name]["columns"]) > config.SCHEMA_FULL_MAX_COLUMNS:
            continue
        kept.append(name)
        used_columns += len(by_name[name]["columns"])

    kept.sort(key=lambda n: planned.index(n))
    kept_columns = sum(len(by_name[t]["columns"]) for t in kept)
    text = _render(kept, total_tables, total_columns, kept_columns)
    return {
        "mode": "retrieved",
        "text": text,
        "tables": kept,
        "stats": _stats(total_tables, total_columns, len(kept), kept_columns, "retrieved"),
    }


def _stats(total_tables, total_columns, injected_tables, injected_columns, mode) -> dict:
    return {
        "mode": mode,
        "tables_total": total_tables,
        "columns_total": total_columns,
        "tables_injected": injected_tables,
        "columns_injected": injected_columns,
    }


def _render(tables: list[str], total_tables: int, total_columns: int,
            kept_columns: int) -> str:
    header = (
        f"-- 本库共 {total_tables} 张表 / {total_columns} 列，已按问题检索出相关子集："
        f"{len(tables)} 张表 / {kept_columns} 列\n"
        f"-- 只允许使用下面列出的表和列；需要的东西若不在这里，说明无法确定，"
        f"不要凭列名相似去猜表或列。"
    )
    return header + "\n" + "\n".join(db.render_tables(tables))