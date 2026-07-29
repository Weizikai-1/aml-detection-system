"""
LLM 语义分析 Agent 单元测试

测试覆盖:
1. 降级语义检测(_fallback_semantic_check)
2. 降级混合裁决(_fallback_hybrid)
3. 降级报告生成(_generate_fallback_report)
4. Agent 节点函数(create_llm_semantic_agent)
"""
import pytest
from agents.llm_semantic_analyzer import (
    _fallback_semantic_check,
    _fallback_hybrid,
    _generate_fallback_report,
    create_llm_semantic_agent,
)


def _make_txn(
    tid="T1", frm="ACC_A", to="ACC_B", amount=50000.0,
    ts="2026-07-01T10:00:00", remark="货款",
):
    """构造测试交易"""
    return {
        "transaction_id": tid,
        "from_account": frm,
        "to_account": to,
        "amount": amount,
        "timestamp": ts,
        "remark": remark,
    }


def _make_suspicious(
    risk_score=70, rule_hits=None, txn=None,
):
    """构造可疑交易对象"""
    return {
        "transaction": txn or _make_txn(),
        "rule_hits": rule_hits if rule_hits is not None else ["大额交易"],
        "risk_score": risk_score,
        "evidence": [],
        "graph_evidence": None,
    }


# ============================================================
# 1. _fallback_semantic_check
# ============================================================
@pytest.mark.unit
class TestFallbackSemanticCheck:
    def test_normal_transaction(self):
        """正常交易返回 anomaly_detected=False"""
        txn = _make_txn(amount=8000, remark="货款", ts="2026-07-01T14:00:00")
        result = _fallback_semantic_check(txn)

        assert result["anomaly_detected"] is False
        assert result["risk_amplification"] == 1.0

    def test_amount_mismatch(self):
        """金额-备注不匹配检测: '工资'金额200000远超上限50000*3"""
        txn = _make_txn(amount=200000, remark="工资")
        result = _fallback_semantic_check(txn)

        assert result["anomaly_detected"] is True
        assert result["anomaly_type"] == "amount_mismatch"
        assert "工资" in result["explanation"]
        assert result["risk_amplification"] == 1.2

    def test_time_mismatch(self):
        """时间-业务不匹配检测: 凌晨大额交易"""
        txn = _make_txn(amount=80000, remark="货款", ts="2026-07-01 03:00:00")
        result = _fallback_semantic_check(txn)

        assert result["anomaly_detected"] is True
        assert result["anomaly_type"] == "time_mismatch"
        assert "凌晨" in result["explanation"]

    def test_no_remark(self):
        """无备注交易不误报"""
        txn = _make_txn(amount=60000, remark="", ts="2026-07-01T10:00:00")
        result = _fallback_semantic_check(txn)

        assert result["anomaly_detected"] is False


# ============================================================
# 2. _fallback_hybrid
# ============================================================
@pytest.mark.unit
class TestFallbackHybrid:
    def test_all_high_risk_anomaly(self):
        """三系统全高风险→suspicious, confidence=0.90"""
        result = _fallback_hybrid(
            rule_score=80, gnn_score=75,
            semantic_result={"anomaly_detected": True},
            rule_hits=["大额交易"],
        )

        assert result["final_verdict"] == "suspicious"
        assert result["confidence"] == 0.90

    def test_rule_gnn_high_no_anomaly(self):
        """规则+GNN高风险但无语义异常→suspicious, confidence=0.75"""
        result = _fallback_hybrid(
            rule_score=80, gnn_score=75,
            semantic_result={"anomaly_detected": False},
            rule_hits=["大额交易"],
        )

        assert result["final_verdict"] == "suspicious"
        assert result["confidence"] == 0.75

    def test_rule_high_anomaly(self):
        """规则高风险+语义异常→suspicious, confidence=0.65"""
        result = _fallback_hybrid(
            rule_score=80, gnn_score=50,
            semantic_result={"anomaly_detected": True},
            rule_hits=["大额交易"],
        )

        assert result["final_verdict"] == "suspicious"
        assert result["confidence"] == 0.65

    def test_rule_or_gnn_high(self):
        """仅规则或GNN高风险→needs_review, confidence=0.50"""
        # 仅规则高风险
        result_a = _fallback_hybrid(
            rule_score=80, gnn_score=50,
            semantic_result={"anomaly_detected": False},
            rule_hits=["大额交易"],
        )
        assert result_a["final_verdict"] == "needs_review"
        assert result_a["confidence"] == 0.50

        # 仅GNN高风险
        result_b = _fallback_hybrid(
            rule_score=50, gnn_score=80,
            semantic_result={"anomaly_detected": False},
            rule_hits=[],
        )
        assert result_b["final_verdict"] == "needs_review"
        assert result_b["confidence"] == 0.50

    def test_all_low(self):
        """三系统全低风险→normal, confidence=0.80"""
        result = _fallback_hybrid(
            rule_score=30, gnn_score=40,
            semantic_result={"anomaly_detected": False},
            rule_hits=[],
        )

        assert result["final_verdict"] == "normal"
        assert result["confidence"] == 0.80

    def test_combined_score_calculation(self):
        """combined_score 计算正确: rule*0.4 + gnn*0.35 + anomaly*0.25"""
        # anomaly_detected=True → anomaly_score=80
        result = _fallback_hybrid(
            rule_score=80, gnn_score=60,
            semantic_result={"anomaly_detected": True},
            rule_hits=["大额交易"],
        )
        expected = 80 * 0.4 + 60 * 0.35 + 80 * 0.25  # 32+21+20 = 73
        assert result["combined_score"] == pytest.approx(expected, abs=0.1)

        # anomaly_detected=False → anomaly_score=30
        result2 = _fallback_hybrid(
            rule_score=80, gnn_score=60,
            semantic_result={"anomaly_detected": False},
            rule_hits=["大额交易"],
        )
        expected2 = 80 * 0.4 + 60 * 0.35 + 30 * 0.25  # 32+21+7.5 = 60.5
        assert result2["combined_score"] == pytest.approx(expected2, abs=0.1)

    def test_contributing_signals(self):
        """contributing_signals 包含三个来源及权重"""
        result = _fallback_hybrid(
            rule_score=70, gnn_score=60,
            semantic_result={"anomaly_detected": True},
            rule_hits=["大额交易"],
        )
        signals = result["contributing_signals"]

        sources = {s["source"] for s in signals}
        assert sources == {"rule_engine", "gnn_model", "semantic"}

        weights = {s["source"]: s["weight"] for s in signals}
        assert weights["rule_engine"] == 0.40
        assert weights["gnn_model"] == 0.35
        assert weights["semantic"] == 0.25


# ============================================================
# 3. _generate_fallback_report
# ============================================================
@pytest.mark.unit
class TestGenerateFallbackReport:
    def _make_report_data(self):
        """构造报告测试数据"""
        suspicious = [
            {"transaction": _make_txn(tid="T1", amount=100000, remark="工资"), "risk_score": 80},
            {"transaction": _make_txn(tid="T2", amount=50000, remark="货款"), "risk_score": 60},
        ]
        adjudications = [
            {"final_verdict": "suspicious", "combined_score": 75},
            {"final_verdict": "needs_review", "combined_score": 55},
        ]
        return suspicious, adjudications

    def test_report_contains_summary(self):
        """报告包含摘要信息"""
        suspicious, adjudications = self._make_report_data()
        report = _generate_fallback_report(suspicious, adjudications)

        assert "执行摘要" in report
        assert "2" in report  # 检测可疑交易数

    def test_report_contains_table(self):
        """报告包含交易表格"""
        suspicious, adjudications = self._make_report_data()
        report = _generate_fallback_report(suspicious, adjudications)

        assert "交易ID" in report
        assert "T1" in report
        assert "T2" in report

    def test_report_degraded_note(self):
        """报告包含降级模式说明"""
        suspicious, adjudications = self._make_report_data()
        report = _generate_fallback_report(suspicious, adjudications)

        assert "降级模式" in report


# ============================================================
# 4. create_llm_semantic_agent (Agent 节点)
# ============================================================
@pytest.mark.unit
class TestCreateLLMSemanticAgent:
    def test_no_suspicious_transactions(self):
        """无可疑交易时返回空结果"""
        agent = create_llm_semantic_agent(llm=None)
        result = agent({"graph_suspicious": []})

        assert result["semantic_results"] == []
        assert result["adjudications"] == []
        assert result["risk_report"] == "# 无待分析交易"

    def test_with_suspicious_fallback(self):
        """有可疑交易且LLM=None时走降级路径"""
        txn = _make_txn(amount=200000, remark="工资", ts="2026-07-01T03:00:00")
        suspicious = [_make_suspicious(risk_score=80, txn=txn)]
        agent = create_llm_semantic_agent(llm=None)
        result = agent({"graph_suspicious": suspicious})

        assert len(result["semantic_results"]) == 1
        assert len(result["adjudications"]) == 1
        # 降级路径: 裁决结果带 _fallback 标记
        assert result["adjudications"][0]["_fallback"] is True
        # 降级报告包含降级模式说明
        assert "降级模式" in result["risk_report"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
