"""LLM 封装：用 langchain-openai 的 ChatOpenAI，兼容任何 OpenAI 风格接口。

只暴露 get_llm()，惰性创建单例，避免在每个请求里重复初始化连接。
"""
import config
from langchain_openai import ChatOpenAI

_llm = None


def get_llm():
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            model=config.MODEL_NAME,
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
            temperature=config.TEMPERATURE,
        )
    return _llm