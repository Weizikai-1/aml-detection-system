"""规则引擎核心规则单元测试"""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agents.rules.large_amount import detect as detect_large_amount
from agents.rules.smurfing import detect as detect_smurfing
from agents.rules.fast_in_fast_out import detect as detect_fast_in_fast_out
from agents.rules.round_trip import detect as detect_round_trip


class TestLargeAmount:
    def test_hit_large(self, sample_transactions):
        result = detect_large_amount(sample_transactions)
        hit_ids = {s["transaction"]["transaction_id"] for s in result}
        assert "TXN_00000001" in hit_ids  # 500k
        assert "TXN_00000002" in hit_ids  # 300k
        assert "TXN_00000004" in hit_ids  # 480k

    def test_no_hit_small(self):
        txns = [{"transaction_id": "T", "amount": 1000.0, "transaction_type": "PAYMENT"}]
        result = detect_large_amount(txns)
        assert len(result) == 0

    def test_self_transfer_tagged(self):
        """自转账大额应标注但不错过"""
        txns = [{"transaction_id": "T", "from_account": "A", "to_account": "A", "amount": 200000.0}]
        result = detect_large_amount(txns)
        assert len(result) == 1
        assert "自转账" in result[0]["evidence"][0]

    def test_empty_list(self):
        assert detect_large_amount([]) == []

    def test_amount_none(self):
        txns = [{"transaction_id": "T", "amount": None}]
        result = detect_large_amount(txns)
        assert len(result) == 0


class TestSmurfing:
    def test_detect_smurfing_pattern(self, smurfing_transactions):
        result = detect_smurfing(smurfing_transactions)
        assert len(result) == 6  # All 6 are in the smurfing window

    def test_no_smurfing_few_payers(self):
        """少于5个不同付款方不触发"""
        txns = [
            {"transaction_id": f"TS{i}", "from_account": "PAYER_A", "to_account": "TGT",
             "amount": 45000.0, "timestamp": f"2024-01-01 10:{i:02d}:00"}
            for i in range(10)
        ]
        result = detect_smurfing(txns)
        assert len(result) == 0  # All from same payer → not smurfing

    def test_smurfing_self_transfer_excluded(self):
        """自转账不参与分拆检测"""
        txns = [
            {"transaction_id": f"TS{i}", "from_account": f"P{i}", "to_account": "TARGET",
             "amount": 45000.0, "timestamp": f"2024-01-01 10:{i:02d}:00"}
            for i in range(4)
        ]
        txns.append({"transaction_id": "TS4", "from_account": "TARGET", "to_account": "TARGET",
                     "amount": 45000.0, "timestamp": "2024-01-01 10:05:00"})
        result = detect_smurfing(txns)
        # Only 4 valid payers, min_count=5 → 0 hits
        assert len(result) == 0


class TestFastInFastOut:
    def test_detect_pattern(self, fast_in_fast_out_transactions):
        result = detect_fast_in_fast_out(fast_in_fast_out_transactions)
        assert len(result) >= 1
        rules_hit = []
        for s in result:
            rules_hit.extend(s["rule_hits"])
        assert "快进快出" in rules_hit

    def test_no_pattern_slow_out(self):
        """出账超过10分钟不触发"""
        txns = [
            {"transaction_id": "T0", "from_account": "X", "to_account": "HUB",
             "amount": 100000.0, "timestamp": "2024-01-01 10:00:00"},
            {"transaction_id": "T1", "from_account": "HUB", "to_account": "Y",
             "amount": 96000.0, "timestamp": "2024-01-01 11:00:00"},  # 1 hour later
        ]
        result = detect_fast_in_fast_out(txns)
        assert len(result) == 0

    def test_self_transfer_excluded(self):
        """自转账不触发"""
        txns = [
            {"transaction_id": "T0", "from_account": "A", "to_account": "A",
             "amount": 100000.0, "timestamp": "2024-01-01 10:00:00"},
        ]
        result = detect_fast_in_fast_out(txns)
        assert len(result) == 0


class TestRoundTrip:
    def test_detect_round_trip(self):
        txns = [
            {"transaction_id": "RT0", "from_account": "A", "to_account": "B",
             "amount": 100000.0, "timestamp": "2024-01-01 10:00:00"},
            {"transaction_id": "RT1", "from_account": "B", "to_account": "A",
             "amount": 105000.0, "timestamp": "2024-01-03 10:00:00"},  # 5% diff
        ]
        result = detect_round_trip(txns)
        assert len(result) == 2

    def test_no_round_trip_large_diff(self):
        txns = [
            {"transaction_id": "RT0", "from_account": "A", "to_account": "B",
             "amount": 100000.0, "timestamp": "2024-01-01 10:00:00"},
            {"transaction_id": "RT1", "from_account": "B", "to_account": "A",
             "amount": 150000.0, "timestamp": "2024-01-03 10:00:00"},  # 50% diff
        ]
        result = detect_round_trip(txns)
        assert len(result) == 0

    def test_self_transfer_excluded(self):
        txns = [
            {"transaction_id": "RT0", "from_account": "A", "to_account": "A",
             "amount": 100000.0, "timestamp": "2024-01-01 10:00:00"},
        ]
        result = detect_round_trip(txns)
        assert len(result) == 0


class TestRuleEngineIntegration:
    def test_full_pipeline(self, sample_transactions):
        """集成测试：4条核心规则同时运行"""
        from agents.rule_engine import create_rule_engine_agent
        agent_fn = create_rule_engine_agent(llm=None)
        state = {"cleaned_transactions": sample_transactions}
        result = agent_fn(state)

        assert "rule_hits" in result
        assert "rule_details" in result
        assert result["rule_hit_count"] > 0  # At least large amount hits
        assert result["current_step"] == "rule_engine"

    def test_empty_transactions(self):
        from agents.rule_engine import create_rule_engine_agent
        agent_fn = create_rule_engine_agent(llm=None)
        result = agent_fn({"cleaned_transactions": []})
        assert result["rule_hits"] == []
        assert result["rule_hit_count"] == 0
