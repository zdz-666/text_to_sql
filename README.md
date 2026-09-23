# DataSidekick

智能体驱动的 Text-to-SQL 助手。用自然语言问数，自动检索业务口径、生成只读 SQL、执行并汇报结果。

## 架构：前后端分离

```
frontend/app.py        Streamlit UI（纯渲染，所有数据通过 HTTP 获取）
backend/server.py      FastAPI 后端（封装 agent + memory，暴露 REST API）
semantics.json         语义层：声明式 join 关系 + 结构化指标字典（唯一真相来源）
data_sidekick/
    agent.py           LangGraph 状态图
    semantics.py       加载声明、渲染模板、扇出陷阱静态校验
    schema.py          schema 注入策略：小库全量 / 大库检索
    llm.py             LLM 封装（OpenAI 兼容接口）
    db.py              只读连接 / schema 内省 / SQL 执行
    rag.py             Chroma 指标索引 + 表索引
    memory.py          会话历史：新建 / 追加 / 读取 / 删除
    state.py           AgentState 定义
config.py             集中配置（全部从 .env / 环境变量读取）
```

## 启动

```bash
# 终端 1 — 后端
uvicorn backend.server:app --reload --port 8000

# 终端 2 — 前端
streamlit run frontend/app.py
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 LLM（兼容任何 OpenAI 风格 API，以 DeepSeek 为例）
cp .env.example .env
# 编辑 .env：填 OPENAI_API_KEY

# 3. 生成示例数据 + 口径库
E:\ANACONDA\envs\langchain_311\python.exe seed_data.py

# 4. 按上面分别启动后端和前端
```

## 核心链路

```
用户提问（+ 从会话文件读出的最近 k 条历史）
     │
     ├─ route      (判定本轮是否需要查库；不需要就直接去 answer)
     ├─ retrieve   (Chroma 检索命中的结构化指标 + 读取 schema)
     ├─ generate   (在指标模板骨架上生成只读 SQL；口径缺失则进入澄清)
     ├─ validate   (用声明的 join 关系静态校验扇出陷阱)
     │      └─ 命中陷阱且未超重试上限 → 回到 generate（带原因重写）
     ├─ execute    (只读执行，失败记录 error)
     │      └─ 出错且未超重试上限 → 回到 generate（自纠错）
     └─ answer     (把结果 + SQL 汇报成自然语言；非取数问题直接闲聊式回答)
                   （后端负责把本轮 user / assistant 两条消息追加进会话文件）
```

## 语义层：声明 join + 指标

真实仓库（尤其 Fivetran 复制的 Salesforce / ServiceNow）通常**没有任何外键**，连接关系只存在于
"某个人脑子里"。模型只能靠列名相似度猜，于是频繁踩到**扇出陷阱（fan trap）**：

> 一个订单对应多条明细。把订单级的优惠金额 `orders.discount` 连接明细表后求和，
> 订单 1 有 2 条明细，100 元就被算成 200 元。SQL 合法、schema 合法、数字是错的，而且没人看得出来。

`semantics.json` 把这类隐性约定显式化，分两部分：

**1. 声明式 join**——每条关系带基数，扇出风险因此可被机器推导：

```json
{
  "id": "order_items_orders",
  "from_table": "order_items", "from_column": "order_id",
  "to_table": "orders",        "to_column": "order_id",
  "cardinality": "many_to_one",
  "note": "一个订单有多条明细，一条明细只属于一个订单"
}
```

**2. 结构化指标**——从"一段文字"升级成 `{name, sql_template, requires_joins, grain}`。
命中时直接采用预写的聚合表达式与过滤条件，不再让 LLM 现场推理：

```json
{
  "name": "gmv",
  "aliases": ["GMV", "销售额", "成交额", "营收"],
  "grain": "order_item",
  "requires_joins": ["order_items_orders"],
  "sql_template": "SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0) AS gmv\nFROM order_items oi\nJOIN orders o ON oi.order_id = o.order_id\nWHERE o.status = 'paid'\n  AND {date_filter}"
}
```

模板里的 `COUNT(DISTINCT o.order_id)` 这类写法，本身就是把正确粒度固化下来——口径一旦写对一次，
后续每次命中都不会退化。

**静态防线**：`semantics.validate_sql()` 在 SQL 执行前用声明的基数做检查——若同一作用域内
既出现了某对关系的两张表、又对"一"侧表的度量列做了聚合，即判为扇出陷阱并打回重写。
作用域按括号深度切分，`COUNT(DISTINCT ...)` 予以豁免，因此 CTE 里先聚合再连接的正确写法不会误报。

```
正确写法：  SELECT SUM(o.discount) FROM orders o                     → 250
踩坑写法：  ... FROM orders o JOIN order_items oi ON ...  SUM(o.discount) → 350  ✗ 被放大
```

## schema 注入：小库全量，大库检索

公开 benchmark 平均 **6.8 张表 / 72.5 列**，全量塞进 prompt 毫无压力。真实企业库常见
**100+ 表 / 上千列**，甚至 4 万列。全量注入的问题不只是"塞不进上下文"——即使装得下，
模型也会 attention 到无关列，进而幻觉出不存在的 join。

`db.get_schema_summary()` 保留全量语义（小库仍走它），大库改走 `schema.select()`：

| 层级 | 性质 | 说明 |
| --- | --- | --- |
| 1. 锚定 | 确定性 | 命中指标模板直接引用到的表。命中 GMV 就必带 `orders`/`order_items`——否则模板没法用。**永不参与淘汰** |
| 2. 闭包 | 半确定性 | 沿 `semantics.json` 里**已声明**的 join 关系扩展 N 跳。只走声明的边，天然不会把幻觉的 join 带进来 |
| 3. 召回 | 概率性 | 标识符字面命中 + 向量召回，补上问题里点到、前两级没覆盖的表 |

**实测（130 表 / 3798 列的合成库）**：全量 schema 文本 88,248 字符 → 检索后 **426 字符**，
只注入 `orders / order_items / customers / products` 这 4 张真实表，126 张干扰表一张没混进来，
端到端 GMV 仍准确命中模板并算出 5999。

**召回门控**——这一步是关键。跨语言检索实测距离分布：

```
"2024 年 6 月的 GMV 是多少？"   → stg_payment_0 1.6255  ← 垃圾表排第一
                                  marketo_invoice_0 1.6273
                                  orders 1.6584          ← 真正相关的排第 5
"orders 表的 discount 字段"      → orders 0.8523         ← 带字面标识符才真正分开
```

中文问题对英文标识符时，距离全挤在 1.6+，**最近的邻居纯属噪声**。所以召回必须做相关性门控：
最相似的表要足够近（绝对阈值）且明显优于其它候选（相对带宽），否则一张都不补——
**错的表比没有表更危险**。阈值与 embedding 模型强相关，换多语言模型后需重新标定。

## 特性

- **前后端分离**：FastAPI 后端负责全部业务逻辑和文件 I/O，Streamlit 前端只负责渲染与 HTTP 调用。
- **多会话管理**：每个对话一个 JSON 文件（`conversations/`），前端支持新建、切换、删除，互不干扰。
- **会话历史（短期记忆）**：答完把本轮问答写盘，下次提问读最近 k 条回注给模型，支撑 "那 5 月呢" 这类省略式追问。
- **意图路由**：进图先由 `route` 判定本轮要不要查库，闲聊与概念解释直接回答，不浪费检索和执行。
- **声明 join + 扇出拦截**：连接关系显式声明并带基数，跨一对多聚合"一"侧度量会在执行前被拦下，
  把"静默算错"变成"可拦截的错误"。
- **schema 按需注入**：小库全量、大库检索式子集（锚定 + 声明 join 闭包 + 门控召回），
  实测 130 表库把 schema 从 88K 字符压到 426 字符，且不引入干扰表。
- **指标模板直出**：GMV 这类高频问题命中预写 SQL，聚合表达式与过滤条件逐字复用，不依赖模型现场推理。
- **自纠错循环**：SQL 执行报错或语义校验不通过时，把原因回喂给模型重写，最多 `MAX_RETRIES` 轮。
- **模板自检**：`seed_data.py` 会逐条渲染模板并试跑，模板笔误在建库阶段就暴露，不会留到查询时。
- **只读安全**：禁止 DROP/INSERT/UPDATE/DELETE，结果行数上限 `MAX_ROWS`，SQLite 只读模式连接。

## 项目结构

```
d:\data-sidekick\
├── frontend/
│   └── app.py              # Streamlit 前端入口
├── backend/
│   └── server.py           # FastAPI 后端入口
├── data_sidekick/
│   ├── agent.py            # LangGraph 图编排与节点
│   ├── semantics.py        # 语义层：声明加载 / 模板渲染 / 扇出校验
│   ├── llm.py              # LLM 封装
│   ├── db.py               # 数据库操作
│   ├── rag.py              # Chroma 指标检索
│   ├── memory.py           # 会话历史存储
│   └── state.py            # 状态定义
├── semantics.json          # 声明式 join + 结构化指标字典
├── config.py               # 配置
├── seed_data.py            # 示例数据 + 语义层灌库 + 模板自检
├── app.py                  # 启动引导说明
├── requirements.txt
├── .env.example
├── chroma_store/           # 口径向量库（运行 seed_data.py 后生成）
└── conversations/          # 会话历史，一个对话一个 JSON 文件（运行时生成）
```

## 技术栈

LangGraph（编排）、Chroma（口径检索）、FastAPI（后端）、Streamlit（前端）、SQLite（示例数据）。