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

# 数据与检索
DB_PATH = str(BASE_DIR / os.getenv("DB_PATH", "demo.db"))
CHROMA_DIR = str(BASE_DIR / os.getenv("CHROMA_DIR", "chroma_store"))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "metric_definitions")

# 智能体行为
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
MAX_ROWS = int(os.getenv("MAX_ROWS", "50"))