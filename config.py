"""集中配置：所有可调参数都从这里读取环境变量。

刻意保持简单，不引入 pydantic-settings，用 os.getenv + 默认值即可满足原型阶段。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# LLM
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-chat")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0"))

# 路由模型：使用硅基流动 Kev-4b 快速决策模型代替通用 LLM 做意图路由。
# Kev-4b 通过 SystemOne API 调用，比通用 chat 模型更快、更省 token。
ROUTE_MODEL_NAME = os.getenv("ROUTE_MODEL_NAME", "SemIf")
ROUTE_SCORE_THRESHOLD = float(os.getenv("ROUTE_SCORE_THRESHOLD", "0.5"))

# 数据与检索
DB_PATH = str(BASE_DIR / os.getenv("DB_PATH", "demo.db"))
CHROMA_DIR = str(BASE_DIR / os.getenv("CHROMA_DIR", "chroma_store"))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "metric_definitions")

# 语义层：声明式 join 关系 + 结构化指标字典（唯一真相来源）
SEMANTICS_PATH = str(BASE_DIR / os.getenv("SEMANTICS_PATH", "semantics.json"))
SEMANTICS_TOP_K = int(os.getenv("SEMANTICS_TOP_K", "3"))

# schema 注入策略：超过阈值就从"全量塞 prompt"切换成"检索式注入"
# 公开 benchmark 平均 6.8 表 / 72.5 列，全量注入没问题；
# 真实企业库常见 100+ 表 / 上千列（甚至 4 万列），必须改成检索。
SCHEMA_FULL_MAX_TABLES = int(os.getenv("SCHEMA_FULL_MAX_TABLES", "30"))
SCHEMA_FULL_MAX_COLUMNS = int(os.getenv("SCHEMA_FULL_MAX_COLUMNS", "200"))
# 检索模式下最多注入多少张表（列预算复用 SCHEMA_FULL_MAX_COLUMNS）
SCHEMA_TOP_K_TABLES = int(os.getenv("SCHEMA_TOP_K_TABLES", "12"))
# 沿已声明 join 关系扩展几跳
SCHEMA_JOIN_HOPS = int(os.getenv("SCHEMA_JOIN_HOPS", "1"))
SCHEMA_COLLECTION_NAME = os.getenv("SCHEMA_COLLECTION_NAME", "schema_tables")
# 向量召回的相关性门控：
# 中文问题对英文标识符时，embedding 距离会挤在一起（实测全部落在 1.6+），
# 这时"最近邻"其实是噪声，注入错的表比不注入更危险。
# 只有最相似的表足够近（绝对阈值）且明显优于其它候选（相对带宽）才采纳。
# 阈值与 embedding 模型强相关：换成多语言模型后需要重新标定。
SCHEMA_RECALL_MAX_DISTANCE = float(os.getenv("SCHEMA_RECALL_MAX_DISTANCE", "1.2"))
SCHEMA_RECALL_DISTANCE_RATIO = float(os.getenv("SCHEMA_RECALL_DISTANCE_RATIO", "1.15"))

# 智能体行为
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
MAX_ROWS = int(os.getenv("MAX_ROWS", "50"))

# 短期记忆：会话历史落盘目录 + 每次回注最近多少条
CONVERSATIONS_DIR = str(BASE_DIR / os.getenv("CONVERSATIONS_DIR", "conversations"))
HISTORY_MESSAGES = int(os.getenv("HISTORY_MESSAGES", "10"))
HISTORY_MAX_CHARS = int(os.getenv("HISTORY_MAX_CHARS", "300"))

# 前后端分离时，前端用来找到后端的地址
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")