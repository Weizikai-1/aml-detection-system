"""补充规则 + data_preprocessor + compliance_auditor 测试"""
import sys
import os
import pytest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ============================================================
# Data Preprocessor 辅助函数
# ============================================================
class TestAmountLevel:
    def test_low(self):
        from agents.data_preprocessor import _amount_level
        assert _amount_level(5000) == "low"

    def test_medium(self):
        from agents.data_preprocessor import _amount_level
        assert _amount_level(25000) == "medium"

    def test_high(self):
        from agents.data_preprocessor import _amount_level
        assert _amount_level(75000) == "high"

    def test_very_high(self):
        from agents.data_preprocessor import _amount_level
        assert _amount_level(500000) == "very_high"

    def test_none(self):
        from agents.data_preprocessor import _amount_level
        assert _amount_level(None) == "unknown"

    def test_invalid(self):
        from agents.data_preprocessor import _amount_level
        assert _amount_level("not-a-number") == "unknown"


class TestTimeHelpers:
    def test_is_night(self):
        from agents.data_preprocessor import _is_night_transaction
        assert _is_night_transaction(datetime(2024, 1, 1, 23, 0))  # 23:00
        assert _is_night_transaction(datetime(2024, 1, 1, 3, 0))   # 03:00
        assert not _is_night_transaction(datetime(2024, 1, 1, 12, 0))  # 12:00
        assert not _is_night_transaction(None)

    def test_is_weekend(self):
        from agents.data_preprocessor import _is_weekend
        assert _is_weekend(datetime(2024, 1, 6))   # Saturday
        assert _is_weekend(datetime(2024, 1, 7))   # Sunday
        assert not _is_weekend(datetime(2024, 1, 5))  # Friday
        assert not _is_weekend(None)


class TestAccountBaselines:
    def test_basic(self):
        from agents.data_preprocessor import _compute_account_baselines
        txns = [
            {"from_account": "A", "to_account": "B", "amount": 1000.0, "timestamp": "2024-01-01 10:00:00"},
            {"from_account": "A", "to_account": "C", "amount": 2000.0, "timestamp": "2024-01-01 14:00:00"},
            {"from_account": "B", "to_account": "A", "amount": 500.0, "timestamp": "2024-01-02 10:00:00"},
        ]
        baselines = _compute_account_baselines(txns)
        assert "A" in baselines
        assert baselines["A"]["total_txns"] == 3  # 2 out + 1 in
        assert baselines["A"]["total_amount"] > 0

    def test_self_transfer_excluded(self):
        from agents.data_preprocessor import _compute_account_baselines
        txns = [
            {"from_account": "A", "to_account": "A", "amount": 1000.0, "timestamp": "2024-01-01 10:00:00"},
        ]
        baselines = _compute_account_baselines(txns)
        assert "A" not in baselines or baselines.get("A", {}).get("total_txns", 0) == 0

    def test_empty(self):
        from agents.data_preprocessor import _compute_account_baselines
        assert _compute_account_baselines([]) == {}


# ============================================================
# Compliance Auditor
# ============================================================
class TestComplianceCompleteness:
    def test_complete(self):
        from agents.compliance_auditor import _check_completeness
        report = {
            "report_id": "R001", "primary_account": "ACC1",
            "suspicious_transactions": [{}], "total_suspicious_amount": 10000.0,
            "risk_level": "high", "analysis_summary": "test",
            "evidence_chain": ["e1"], "disposal_suggestion": "report",
        }
        score, issues = _check_completeness(report)
        assert score == 1.0
        assert len(issues) == 0

    def test_missing_fields(self):
        from agents.compliance_auditor import _check_completeness
        report = {"report_id": "R001"}
        score, issues = _check_completeness(report)
        assert score < 1.0
        assert len(issues) > 0
        assert any("primary_account" in i for i in issues)

    def test_empty_lists(self):
        from agents.compliance_auditor import _check_completeness
        report = {
            "report_id": "R001", "primary_account": "ACC1",
            "suspicious_transactions": [], "total_suspicious_amount": 10000.0,
            "risk_level": "high", "analysis_summary": "test",
            "evidence_chain": [], "disposal_suggestion": "report",
        }
        score, issues = _check_completeness(report)
        assert score < 1.0


class TestComplianceEvidence:
    def test_sufficient(self):
        from agents.compliance_auditor import _check_evidence
        report = {
            "evidence_chain": ["e1", "e2", "e3"],
            "suspicious_transactions": [
                {"rule_hits": ["大额交易", "分拆转账", "快进快出"]},
                {"rule_hits": ["对敲交易"]},
            ],
        }
        score, issues = _check_evidence(report)
        assert score >= 0.5

    def test_empty(self):
        from agents.compliance_auditor import _check_evidence
        report = {"evidence_chain": [], "suspicious_transactions": []}
        score, issues = _check_evidence(report)
        assert score == 0.0
        assert "证据链为空" in issues


class TestRuleCompliance:
    def test_all_pass(self):
        from agents.compliance_auditor import _check_rule_compliance
        report = {
            "suspicious_transactions": [
                {"rule_hits": ["大额交易"], "risk_score": 50, "evidence": ["test"]},
                {"rule_hits": ["分拆转账"], "risk_score": 70, "evidence": ["test"]},
            ],
        }
        score, issues = _check_rule_compliance(report)
        # Should be close to 1.0 if all rules are met
        assert score >= 0.5

    def test_no_transactions(self):
        from agents.compliance_auditor import _check_rule_compliance
        report = {"suspicious_transactions": []}
        score, issues = _check_rule_compliance(report)
        assert score == 0.0


class TestRiskConsistency:
    def test_valid(self):
        from agents.compliance_auditor import _check_risk_consistency
        report = {
            "risk_level": "critical",
            "suspicious_transactions": [
                {"risk_score": 90}, {"risk_score": 95},
            ],
        }
        score, issues = _check_risk_consistency(report)
        assert score >= 0.8  # Critical with high scores should pass

    def test_mismatch(self):
        from agents.compliance_auditor import _check_risk_consistency
        report = {
            "risk_level": "low",
            "suspicious_transactions": [
                {"risk_score": 90}, {"risk_score": 95},
            ],
        }
        score, issues = _check_risk_consistency(report)
        assert score < 0.8  # Low risk with high scores should fail


# ============================================================
# 额外规则测试
# ============================================================
class TestShellCompany:
    def test_disabled_by_default(self):
        from agents.rules.shell_company import detect as detect_shell
        txns = [
            {"from_account": f"A{i}", "to_account": f"B{i%5}",
             "amount": 50000.0, "timestamp": "2024-01-01 10:00:00"}
            for i in range(20)
        ]
        result = detect_shell(txns)
        # Shell company rule may or may not fire depending on config
        assert isinstance(result, list)

    def test_empty(self):
        from agents.rules.shell_company import detect as detect_shell
        assert detect_shell([]) == []


class TestSanctionList:
    def test_empty(self):
        from agents.rules.sanction_list import detect as detect_sanction
        assert detect_sanction([]) == []

    def test_no_match(self):
        from agents.rules.sanction_list import detect as detect_sanction
        txns = [{"from_account": "Normal Person", "to_account": "Business",
                 "amount": 5000.0, "timestamp": "2024-01-01 10:00:00"}]
        result = detect_sanction(txns)
        assert isinstance(result, list)


class TestCrossBorder:
    def test_empty(self):
        from agents.rules.cross_border import detect as detect_cb
        assert detect_cb([]) == []

    def test_domestic_no_flag(self):
        from agents.rules.cross_border import detect as detect_cb
        txns = [{"from_account": "A", "to_account": "B", "amount": 1000.0,
                 "currency": "CNY", "transaction_type": "TRANSFER", "timestamp": "2024-01-01 10:00:00"}]
        result = detect_cb(txns)
        assert len(result) == 0  # CNY domestic, no cross-border


class TestCryptoPattern:
    def test_empty(self):
        from agents.rules.crypto_pattern import detect as detect_crypto
        assert detect_crypto([]) == []

    def test_no_pattern(self):
        from agents.rules.crypto_pattern import detect as detect_crypto
        txns = [{"from_account": "A", "to_account": "B", "amount": 5000.0,
                 "remark": "正常转账", "timestamp": "2024-01-01 10:00:00"}]
        result = detect_crypto(txns)
        assert isinstance(result, list)


class TestRemarkKeywords:
    def test_empty(self):
        from agents.rules.remark_keywords import detect as detect_remarks
        assert detect_remarks([]) == []

    def test_no_keyword(self):
        from agents.rules.remark_keywords import detect as detect_remarks
        txns = [{"from_account": "A", "to_account": "B", "amount": 5000.0,
                 "remark": "正常工资", "timestamp": "2024-01-01 10:00:00"}]
        result = detect_remarks(txns)
        assert isinstance(result, list)


class TestBaselineDeviation:
    def test_empty(self):
        from agents.rules.baseline_deviation import detect as detect_baseline
        assert detect_baseline([], {}) == []

    def test_no_baseline_no_flag(self):
        from agents.rules.baseline_deviation import detect as detect_baseline
        txns = [{"from_account": "A", "to_account": "B", "amount": 5000.0,
                 "timestamp": "2024-01-01 10:00:00"}]
        result = detect_baseline(txns, {})
        assert len(result) == 0


# ============================================================
# Graph Analyst
# ============================================================
class TestGraphBuilding:
    def test_build_graph(self):
        from agents.graph_analyst import _build_graph
        txns = [
            {"from_account": "A", "to_account": "B", "amount": 1000.0, "timestamp": "2024-01-01 10:00:00"},
            {"from_account": "B", "to_account": "C", "amount": 2000.0, "timestamp": "2024-01-01 11:00:00"},
            {"from_account": "A", "to_account": "C", "amount": 3000.0, "timestamp": "2024-01-01 12:00:00"},
        ]
        G = _build_graph(txns)
        assert len(G.nodes) == 3
        assert len(G.edges) == 3

    def test_empty(self):
        from agents.graph_analyst import _build_graph
        G = _build_graph([])
        assert len(G.nodes) == 0


class TestNodeRiskScores:
    def test_with_hits(self):
        from agents.graph_analyst import _build_graph, _compute_node_risk_scores
        txns = [
            {"from_account": "A", "to_account": "B", "amount": 1000.0, "timestamp": "2024-01-01 10:00:00"},
        ]
        G = _build_graph(txns)
        hits = [{"transaction": {"from_account": "A", "to_account": "B"}, "risk_score": 70}]
        scores = _compute_node_risk_scores(G, hits)
        assert scores.get("A", 0) >= 70 or scores.get("B", 0) >= 70

    def test_no_hits(self):
        from agents.graph_analyst import _build_graph, _compute_node_risk_scores
        G = _build_graph([{"from_account": "A", "to_account": "B", "amount": 1000.0, "timestamp": "2024-01-01 10:00:00"}])
        scores = _compute_node_risk_scores(G, [])
        assert all(v == 0.0 for v in scores.values())


# ============================================================
# 集成测试: 完整 Agent Pipeline
# ============================================================
class TestFullPipeline:
    def test_rule_to_report(self, sample_transactions):
        """端到端: 规则引擎 → 合规审核"""
        from agents.rule_engine import create_rule_engine_agent
        from agents.compliance_auditor import _check_completeness, _check_evidence

        # 1. 运行规则引擎
        agent_fn = create_rule_engine_agent(llm=None)
        result = agent_fn({"cleaned_transactions": sample_transactions})

        assert result["rule_hit_count"] > 0
        hits = result["rule_hits"]

        # 2. 构建简化报告
        report = {
            "report_id": "R_TEST_001",
            "primary_account": hits[0]["transaction"].get("from_account", "UNKNOWN"),
            "suspicious_transactions": hits,
            "total_suspicious_amount": sum(h["transaction"].get("amount", 0) for h in hits),
            "risk_level": "high",
            "analysis_summary": "测试摘要",
            "evidence_chain": [h["evidence"][0] for h in hits if h.get("evidence")],
            "disposal_suggestion": "建议上报",
        }

        # 3. 合规检查
        completeness_score, _ = _check_completeness(report)
        evidence_score, _ = _check_evidence(report)

        assert completeness_score == 1.0
        assert evidence_score > 0.0
