"""
测试 State 定义
"""
import pytest
from graph.state import AMLState, Transaction


def test_transaction_type():
    """测试 Transaction 类型定义"""
    txn: Transaction = {
        "transaction_id": "TXN001",
        "from_account": "A001",
        "to_account": "A002",
        "amount": 50000.0,
        "timestamp": "2026-07-24",
        "transaction_type": "transfer",
        "remark": "货款",
        "is_suspicious": False,
        "suspicious_reason": None,
        "risk_score": None,
    }
    assert txn["transaction_id"] == "TXN001"
    assert txn["amount"] == 50000.0


def test_aml_state_type():
    """测试 AMLState 类型定义"""
    state: AMLState = {
        "transactions": [],
        "analysis_date": "2026-07-24",
        "analysis_params": {},
        "cleaned_transactions": [],
        "transaction_features": {},
        "preprocessing_stats": {},
        "rule_hits": [],
        "rule_hit_count": 0,
        "rule_details": {},
        "rule_engine_stats": {},
        "graph_data": {},
        "graph_suspicious": [],
        "graph_hit_count": 0,
        "llm_reviewed": [],
        "llm_confirmed": [],
        "false_positives": [],
        "llm_analysis_count": 0,
        "llm_stats": {},
        "str_reports": [],
        "report_count": 0,
        "report_generation_stats": {},
        "final_reports": [],
        "rejected_reports": [],
        "human_review_tasks": [],
        "compliance_stats": {},
        "compliance_summary": "",
        "messages": [],
        "current_step": "init",
        "error": "",
        "total_processing_time": 0.0,
        "step_times": {},
        "execution_id": "test-001",
    }
    assert state["analysis_date"] == "2026-07-24"
    assert state["analysis_params"] == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
