"""LLM 封装：用 langchain-openai 的 ChatOpenAI，兼容任何 OpenAI 风格接口。

对外暴露两个东西：
- get_llm()：惰性创建单例，避免在每个请求里重复初始化连接。
- extract_json()：把要求模型输出 JSON 的那些 prompt 的返回值统一解析掉。
"""
import json
import re

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


def extract_json(text: str) -> dict:
    """从 LLM 输出里稳健地抽出 JSON（容忍 markdown 代码块围栏和前后废话）。

    解析失败返回空 dict，交给调用方用 .get() 兜底，不抛异常中断整条链路。
    """
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except json.JSONDecodeError:
                pass
    return {}