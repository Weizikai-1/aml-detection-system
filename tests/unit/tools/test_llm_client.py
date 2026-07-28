"""
LLM 客户端测试
"""
import os
import pytest

from tools.llm_client import LlmFileCache, get_file_cache, invoke_llm


class TestLlmFileCache:
    """文件缓存测试"""

    def test_get_key_consistent(self):
        """相同输入生成相同缓存键"""
        cache = LlmFileCache(expire_hours=1)
        key1 = cache._get_key("hello", "system")
        key2 = cache._get_key("hello", "system")
        assert key1 == key2

    def test_get_key_different(self):
        """不同输入生成不同缓存键"""
        cache = LlmFileCache(expire_hours=1)
        key1 = cache._get_key("hello", "system")
        key2 = cache._get_key("world", "system")
        key3 = cache._get_key("hello", "other")
        assert key1 != key2
        assert key1 != key3

    def test_cache_set_and_get(self, tmp_path):
        """缓存设置和获取"""
        cache_dir = tmp_path / "llm_cache"
        cache = LlmFileCache(str(cache_dir), expire_hours=1)

        cache.set("test_prompt", "test_system", "test_response")
        result = cache.get("test_prompt", "test_system")
        assert result == "test_response"

    def test_cache_not_found(self, tmp_path):
        """不存在的缓存返回 None"""
        cache_dir = tmp_path / "llm_cache"
        cache = LlmFileCache(str(cache_dir), expire_hours=1)

        result = cache.get("nonexistent", "")
        assert result is None

    def test_cache_expire(self, tmp_path):
        """过期缓存返回 None"""
        import time
        cache_dir = tmp_path / "llm_cache"
        cache = LlmFileCache(str(cache_dir), expire_hours=0.001)

        cache.set("expire_prompt", "", "response")
        time.sleep(5)
        result = cache.get("expire_prompt", "")
        assert result is None

    def test_cache_clear(self, tmp_path):
        """清理过期缓存"""
        import time
        cache_dir = tmp_path / "llm_cache"
        cache = LlmFileCache(str(cache_dir), expire_hours=0.001)

        cache.set("clear_prompt", "", "response")
        time.sleep(5)
        count = cache.clear()
        assert count >= 1

    def test_get_file_cache_singleton(self):
        """单例模式测试"""
        cache1 = get_file_cache()
        cache2 = get_file_cache()
        assert cache1 is cache2


class TestLlmClientConfig:
    """LLM 客户端配置测试"""

    def test_cache_disabled_by_default(self, monkeypatch):
        """默认配置下缓存禁用"""
        monkeypatch.delenv("LLM_CACHE_ENABLED", raising=False)
        from config.cache import CACHE_CONFIG
        assert CACHE_CONFIG["llm_cache_enabled"] is False

    def test_cache_enabled_via_env(self, monkeypatch):
        """通过环境变量启用缓存"""
        monkeypatch.setenv("LLM_CACHE_ENABLED", "true")
        import importlib
        import config.cache
        importlib.reload(config.cache)
        from config.cache import CACHE_CONFIG
        assert CACHE_CONFIG["llm_cache_enabled"] is True