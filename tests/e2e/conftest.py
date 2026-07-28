"""E2E 测试 conftest"""
import os
import sys
import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def _setup_env():
    """设置 E2E 测试环境变量（session 级，只设一次）"""
    # 配置开发模式用户密码
    os.environ["DEV_ADMIN_PASSWORD"] = "test-pass-123"
    os.environ["DEV_ANALYST_PASSWORD"] = "test-pass-123"
    # 确保使用 JSON 文件模式（不依赖 PostgreSQL）
    os.environ.setdefault("APP_ENV", "development")
    # 禁用 LLM（E2E 测试不依赖真实 LLM）
    os.environ.setdefault("DEEPSEEK_API_KEY", "placeholder")
    yield


@pytest.fixture()
def client(_setup_env):
    """FastAPI TestClient"""
    # 延迟导入，确保环境变量先设置
    from api.main import app
    with TestClient(app) as c:
        yield c
