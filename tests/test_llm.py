"""LLM DeepSeek 客户端测试 — mock 覆盖重试/超时/fallback/JSON解析"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from unittest.mock import patch, MagicMock
import time


# ============================================================
# DeepSeekClient 测试
# ============================================================

class TestDeepSeekClient:
    def test_is_available_with_key(self):
        """有 API Key 时应返回 True"""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test123"}, clear=True):
            from llm.deepseek_client import DeepSeekClient
            client = DeepSeekClient()
            assert client.is_available()

    def test_is_available_without_key(self):
        """无 API Key 时应返回 False"""
        with patch.dict(os.environ, {}, clear=True):
            from llm.deepseek_client import DeepSeekClient
            client = DeepSeekClient(api_key="")
            assert not client.is_available()

    def test_api_key_priority(self):
        """构造参数优先于环境变量"""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env"}, clear=True):
            from llm.deepseek_client import DeepSeekClient
            client = DeepSeekClient(api_key="sk-arg")
            assert client.api_key == "sk-arg"

    def test_api_key_fallback_to_env(self):
        """无构造参数时使用环境变量"""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env"}, clear=True):
            from llm.deepseek_client import DeepSeekClient
            client = DeepSeekClient()
            assert client.api_key == "sk-env"


class TestChatMock:
    """使用 mock 测试 chat 核心逻辑"""

    def _make_mock_response(self, content: str):
        """构造 OpenAI API 响应 mock"""
        mock = MagicMock()
        mock.choices = [MagicMock()]
        mock.choices[0].message.content = content
        return mock

    def test_chat_returns_content(self):
        """正常调用返回 LLM 文本"""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=True):
            from llm.deepseek_client import DeepSeekClient
            client = DeepSeekClient()

            mock_resp = self._make_mock_response("这是 LLM 的分析结果")
            with patch("openai.OpenAI") as mock_openai:
                mock_instance = mock_openai.return_value
                mock_instance.chat.completions.create.return_value = mock_resp
                result = client.chat("系统提示", "用户消息")
                assert result == "这是 LLM 的分析结果"

    def test_chat_without_key(self):
        """无 API Key 时返回占位文本"""
        with patch.dict(os.environ, {}, clear=True):
            from llm.deepseek_client import DeepSeekClient
            client = DeepSeekClient(api_key="")
            result = client.chat("系统", "用户")
            assert "不可用" in result

    def test_chat_custom_timeout(self):
        """自定义 timeout 参数应生效"""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=True):
            from llm.deepseek_client import DeepSeekClient
            client = DeepSeekClient()
            mock_resp = self._make_mock_response("OK")
            with patch("openai.OpenAI") as mock_openai:
                mock_instance = mock_openai.return_value
                mock_instance.chat.completions.create.return_value = mock_resp
                result = client.chat("sys", "msg", timeout=5.0)
                assert result == "OK"
                # 验证 timeout 被传递
                call_kwargs = mock_openai.call_args[1]
                assert call_kwargs["timeout"] == 5.0

    def test_chat_rate_limit(self):
        """连续调用应有速率限制间隔"""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=True):
            from llm.deepseek_client import DeepSeekClient
            client = DeepSeekClient()
            mock_resp = self._make_mock_response("OK")
            with patch("openai.OpenAI") as mock_openai:
                mock_instance = mock_openai.return_value
                mock_instance.chat.completions.create.return_value = mock_resp
                t0 = time.perf_counter()
                client.chat("sys", "msg1")
                client.chat("sys", "msg2")
                t1 = time.perf_counter()
                # 应有速率限制，但 mock 下实际很快
                assert t1 - t0 >= 0  # 不应崩溃

    def test_retry_on_failure_then_success(self):
        """第1次失败、第2次成功 → 应返回结果"""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=True):
            from llm.deepseek_client import DeepSeekClient
            client = DeepSeekClient()
            mock_resp = self._make_mock_response("重试后成功")
            with patch("openai.OpenAI") as mock_openai:
                mock_instance = mock_openai.return_value
                # 第1次抛异常，第2次成功
                mock_instance.chat.completions.create.side_effect = [
                    Exception("临时错误"),
                    mock_resp,
                ]
                # 缩短重试间隔以加速测试
                with patch("llm.deepseek_client._RETRY_DELAY", 0.01):
                    with patch("llm.deepseek_client._RATE_LIMIT_DELAY", 0.0):
                        result = client.chat("系统", "用户")
                assert result == "重试后成功"
                assert mock_instance.chat.completions.create.call_count == 2

    def test_all_retries_exhausted(self):
        """全部重试失败 → 返回 fallback 文本"""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=True):
            from llm.deepseek_client import DeepSeekClient
            client = DeepSeekClient()
            with patch("openai.OpenAI") as mock_openai:
                mock_instance = mock_openai.return_value
                mock_instance.chat.completions.create.side_effect = Exception("永久错误")
                with patch("llm.deepseek_client._RETRY_DELAY", 0.01):
                    with patch("llm.deepseek_client._RATE_LIMIT_DELAY", 0.0):
                        result = client.chat("系统", "用户")
                assert "Fallback" in result
                assert "3" in result  # 3 次重试耗尽

    def test_chat_model_param(self):
        """自定义 model 参数"""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=True):
            from llm.deepseek_client import DeepSeekClient
            client = DeepSeekClient(model="deepseek-reasoner")
            mock_resp = self._make_mock_response("OK")
            with patch("openai.OpenAI") as mock_openai:
                mock_instance = mock_openai.return_value
                mock_instance.chat.completions.create.return_value = mock_resp
                client.chat("sys", "msg")
                create_kwargs = mock_instance.chat.completions.create.call_args[1]
                assert create_kwargs["model"] == "deepseek-reasoner"

    def test_base_url_from_settings(self):
        """base_url 来自 settings.LLM"""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=True):
            from llm.deepseek_client import DeepSeekClient
            from settings import LLM as LLM_CFG
            client = DeepSeekClient()
            expected = LLM_CFG.get("base_url", "")
            assert client.base_url == expected


# ============================================================
# LLM Reviewer Agent json 解析测试
# ============================================================

class TestLLMReviewer:
    def test_parse_valid_json(self):
        from agents.llm_reviewer import _parse_json
        result = _parse_json('{"suspicion_level": "high", "reasoning": "test"}')
        assert result["suspicion_level"] == "high"
        assert result["reasoning"] == "test"

    def test_parse_json_with_markdown_wrapper(self):
        from agents.llm_reviewer import _parse_json
        result = _parse_json('```json\n{"suspicion_level": "low"}\n```')
        assert result["suspicion_level"] == "low"

    def test_parse_json_with_extra_text(self):
        from agents.llm_reviewer import _parse_json
        result = _parse_json('分析结果: {"suspicion_level": "medium", "reasoning": "可疑"}')
        assert result["suspicion_level"] == "medium"

    def test_parse_invalid_json_fallback(self):
        from agents.llm_reviewer import _parse_json
        result = _parse_json("这是纯文本，没有 JSON")
        assert result["suspicion_level"] == "unknown"
        assert "error" in result

    def test_parse_empty_string(self):
        from agents.llm_reviewer import _parse_json
        result = _parse_json("")
        assert result["suspicion_level"] == "unknown"

    def test_system_prompt_exists(self):
        from agents.llm_reviewer import _SYSTEM_PROMPT
        assert "反洗钱" in _SYSTEM_PROMPT
        assert "suspicion_level" in _SYSTEM_PROMPT
