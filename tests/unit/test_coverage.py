"""报告生成 + 跨境检测 + 边界情况补充测试"""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ============================================================
# Report Generator
# ============================================================
class TestRiskAssessment:
    def test_critical(self):
        from agents.report_generator import _assess_risk_level
        txns = [{"risk_score": 90}, {"risk_score": 85}]
        assert _assess_risk_level(txns) == "critical"

    def test_high(self):
        from agents.report_generator import _assess_risk_level
        txns = [{"risk_score": 75}, {"risk_score": 60}]
        assert _assess_risk_level(txns) == "high"

    def test_medium(self):
        from agents.report_generator import _assess_risk_level
        txns = [{"risk_score": 55}, {"risk_score": 45}]
        assert _assess_risk_level(txns) == "medium"

    def test_low(self):
        from agents.report_generator import _assess_risk_level
        txns = [{"risk_score": 30}]
        assert _assess_risk_level(txns) == "low"

    def test_empty(self):
        from agents.report_generator import _assess_risk_level
        assert _assess_risk_level([]) == "low"


class TestGroupByAccount:
    def test_basic(self):
        from agents.report_generator import _group_by_account
        txns = [
            {"transaction": {"from_account": "A", "to_account": "B"}, "rule_hits": ["大额交易"]},
            {"transaction": {"from_account": "C", "to_account": "A"}, "rule_hits": ["大额交易"]},
        ]
        groups = _group_by_account(txns)
        assert len(groups) > 0

    def test_empty(self):
        from agents.report_generator import _group_by_account
        assert _group_by_account([]) == {}


class TestEvidenceChain:
    def test_generates(self):
        from agents.report_generator import _generate_evidence_chain
        txns = [
            {"evidence": ["evidence_1", "evidence_2"], "rule_hits": ["大额交易"]},
            {"evidence": ["evidence_3"], "rule_hits": ["分拆转账"]},
        ]
        chain = _generate_evidence_chain(txns)
        assert len(chain) >= 2

    def test_empty(self):
        from agents.report_generator import _generate_evidence_chain
        assert _generate_evidence_chain([]) == []


class TestPatternSummary:
    def test_extracts(self):
        from agents.report_generator import _summarize_patterns
        txns = [
            {"rule_hits": ["大额交易", "快进快出"], "evidence": ["e1"]},
            {"rule_hits": ["大额交易"], "evidence": ["e2"]},
        ]
        patterns = _summarize_patterns(txns)
        assert any("大额交易" in p for p in patterns)

    def test_empty(self):
        from agents.report_generator import _summarize_patterns
        assert _summarize_patterns([]) == []


class TestPrimaryAccount:
    def test_most_frequent(self):
        from agents.report_generator import _determine_primary_account
        txns = [
            {"transaction": {"from_account": "A", "to_account": "B"}, "rule_hits": ["大额交易"]},
            {"transaction": {"from_account": "A", "to_account": "C"}, "rule_hits": ["大额交易"]},
        ]
        # primary account is determined from rule_hits + tx pattern
        acc = _determine_primary_account(txns[0]["rule_hits"], txns[0]["transaction"])
        assert isinstance(acc, str) and len(acc) > 0

    def test_empty(self):
        from agents.report_generator import _determine_primary_account
        assert _determine_primary_account([], {}) == "UNKNOWN"


# ============================================================
# Cross-Border 实际场景
# ============================================================
class TestCrossBorderReal:
    def test_foreign_currency_multiple(self):
        """外币交易需达到频繁阈值（5笔/7天）才触发"""
        from agents.rules.cross_border import detect as detect_cb
        txns = [
            {"from_account": "A", "to_account": f"B{i}", "amount": 80000.0,
             "currency": "USD", "transaction_type": "TRANSFER",
             "timestamp": f"2024-01-01 {i:02d}:00:00"}
            for i in range(6)  # ≥5笔触发频繁跨境
        ]
        result = detect_cb(txns)
        assert len(result) > 0

    def test_fx_type_flags(self):
        """FX交易类型直接触发大额换汇"""
        from agents.rules.cross_border import detect as detect_cb
        txns = [
            {"from_account": "A", "to_account": "B", "amount": 80000.0,
             "currency": "CNY", "transaction_type": "fx", "timestamp": "2024-01-01 10:00:00"},
        ]
        result = detect_cb(txns)
        assert len(result) > 0

    def test_high_risk_region(self):
        from agents.rules.cross_border import detect as detect_cb
        txns = [
            {"from_account": "A", "to_account": "B", "amount": 80000.0,
             "currency": "CNY", "counterparty_country": "HK", "transaction_type": "TRANSFER",
             "timestamp": "2024-01-01 10:00:00"},
        ]
        result = detect_cb(txns)
        assert len(result) > 0  # HK is a high-risk region


# ============================================================
# Crypto Pattern 实际场景
# ============================================================
class TestCryptoPatternReal:
    def test_platform_keyword(self):
        from agents.rules.crypto_pattern import detect as detect_crypto
        txns = [
            {"from_account": "A", "to_account": "B", "amount": 50000.0,
             "remark": "币安OTC交易", "timestamp": "2024-01-01 10:00:00"},
        ]
        result = detect_crypto(txns)
        assert isinstance(result, list)


# ============================================================
# 边界情况: 大量数据 + 异常输入
# ============================================================
class TestEdgeCases:
    def test_rule_engine_handles_duplicate_ids(self):
        from agents.rule_engine import create_rule_engine_agent
        txns = [
            {"transaction_id": "TXN_1", "from_account": "A", "to_account": "B",
             "amount": 500000.0, "transaction_type": "TRANSFER", "timestamp": "2024-01-01 10:00:00"},
            {"transaction_id": "TXN_1", "from_account": "A", "to_account": "B",  # duplicate
             "amount": 500000.0, "transaction_type": "TRANSFER", "timestamp": "2024-01-01 10:00:00"},
        ]
        agent_fn = create_rule_engine_agent(llm=None)
        result = agent_fn({"cleaned_transactions": txns})
        # 去重后至少命中1笔
        assert result["rule_hit_count"] >= 1

    def test_rule_engine_handles_missing_amount(self):
        from agents.rule_engine import create_rule_engine_agent
        txns = [
            {"transaction_id": "TXN_1", "from_account": "A", "to_account": "B",
             "transaction_type": "TRANSFER", "timestamp": "2024-01-01 10:00:00"},
        ]
        agent_fn = create_rule_engine_agent(llm=None)
        result = agent_fn({"cleaned_transactions": txns})
        assert result["rule_hits"] == []  # Should skip, not crash

    def test_rule_engine_all_self_transfers(self):
        from agents.rule_engine import create_rule_engine_agent
        txns = [
            {"transaction_id": f"TXN_{i}", "from_account": "A", "to_account": "A",
             "amount": 500000.0, "transaction_type": "TRANSFER", "timestamp": f"2024-01-01 {i:02d}:00:00"}
            for i in range(10)
        ]
        agent_fn = create_rule_engine_agent(llm=None)
        result = agent_fn({"cleaned_transactions": txns})
        # 自转账不应触发分拆/快进快出/对敲
        rules = result.get("rule_details", {})
        assert rules.get("分拆转账", 0) == 0
        assert rules.get("快进快出", 0) == 0
        assert rules.get("对敲交易", 0) == 0

    def test_graph_analyst_empty(self):
        from agents.graph_analyst import _build_graph, _detect_communities
        G = _build_graph([])
        assert len(G.nodes) == 0
        communities = _detect_communities(G)
        assert communities == []

    def test_graph_analyst_centrality(self):
        from agents.graph_analyst import _build_graph, _compute_centrality
        txns = [
            {"from_account": "A", "to_account": "B", "amount": 1000.0, "timestamp": "2024-01-01 10:00:00"},
            {"from_account": "B", "to_account": "C", "amount": 2000.0, "timestamp": "2024-01-01 11:00:00"},
        ]
        G = _build_graph(txns)
        try:
            centrality = _compute_centrality(G)
            assert isinstance(centrality, dict)
        except ModuleNotFoundError:
            pytest.skip("scipy not installed, skipping PageRank test")

    def test_llm_semantic_agent_factory(self):
        """验证LLM语义裁决Agent能正常创建"""
        from agents.llm_semantic_analyzer import create_llm_semantic_agent
        agent = create_llm_semantic_agent(llm=None)
        assert callable(agent)

    def test_data_preprocessor_agent_factory(self):
        from agents.data_preprocessor import create_data_preprocessor_agent
        agent = create_data_preprocessor_agent(llm=None)
        assert callable(agent)

    def test_baseline_computation_large_data(self):
        """大量数据下的基线计算不崩溃"""
        from agents.data_preprocessor import _compute_account_baselines
        txns = [
            {"from_account": f"A{i%100}", "to_account": f"B{i%100}",
             "amount": float(i * 1000), "timestamp": f"2024-01-{(i//24)+1:02d} {i%24:02d}:00:00"}
            for i in range(500)
        ]
        baselines = _compute_account_baselines(txns)
        assert len(baselines) > 0
        for acc, bl in baselines.items():
            assert "total_txns" in bl
            assert "avg_amount" in bl
