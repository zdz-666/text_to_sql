# DataSidekick

自然语言问数助手——把"向数据组提需求"缩短为"用大白话秒级取数"。

核心思路：**不只做 Text-to-SQL**，而是在写 SQL 之前先检索业务口径（Agentic RAG），
执行后自纠错，且全程只读、可解释。用 LangGraph 把整条链路编排成有状态、可分支、
可重试的图。

## 特性

- **口径先行**：用 Chroma 向量库存"指标字典"，写 SQL 前先检索口径（如 GMV 只统计 paid 订单），避免 LLM 瞎猜。

- **自纠错循环**：SQL 执行报错时，把错误信息回喂给模型重写，最多 `MAX_RETRIES` 轮。

- **只读安全**：SQLite 以 `mode=ro` 只读连接打开 + 白名单校验（只允许 SELECT / WITH），双保险。

- **可解释**：每个答案附带实际执行的 SQL 与依据的口径。

- **主动澄清**：口径缺失时宁可反问，也不硬猜。

## 架构

```
用户问题
   │
   ├─ retrieve  (Chroma 检索指标口径 + 读取 schema)
   ├─ generate  (LLM 生成只读 SQL；口径缺失则进入澄清)
   ├─ execute   (只读执行，失败记录 error)
   │     └─ 出错且未超重试上限 → 回到 generate（自纠错）
   └─ answer    (把结果 + SQL 汇报成自然语言)
```

技术栈：LangGraph（编排）、Chroma（口径检索）、FastAPI（后端）、Streamlit（前端）、SQLite（示例数据）。

## 目录结构

```
data-sidekick/
├── config.py              # 集中配置（读 .env）
├── seed_data.py           # 建 demo 库 + 写示例数据 + 向量化口径字典
├── api.py                 # FastAPI 入口
├── app.py                 # Streamlit 前端
├── requirements.txt
├── .env.example
└── data_sidekick/
    ├── llm.py             # LLM 封装（OpenAI 兼容接口）
    ├── db.py              # 只读连接 / schema 摘要 / 安全执行
    ├── rag.py             # Chroma 口径检索
    ├── state.py           # LangGraph 状态定义
    └── agent.py           # 图编排与各节点
```

## 快速开始

```bash
# 1. 建虚拟环境并装依赖
python -m venv .venv
.venv\Scripts\activate          # Windows；macOS/Linux 用 source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置 LLM
copy .env.example .env          # 填写 OPENAI_API_KEY 等

# 3. 初始化示例数据和口径字典
python seed_data.py

# 4a. 起前端（推荐，直接对话）
streamlit run app.py

# 4b. 或起后端
uvicorn api:app --reload
```

> 首次 `seed_data.py` 时 Chroma 会自动下载默认 embedding 模型（需联网）。

## 可用的 LLM 配置

只要 OpenAI 兼容即可，改 `.env` 三行：

| Provider | OPENAI\_BASE\_URL                                   | MODEL\_NAME     |
| -------- | --------------------------------------------------- | --------------- |
| DeepSeek | `https://api.deepseek.com`                          | `deepseek-chat` |
| 通义千问     | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus`     |
| OpenAI   | `https://api.openai.com/v1`                         | `gpt-4o-mini`   |

## 后续改进方向（写进简历的加分项）

- 把 schema 从"全量塞 prompt"升级为"相关表选择 + 列裁剪"，支持更大库。

- 加 SQL 结果评估集（20\~30 条标准问题）做可复现的准确率 eval。

- 用 LangSmith / 日志把每一步轨迹可视化，做可观测性。

- 加入人工确认节点（human-in-the-loop），对高风险查询先审后跑。

- 支持 PostgreSQL 并通过连接权限做数据库级只读。

