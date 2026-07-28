"""
LLM 配置
"""
import os
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

LLM_CONFIG = {
    "temperature": 0,
    "max_tokens": 2000,
    "timeout": 60,
    "retry_times": 3,
    "max_concurrency": 5,
    "concurrency_enabled": True,
}

_PLACEHOLDER_KEYS = {
    "",
    "your-deepseek-api-key-here",
    "在这里填入你的Deepseek API Key",
    "在这里填入你的DeepSeek API Key",
}


def has_llm() -> bool:
    return DEEPSEEK_API_KEY.strip() not in _PLACEHOLDER_KEYS
