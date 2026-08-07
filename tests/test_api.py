"""FastAPI 接口测试 — TestClient 集成测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from api import app

client = TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_200(self):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_json_structure(self):
        response = client.get("/health")
        data = response.json()
        assert "status" in data
        assert data["status"] == "ok"
        assert "timestamp" in data
        assert "langgraph" in data
        assert "llm_available" in data
        assert "gnn_available" in data
        assert isinstance(data["langgraph"], bool)
        assert isinstance(data["llm_available"], bool)
        assert isinstance(data["gnn_available"], bool)

    def test_health_content_type(self):
        response = client.get("/health")
        assert "application/json" in response.headers["content-type"]


class TestDetectEndpoint:
    def test_detect_basic(self):
        """基本检测请求"""
        response = client.post("/detect", json={"n_samples": 50, "demo_mode": False})
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert "timestamp" in data
        assert "data_summary" in data
        assert "rule_summary" in data
        assert "gnn_report" in data
        assert "llm_count" in data
        assert "compliance" in data
        assert "report_preview" in data

    def test_detect_demo_mode(self):
        """Demo 模式检测请求"""
        response = client.post("/detect", json={"n_samples": 50, "demo_mode": True})
        assert response.status_code == 200
        data = response.json()
        # Demo 模式应有更多命中
        assert data["rule_summary"]["total_hits"] >= 0
        # 合规评分应在合理范围
        assert 0 <= data["compliance"]["score"] <= 100

    def test_detect_data_summary(self):
        """数据摘要字段完整性"""
        response = client.post("/detect", json={"n_samples": 100, "demo_mode": False})
        data = response.json()
        ds = data["data_summary"]
        assert ds["total"] == 100
        assert "fraud" in ds
        assert "fraud_rate" in ds
        assert "source" in ds

    def test_detect_rule_summary(self):
        """规则命中摘要字段完整性"""
        response = client.post("/detect", json={"n_samples": 100, "demo_mode": True})
        data = response.json()
        rs = data["rule_summary"]
        assert "total_hits" in rs
        assert "high_risk" in rs
        assert "medium_risk" in rs
        assert "low_risk" in rs

    def test_detect_gnn_report(self):
        """GNN 报告字段完整性"""
        response = client.post("/detect", json={"n_samples": 100, "demo_mode": False})
        data = response.json()
        gn = data["gnn_report"]
        assert "f1" in gn
        assert "precision" in gn
        assert "recall" in gn
        assert "enabled" in gn

    def test_detect_compliance(self):
        """合规审核字段完整性"""
        response = client.post("/detect", json={"n_samples": 100, "demo_mode": True})
        data = response.json()
        comp = data["compliance"]
        assert "passed" in comp
        assert "score" in comp
        assert "status" in comp

    def test_detect_report_preview(self):
        """报告预览应为非空字符串"""
        response = client.post("/detect", json={"n_samples": 100, "demo_mode": True})
        data = response.json()
        assert isinstance(data["report_preview"], str)
        assert len(data["report_preview"]) > 0

    def test_detect_llm_count(self):
        """LLM 深审数量应为整数"""
        response = client.post("/detect", json={"n_samples": 100, "demo_mode": True})
        data = response.json()
        assert isinstance(data["llm_count"], int)
        assert data["llm_count"] >= 0


class TestDetectEdgeCases:
    def test_minimum_samples(self):
        """最小样本数"""
        response = client.post("/detect", json={"n_samples": 50, "demo_mode": False})
        assert response.status_code == 200

    def test_detect_without_demo_mode(self):
        """非 Demo 模式"""
        response = client.post("/detect", json={"n_samples": 100, "demo_mode": False})
        assert response.status_code == 200
        data = response.json()
        assert data["llm_count"] >= 0

    def test_detect_different_sample_sizes(self):
        """不同样本数"""
        for n in [50, 100]:
            response = client.post("/detect", json={"n_samples": n, "demo_mode": False})
            assert response.status_code == 200
            assert response.json()["data_summary"]["total"] == n


class TestReportEndpoint:
    def test_report_endpoint(self):
        """报告端点 — 根据文件是否存在返回 200 或 404"""
        import os
        report_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "reports", "aml_report.md"
        )
        response = client.get("/report/test_id")
        if os.path.exists(report_path):
            assert response.status_code == 200
            data = response.json()
            assert "report" in data
        else:
            assert response.status_code == 404
            data = response.json()
            assert "error" in data
