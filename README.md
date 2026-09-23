# DataSidekick

智能体驱动的 Text-to-SQL 助手。用自然语言问数，自动检索业务口径、生成只读 SQL、执行并汇报结果。

## 架构：前后端分离

```
frontend/app.py        Streamlit UI（纯渲染，所有数据通过 HTTP 获取）
backend/server.py      FastAPI 后端（封装 agent + memory，暴露 REST API）
data_sidekick/
    agent.py           LangGraph 状态图（route → retrieve → generate ⇄ execute → answer）
    llm.py             LLM 封装（OpenAI 兼容接口）
    db.py              只读连接 / schema 摘要 / SQL 执行
    rag.py             Chroma 口径检索
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
     ├─ retrieve   (Chroma 检索指标口径 + 读取 schema)
     ├─ generate   (LLM 生成只读 SQL；口径缺失则进入澄清)
     ├─ execute    (只读执行，失败记录 error)
     │      └─ 出错且未超重试上限 → 回到 generate（自纠错）
     └─ answer     (把结果 + SQL 汇报成自然语言；非取数问题直接闲聊式回答)
                   （后端负责把本轮 user / assistant 两条消息追加进会话文件）
```

## 特性

- **前后端分离**：FastAPI 后端负责全部业务逻辑和文件 I/O，Streamlit 前端只负责渲染与 HTTP 调用。
- **多会话管理**：每个对话一个 JSON 文件（`conversations/`），前端支持新建、切换、删除，互不干扰。
- **会话历史（短期记忆）**：答完把本轮问答写盘，下次提问读最近 k 条回注给模型，支撑 "那 5 月呢" 这类省略式追问。
- **意图路由**：进图先由 `route` 判定本轮要不要查库，闲聊与概念解释直接回答，不浪费检索和执行。
- **自纠错循环**：SQL 执行报错时，把错误信息回喂给模型重写，最多 `MAX_RETRIES` 轮。
- **口径先行**：拿到问题先搜指标字典（Chroma 向量库），确保 GMV 用 `status='paid'` 等口径约束。
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
│   ├── llm.py              # LLM 封装
│   ├── db.py               # 数据库操作
│   ├── rag.py              # Chroma 口径检索
│   ├── memory.py           # 会话历史存储
│   └── state.py            # 状态定义
├── config.py               # 配置
├── seed_data.py            # 示例数据生成
├── app.py                  # 启动引导说明
├── requirements.txt
├── .env.example
├── chroma_store/           # 口径向量库（运行 seed_data.py 后生成）
└── conversations/          # 会话历史，一个对话一个 JSON 文件（运行时生成）
```

## 技术栈

LangGraph（编排）、Chroma（口径检索）、FastAPI（后端）、Streamlit（前端）、SQLite（示例数据）。