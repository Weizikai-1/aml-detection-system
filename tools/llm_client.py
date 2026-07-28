"""
LLM 客户端封装
统一管理 DeepSeek API 调用
"""
import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Optional, Dict, Any

from langchain_openai import ChatOpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, LLM_CONFIG
from config.cache import CACHE_CONFIG

logger = logging.getLogger(__name__)

try:
    from langchain_core.globals import set_llm_cache
    from langchain_core.cache import InMemoryCache
    if CACHE_CONFIG.get("llm_cache_enabled", False):
        set_llm_cache(InMemoryCache())
        logger.info("[LLM] 内存缓存已启用")
except ImportError:
    logger.debug("[LLM] langchain_core.cache 不可用，仅使用文件缓存")


class LlmFileCache:
    """本地文件缓存（作为内存缓存的持久化补充）"""

    def __init__(self, cache_dir: str = None, expire_hours: int = 24):
        if cache_dir is None:
            cache_dir = os.path.join("data", "llm_cache")
        self.cache_dir = cache_dir
        self.expire_hours = expire_hours
        os.makedirs(cache_dir, exist_ok=True)

    def _get_key(self, prompt: str, system_prompt: str = "") -> str:
        """生成缓存键"""
        content = f"{system_prompt}|||{prompt}"
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    def get(self, prompt: str, system_prompt: str = "") -> Optional[str]:
        """获取缓存"""
        key = self._get_key(prompt, system_prompt)
        path = os.path.join(self.cache_dir, f"{key}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if time.time() - data.get("timestamp", 0) > self.expire_hours * 3600:
                os.remove(path)
                return None
            return data.get("response")
        except Exception:
            return None

    def set(self, prompt: str, system_prompt: str, response: str) -> None:
        """设置缓存"""
        key = self._get_key(prompt, system_prompt)
        path = os.path.join(self.cache_dir, f"{key}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "prompt": prompt[:100] + "..." if len(prompt) > 100 else prompt,
                    "system_prompt": system_prompt[:50] + "..." if len(system_prompt) > 50 else system_prompt,
                    "response": response,
                    "timestamp": time.time(),
                    "expire_hours": self.expire_hours,
                }, f, ensure_ascii=False)
        except Exception as e:
            logger.error("[LLM] 缓存写入失败: %s", e)

    def clear(self) -> int:
        """清理过期缓存"""
        count = 0
        for f in os.listdir(self.cache_dir):
            path = os.path.join(self.cache_dir, f)
            if f.endswith(".json"):
                try:
                    with open(path, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                    if time.time() - data.get("timestamp", 0) > self.expire_hours * 3600:
                        os.remove(path)
                        count += 1
                except Exception:
                    os.remove(path)
                    count += 1
        return count


_file_cache = None

def get_file_cache() -> LlmFileCache:
    """获取文件缓存实例"""
    global _file_cache
    if _file_cache is None:
        expire_hours = CACHE_CONFIG.get("llm_cache_expire_hours", 24)
        _file_cache = LlmFileCache(expire_hours=expire_hours)
    return _file_cache


def get_llm(temperature: float = None) -> ChatOpenAI:
    """
    获取 LLM 实例

    Args:
        temperature: 温度参数(为空则用默认值)

    Returns:
        ChatOpenAI 实例
    """
    return ChatOpenAI(
        model=DEEPSEEK_MODEL,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=temperature if temperature is not None else LLM_CONFIG["temperature"],
        max_tokens=LLM_CONFIG["max_tokens"],
        timeout=LLM_CONFIG["timeout"],
    )


def invoke_llm(prompt: str, system_prompt: str = "") -> str:
    """
    调用 LLM (同步)

    Args:
        prompt: 用户提示词
        system_prompt: 系统提示词

    Returns:
        LLM 响应文本
    """
    if CACHE_CONFIG.get("llm_cache_enabled", False):
        file_cache = get_file_cache()
        cached = file_cache.get(prompt, system_prompt)
        if cached is not None:
            logger.debug("[LLM] 命中文件缓存")
            return cached

    llm = get_llm()
    messages = []
    if system_prompt:
        messages.append(("system", system_prompt))
    messages.append(("human", prompt))

    for attempt in range(LLM_CONFIG["retry_times"]):
        try:
            response = llm.invoke(messages)
            result = response.content
            if CACHE_CONFIG.get("llm_cache_enabled", False):
                file_cache = get_file_cache()
                file_cache.set(prompt, system_prompt, result)
            return result
        except Exception as e:
            logger.error("LLM 调用失败(第%d次): %s", attempt + 1, e)
            if attempt == LLM_CONFIG["retry_times"] - 1:
                raise
    return ""


async def ainvoke_llm(prompt: str, system_prompt: str = "", semaphore: asyncio.Semaphore = None) -> str:
    """
    异步调用 LLM

    Args:
        prompt: 用户提示词
        system_prompt: 系统提示词
        semaphore: 并发控制信号量

    Returns:
        LLM 响应文本
    """
    if CACHE_CONFIG.get("llm_cache_enabled", False):
        file_cache = get_file_cache()
        cached = file_cache.get(prompt, system_prompt)
        if cached is not None:
            logger.debug("[LLM] 命中文件缓存(异步)")
            return cached

    async def _call():
        llm = get_llm()
        messages = []
        if system_prompt:
            messages.append(("system", system_prompt))
        messages.append(("human", prompt))

        for attempt in range(LLM_CONFIG["retry_times"]):
            try:
                response = await llm.ainvoke(messages)
                result = response.content
                if CACHE_CONFIG.get("llm_cache_enabled", False):
                    file_cache = get_file_cache()
                    file_cache.set(prompt, system_prompt, result)
                return result
            except Exception as e:
                logger.error("LLM 异步调用失败(第%d次): %s", attempt + 1, e)
                if attempt == LLM_CONFIG["retry_times"] - 1:
                    raise
                await asyncio.sleep(1 * (attempt + 1))
        return ""

    if semaphore:
        async with semaphore:
            return await _call()
    else:
        return await _call()


def _run_async(coro):
    """
    异步协程同步执行 — 兼容 Streamlit 已有事件循环的环境

    - 在主线程/没有运行中循环时：asyncio.run
    - 已有运行中循环（如 Streamlit 重跑时）：使用 nest_asyncio 注入
      若 nest_asyncio 不可用则降级为线程隔离
    """
    try:
        # 戒律 P4: Python 3.10+ asyncio.get_event_loop() 已废弃
        # 使用 get_running_loop() 显式获取正在运行的循环
        loop = asyncio.get_running_loop()
        if loop.is_running():
            # 已有事件循环（Streamlit rerun 场景）
            try:
                import nest_asyncio
                nest_asyncio.apply(loop)
                return loop.run_until_complete(coro)
            except ImportError:
                # nest_asyncio 不可用 → 走线程隔离
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(asyncio.run, coro)
                    return fut.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        # 没有事件循环（首次调用）
        return asyncio.run(coro)


def batch_invoke_llm(prompts: list[str], system_prompt: str = "") -> list[str]:
    """
    批量并发调用 LLM

    Args:
        prompts: 用户提示词列表
        system_prompt: 系统提示词

    Returns:
        LLM 响应文本列表（与prompts顺序一致）
    """
    if not LLM_CONFIG.get("concurrency_enabled", False) or len(prompts) <= 1:
        return [invoke_llm(p, system_prompt) for p in prompts]

    max_conc = LLM_CONFIG.get("max_concurrency", 5)

    async def _batch():
        semaphore = asyncio.Semaphore(max_conc)
        tasks = [ainvoke_llm(p, system_prompt, semaphore) for p in prompts]
        return await asyncio.gather(*tasks, return_exceptions=True)

    results = _run_async(_batch())

    # 戒律 P1/P3: 失败时返回结构化标记而非空字符串，
    # 确保上层对失败交易走"人工复核/默认可疑"分支，不遗漏也不静默误判
    final = []
    for r in results:
        if isinstance(r, Exception):
            final.append({"_llm_error": str(r)})
        else:
            final.append(r)
    return final
