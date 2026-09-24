"""LLM 封装：用 langchain-openai 的 ChatOpenAI，兼容任何 OpenAI 风格接口。

对外暴露：
- get_llm()：惰性创建单例，避免在每个请求里重复初始化连接。
- route_llm()：调用硅基流动 Kev-4b SystemOne API 做快速意图路由。
- extract_json()：把要求模型输出 JSON 的那些 prompt 的返回值统一解析掉。
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

# 直接运行本文件时，把项目根目录加入 sys.path，保证 import config 能找到
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

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


def route_llm(state_text: str) -> dict:
    """调用硅基流动 Kev-4b SystemOne API 做快速意图路由。

    Kev-4b 是一个专为是否判断 / 选项选择 / 打分评价设计的快速决策模型，
    通过 POST /v1/systemone 调用，比通用 chat 模型更快、更省 token。

    返回 {"needs_sql": bool}。调用失败时按"需要查库"兜底（宁可多跑一次）。
    """
    url = "https://api.siliconflow.cn/v1/systemone"
    payload = json.dumps({
        "model": config.ROUTE_MODEL_NAME,
        "state": state_text,
        "questions": {
            "needs_sql": {
                "type": "noul",
                "instructions": "用户最新的提问是否需要查询数据库才能回答？",
            },
        },
    }).encode("utf-8")
    print("模型名称:", config.ROUTE_MODEL_NAME)

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        score = data.get("answers", {}).get("needs_sql", {}).get("noul", 0.5)
        return {"needs_sql": score >= config.ROUTE_SCORE_THRESHOLD}
    except Exception:
        # 任何异常（网络、鉴权、模型不可用）都退化为"需要查库"
        return {"needs_sql": True}


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
