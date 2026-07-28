"""
缓存配置
"""
import os

CACHE_CONFIG = {
    "enabled": os.getenv("CACHE_ENABLED", "false").lower() == "true",
    "expire_days": 7,
    "max_size_mb": 100,
    "skip_when_profile": True,
    "llm_cache_enabled": os.getenv("LLM_CACHE_ENABLED", "false").lower() == "true",
    "llm_cache_expire_hours": 24,
}
