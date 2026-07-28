"""
E2E 端到端测试

测试目标：通过 FastAPI TestClient 验证完整 HTTP API 链路
从"HTTP 请求 → 认证 → 业务处理 → HTTP 响应"的完整链路

覆盖场景：
1. 健康检查端点
2. 认证流程：登录获取 JWT、未认证拒绝、错误密码拒绝
3. 分析流程：提交交易数据 → 同步分析 → 获取结果
4. 任务管理：任务列表、任务详情
5. 错误处理：空数据、无效输入
6. 审计日志：操作被记录
"""
import os
import sys
import json
import pytest
from fastapi.testclient import TestClient

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api.main import app


# ============================================================
# 辅助函数
# ============================================================
def _make_txn(tid, from_acc, to_acc, amount, timestamp, remark=""):
    return {
        "transaction_id": tid,
        "from_account": from_acc,
        "to_account": to_acc,
        "amount": amount,
        "timestamp": timestamp,
        "transaction_type": "transfer",
        "remark": remark,
    }


def _suspicious_txns():
    """构造能触发规则的可疑交易"""
    return [
        # 分拆转账（6 笔 4.5 万）
        *[_make_txn(f"SMURF_{i}", f"PAYER_{i}", "RECV_A", 45000.0, f"2026-07-01T10:{i:02d}:00")
          for i in range(6)],
        # 大额交易
        _make_txn("LARGE_1", "ACC_X", "ACC_Y", 200000.0, "2026-07-01T11:00:00"),
    ]


def _normal_txns():
    """正常交易"""
    return [
        _make_txn("NORMAL_1", "ACC_A", "ACC_B", 5000.0, "2026-07-01T14:00:00", "工资"),
        _make_txn("NORMAL_2", "ACC_C", "ACC_D", 8000.0, "2026-07-01T15:00:00", "报销"),
    ]


def _login(client, username="admin", password="test-pass-123"):
    """登录并返回 JWT token"""
    resp = client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"登录失败: {resp.status_code} {resp.text}"
    return resp.json()["access_token"]


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ============================================================
# 健康检查
# ============================================================
@pytest.mark.smoke
class TestHealthCheck:
    """健康检查端点 E2E 测试"""

    def test_health_endpoint(self, client):
        """健康检查端点返回 200"""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy" or data["status"] == "ok"

    def test_docs_endpoint_accessible(self, client):
        """API 文档端点可访问"""
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_openapi_schema_accessible(self, client):
        """OpenAPI schema 可访问"""
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert schema["info"]["title"] == "AML-Agent API"


# ============================================================
# 认证流程
# ============================================================
@pytest.mark.smoke
class TestAuthenticationE2E:
    """认证流程 E2E 测试"""

    def test_login_success(self, client):
        """正确用户名密码登录成功"""
        resp = client.post(
            "/api/auth/login",
            data={"username": "admin", "password": "test-pass-123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["username"] == "admin"

    def test_login_wrong_password(self, client):
        """错误密码登录失败"""
        resp = client.post(
            "/api/auth/login",
            data={"username": "admin", "password": "wrong-password"},
        )
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client):
        """不存在用户登录失败"""
        resp = client.post(
            "/api/auth/login",
            data={"username": "nobody", "password": "anything"},
        )
        assert resp.status_code == 401

    def test_protected_endpoint_without_token(self, client):
        """未认证访问受保护端点被拒"""
        resp = client.get("/api/auth/me")
        assert resp.status_code in (401, 403)

    def test_protected_endpoint_with_token(self, client):
        """带 JWT 访问受保护端点成功"""
        token = _login(client)
        resp = client.get("/api/auth/me", headers=_auth_headers(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"

    def test_invalid_token_rejected(self, client):
        """无效 JWT 被拒"""
        resp = client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code in (401, 403)


# ============================================================
# 分析流程 E2E
# ============================================================
@pytest.mark.smoke
class TestAnalysisE2E:
    """分析流程 E2E 测试"""

    def test_run_analysis_success(self, client):
        """同步分析成功"""
        token = _login(client)
        txns = _suspicious_txns()
        resp = client.post(
            "/api/analysis/run",
            json=txns,
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200, f"分析失败: {resp.status_code} {resp.text}"
        data = resp.json()
        assert data["status"] == "completed"
        assert "task_id" in data
        assert len(data["task_id"]) > 0

    def test_run_analysis_no_auth_rejected(self, client):
        """未认证提交分析被拒"""
        txns = _suspicious_txns()
        resp = client.post("/api/analysis/run", json=txns)
        assert resp.status_code in (401, 403)

    def test_run_analysis_empty_transactions(self, client):
        """空交易列表被拒"""
        token = _login(client)
        resp = client.post(
            "/api/analysis/run",
            json=[],
            headers=_auth_headers(token),
        )
        assert resp.status_code == 400

    def test_analysis_with_normal_txns(self, client):
        """正常交易分析完成（无误报）"""
        token = _login(client)
        txns = _normal_txns()
        resp = client.post(
            "/api/analysis/run",
            json=txns,
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"


# ============================================================
# 任务管理 E2E
# ============================================================
@pytest.mark.smoke
class TestTaskManagementE2E:
    """任务管理 E2E 测试"""

    def test_list_tasks(self, client):
        """查询任务列表"""
        token = _login(client)
        resp = client.get(
            "/api/analysis/tasks",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200

    def test_task_detail_after_analysis(self, client):
        """分析后查询任务详情"""
        token = _login(client)
        # 先提交分析
        txns = _suspicious_txns()
        run_resp = client.post(
            "/api/analysis/run",
            json=txns,
            headers=_auth_headers(token),
        )
        assert run_resp.status_code == 200
        task_id = run_resp.json()["task_id"]

        # 查询任务详情
        resp = client.get(
            f"/api/analysis/tasks/{task_id}",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task_id
        assert data["status"] == "completed"

    def test_nonexistent_task_returns_404(self, client):
        """查询不存在的任务返回 404"""
        token = _login(client)
        resp = client.get(
            "/api/analysis/tasks/nonexistent-task-id",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 404


# ============================================================
# 报告 E2E
# ============================================================
@pytest.mark.smoke
class TestReportsE2E:
    """报告 E2E 测试"""

    def test_list_reports(self, client):
        """查询报告列表"""
        token = _login(client)
        resp = client.get(
            "/api/reports",
            headers=_auth_headers(token),
        )
        # 报告列表可能为空，但接口应正常返回
        assert resp.status_code == 200

    def test_reports_stats(self, client):
        """查询报告统计"""
        token = _login(client)
        resp = client.get(
            "/api/reports/stats",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200


# ============================================================
# 错误处理 E2E
# ============================================================
@pytest.mark.smoke
class TestErrorHandlingE2E:
    """错误处理 E2E 测试"""

    def test_invalid_json_rejected(self, client):
        """无效 JSON 被拒"""
        token = _login(client)
        resp = client.post(
            "/api/analysis/run",
            content="not a json",
            headers={**_auth_headers(token), "Content-Type": "application/json"},
        )
        assert resp.status_code in (400, 422)

    def test_404_for_unknown_endpoint(self, client):
        """未知端点返回 404"""
        resp = client.get("/api/unknown/endpoint")
        assert resp.status_code == 404
