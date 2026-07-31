"""
DeepSeek LLM 客户端 — 统一封装
支持 reasoning (deepseek-chat)，内置重试 + 超时 + fallback
"""
import os
import time
import logging
import settings  # 触发 .env 加载

log = logging.getLogger("aml.llm")

# 容错配置
_MAX_RETRIES = 3
_RETRY_DELAY = 1.5            # 重试间隔（秒），指数退避
_TIMEOUT = 30                 # 单次调用超时（秒）
_RATE_LIMIT_DELAY = 0.5       # 连续调用间的最小间隔


class DeepSeekClient:
    """DeepSeek API 封装 — 生产级容错"""

    def __init__(self, api_key: str = None, model: str = "deepseek-chat"):
        self._api_key = api_key
        self.model = model
        self.base_url = "https://api.deepseek.com/v1"
        self._last_call = 0.0

    @property
    def api_key(self):
        return self._api_key or os.getenv("DEEPSEEK_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self.api_key)

    def chat(self, system_prompt: str, user_message: str,
             temperature: float = 0.3, max_tokens: int = 2000,
             timeout: float = _TIMEOUT) -> str:
        """
        单轮对话，含自动重试 + 超时 + fallback。

        容错策略:
          1. 指数退避重试 (1.5s → 3s → 6s)
          2. 超时保护
          3. 速率限制（调用间隔 ≥ 0.5s）
          4. 所有重试失败后返回 fallback 文本，不抛异常
        """
        if not self.is_available():
            return "[LLM 不可用: 请设置 DEEPSEEK_API_KEY]"

        from openai import OpenAI

        last_error = ""
        for attempt in range(1, _MAX_RETRIES + 1):
            # 速率限制
            elapsed = time.time() - self._last_call
            if elapsed < _RATE_LIMIT_DELAY:
                time.sleep(_RATE_LIMIT_DELAY - elapsed)

            try:
                client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    timeout=timeout,
                )
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                self._last_call = time.time()
                return resp.choices[0].message.content

            except Exception as e:
                last_error = str(e)
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAY * (2 ** (attempt - 1))
                    log.warning(
                        f"DeepSeek 调用失败 (attempt {attempt}/{_MAX_RETRIES}): "
                        f"{last_error[:80]}, {delay:.1f}s 后重试"
                    )
                    time.sleep(delay)
                else:
                    log.error(
                        f"DeepSeek 调用失败 ({_MAX_RETRIES}次重试耗尽): {last_error[:120]}"
                    )

        return f"[LLM Fallback: API 不可用, 已重试{_MAX_RETRIES}次. 最后错误: {last_error[:100]}]"
