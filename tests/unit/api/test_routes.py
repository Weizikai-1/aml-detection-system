"""
API 路由单元测试

提高以下路由的测试覆盖率:
- upload.py (20% → 80%)
- reports.py (36% → 80%)
- auth.py (69% → 85%)
- analysis.py (79% → 90%)
"""
import os
import sys
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

os.environ["DEV_ADMIN_PASSWORD"] = "test-pass-123"
os.environ["DEV_ANALYST_PASSWORD"] = "test-pass-123"
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DEEPSEEK_API_KEY", "placeholder")

from api.main import app

client = TestClient(app)


def _login(username="admin", password="test-pass-123"):
    """登录并返回 JWT token"""
    resp = client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    if resp.status_code == 200:
        return resp.json()["access_token"]
    return None


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


class TestAuthRoutes:
    """认证路由测试"""

    def test_login_success(self):
        """登录成功"""
        resp = client.post(
            "/api/auth/login",
            data={"username": "admin", "password": "test-pass-123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "user" in data

    def test_login_failure_wrong_password(self):
        """密码错误登录失败"""
        resp = client.post(
            "/api/auth/login",
            data={"username": "admin", "password": "wrong"},
        )
        assert resp.status_code == 401

    def test_login_failure_user_not_found(self):
        """用户不存在登录失败"""
        resp = client.post(
            "/api/auth/login",
            data={"username": "nonexistent", "password": "test"},
        )
        assert resp.status_code == 401

    def test_get_current_user_info(self):
        """获取当前用户信息"""
        token = _login()
        assert token is not None
        resp = client.get("/api/auth/me", headers=_auth_headers(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"

    def test_get_current_user_info_unauthorized(self):
        """未认证访问用户信息"""
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_verify_password(self):
        """密码验证功能"""
        from api.routes.auth import verify_password, get_password_hash
        hashed = get_password_hash("test-pass-123")
        assert verify_password("test-pass-123", hashed)
        assert not verify_password("wrong", hashed)


class TestUploadRoutes:
    """上传路由测试"""

    def test_upload_file_success(self):
        """上传文件成功"""
        token = _login()
        assert token is not None

        csv_content = "交易流水号,交易日期,交易金额,付款账号,收款账号\nTXN001,2026-07-01,1000.0,ACC1,ACC2\n"
        resp = client.post(
            "/api/upload/file",
            headers=_auth_headers(token),
            files={"file": ("test.csv", csv_content, "text/csv")},
        )
        assert resp.status_code == 200

    def test_upload_file_invalid_format(self):
        """上传不支持的文件格式"""
        token = _login()
        assert token is not None

        resp = client.post(
            "/api/upload/file",
            headers=_auth_headers(token),
            files={"file": ("test.txt", "content", "text/plain")},
        )
        assert resp.status_code == 400

    def test_upload_file_empty(self):
        """上传空文件"""
        token = _login()
        assert token is not None

        resp = client.post(
            "/api/upload/file",
            headers=_auth_headers(token),
            files={"file": ("test.csv", "", "text/csv")},
        )
        assert resp.status_code == 200

    def test_preview_uploaded_file(self):
        """预览上传文件"""
        token = _login()
        assert token is not None

        csv_content = "交易流水号,交易日期,交易金额,付款账号,收款账号\nTXN001,2026-07-01,1000.0,ACC1,ACC2\n"
        resp = client.post(
            "/api/upload/preview",
            headers=_auth_headers(token),
            files={"file": ("test.csv", csv_content, "text/csv")},
        )
        assert resp.status_code == 200

    def test_get_supported_formats(self):
        """获取支持的格式列表"""
        resp = client.get("/api/upload/formats")
        assert resp.status_code == 200
        data = resp.json()
        assert "supported_formats" in data
        assert "required_fields" in data


class TestAnalysisRoutes:
    """分析路由测试"""

    def test_submit_analysis_empty(self):
        """提交空交易数据"""
        token = _login()
        assert token is not None

        resp = client.post(
            "/api/analysis/",
            headers=_auth_headers(token),
            json=[],
        )
        assert resp.status_code == 400

    @patch("api.tasks.submit_analysis_task")
    def test_submit_analysis_success(self, mock_task):
        """提交分析任务成功"""
        mock_task.delay.return_value.id = "mock-task-id"
        
        token = _login()
        assert token is not None

        transactions = [{
            "transaction_id": "TXN001",
            "from_account": "ACC1",
            "to_account": "ACC2",
            "amount": 1000.0,
            "timestamp": "2026-07-01T10:00:00",
            "transaction_type": "transfer",
        }]
        resp = client.post(
            "/api/analysis/",
            headers=_auth_headers(token),
            json=transactions,
        )
        assert resp.status_code == 202

    def test_run_analysis_sync_empty(self):
        """同步分析空数据"""
        token = _login()
        assert token is not None

        resp = client.post(
            "/api/analysis/run",
            headers=_auth_headers(token),
            json=[],
        )
        assert resp.status_code == 400

    def test_run_analysis_sync_success(self):
        """同步分析成功"""
        token = _login()
        assert token is not None

        transactions = [{
            "transaction_id": "TXN001",
            "from_account": "ACC1",
            "to_account": "ACC2",
            "amount": 1000.0,
            "timestamp": "2026-07-01T10:00:00",
            "transaction_type": "transfer",
        }]
        resp = client.post(
            "/api/analysis/run",
            headers=_auth_headers(token),
            json=transactions,
        )
        assert resp.status_code == 200

    def test_list_tasks(self):
        """获取任务列表"""
        token = _login()
        assert token is not None

        resp = client.get("/api/analysis/tasks", headers=_auth_headers(token))
        assert resp.status_code == 200


class TestReportsRoutes:
    """报告路由测试"""

    def test_list_reports(self):
        """获取报告列表"""
        token = _login()
        assert token is not None

        resp = client.get("/api/reports/", headers=_auth_headers(token))
        assert resp.status_code == 200

    def test_get_reports_stats(self):
        """获取报告统计"""
        token = _login()
        assert token is not None

        resp = client.get("/api/reports/stats", headers=_auth_headers(token))
        assert resp.status_code == 200

    def test_get_report_not_found(self):
        """获取不存在的报告"""
        token = _login()
        assert token is not None

        resp = client.get("/api/reports/nonexistent", headers=_auth_headers(token))
        assert resp.status_code == 404

    def test_validate_report_id_invalid(self):
        """校验无效报告ID"""
        token = _login()
        assert token is not None

        resp = client.get("/api/reports/invalid@id", headers=_auth_headers(token))
        assert resp.status_code == 400

    def test_export_report_not_found(self):
        """导出不存在的报告"""
        token = _login()
        assert token is not None

        resp = client.get("/api/reports/nonexistent/export/excel", headers=_auth_headers(token))
        assert resp.status_code == 404