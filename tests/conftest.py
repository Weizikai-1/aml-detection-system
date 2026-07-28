"""共享 pytest 夹具

参考 TradingAgents-CN-lite 的 conftest.py 设计：
- autouse 注入占位 API key，防止 CI 因缺密钥卡死
- 注册 marker，便于按层级筛选测试
- 提供 mock_llm fixture，统一 LLM Mock 范式
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

# 确保项目根目录在 sys.path 中（测试移动到子目录后需要）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pytest_configure(config):
    for marker in ("unit", "integration", "smoke"):
        config.addinivalue_line("markers", f"{marker}: {marker}-level tests")


# 防止 CI 因缺 DEEPSEEK_API_KEY 卡死或误触发真实调用
@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", os.environ.get("DEEPSEEK_API_KEY", "placeholder"))


@pytest.fixture()
def mock_llm_suspicious():
    """Mock LLM：默认返回"可疑"判断（high 风险，置信度 0.9）"""
    llm = MagicMock()
    response = MagicMock()
    response.content = (
        '{"is_suspicious": true, "risk_level": "high", '
        '"confidence": 0.9, "analysis": "命中多条可疑模式", '
        '"false_positive_reason": ""}'
    )
    llm.invoke.return_value = response
    return llm


@pytest.fixture()
def mock_llm_false_positive():
    """Mock LLM：默认返回"误报"判断"""
    llm = MagicMock()
    response = MagicMock()
    response.content = (
        '{"is_suspicious": false, "risk_level": "low", '
        '"confidence": 0.85, "analysis": "正常交易", '
        '"false_positive_reason": "金额与营业额匹配"}'
    )
    llm.invoke.return_value = response
    return llm


@pytest.fixture()
def mock_llm_json_in_codeblock():
    """Mock LLM：返回 ```json 包裹的响应，测试解析鲁棒性"""
    llm = MagicMock()
    response = MagicMock()
    response.content = (
        "```json\n"
        '{"is_suspicious": true, "risk_level": "critical", '
        '"confidence": 0.95, "analysis": "多种可疑模式叠加", '
        '"false_positive_reason": ""}'
        "\n```"
    )
    llm.invoke.return_value = response
    return llm


@pytest.fixture()
def mock_llm_failure():
    """Mock LLM：invoke 抛异常，测试降级路径"""
    llm = MagicMock()
    llm.invoke.side_effect = RuntimeError("API connection failed")
    return llm
