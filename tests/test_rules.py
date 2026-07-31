"""规则引擎测试 — 20条规则"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from rules import (large_amount, smurfing, fast_in_fast_out, remark_keywords,
                   cross_border, balance_drain, round_amount, night_activity)
from rule_engine import run_engine, summary


class TestLargeAmount:
    def test_triggers_above_threshold(self):
        txns = [{"amount": 200000, "type": "TRANSFER", "nameOrig": "A", "nameDest": "B"}]
        hits = large_amount(txns)
        assert len(hits) == 1
        assert hits[0]["rule"] == "large_amount"

    def test_ignores_below_threshold(self):
        txns = [{"amount": 5000, "type": "TRANSFER", "nameOrig": "A", "nameDest": "B"}]
        hits = large_amount(txns)
        assert len(hits) == 0


class TestRemarkKeywords:
    def test_high_risk_keyword(self):
        txns = [{"remark": "代付货款", "amount": 10000}]
        hits = remark_keywords(txns)
        assert len(hits) >= 1
        assert "代付" in hits[0]["evidence"][0]

    def test_low_risk_keyword_discount(self):
        txns = [{"remark": "工资发放", "amount": 10000}]
        hits = remark_keywords(txns)
        if hits:
            assert hits[0]["risk_score"] < 55


class TestRuleEngine:
    def test_run_engine_returns_list(self):
        txns = [{"amount": 500000, "type": "TRANSFER", "nameOrig": "A", "nameDest": "B"}]
        hits = run_engine(txns)
        assert isinstance(hits, list)
        assert len(hits) >= 1

    def test_summary_counts(self):
        hits = [{"risk_score": 80, "rules": ["large_amount"], "evidence": ["test"]}]
        s = summary(hits)
        assert s["total_hits"] == 1
        assert s["high_risk"] == 1


class TestEmptyInput:
    def test_empty_transactions(self):
        assert large_amount([]) == []
        assert smurfing([]) == []
        assert fast_in_fast_out([]) == []
        assert run_engine([]) == []


class TestNewRules:
    """P0 新增规则测试"""
    def test_balance_drain_triggers(self):
        txns = [{"amount": 50000, "oldbalanceOrg": 55000, "newbalanceOrig": 3000,
                 "nameOrig": "A", "step": 1, "type": "TRANSFER", "nameDest": "B"}]
        hits = balance_drain(txns)
        assert len(hits) >= 1
        assert hits[0]["rule"] == "balance_drain"

    def test_balance_drain_ignores_normal(self):
        txns = [{"amount": 5000, "oldbalanceOrg": 100000, "newbalanceOrig": 95000,
                 "nameOrig": "A", "step": 1, "type": "TRANSFER", "nameDest": "B"}]
        hits = balance_drain(txns)
        assert len(hits) == 0

    def test_round_amount_detects(self):
        txns = [{"amount": 100000, "nameOrig": "A", "step": i, "type": "TRANSFER"}
                for i in range(5)]
        hits = round_amount(txns)
        assert len(hits) == 1
        assert "round_amount" in hits[0]["rule"]

    def test_night_activity_silent_daytime(self):
        txns = [{"amount": 60000, "nameOrig": "A", "step": 12, "type": "TRANSFER"}
                for _ in range(5)]  # 中午12点
        hits = night_activity(txns)
        assert len(hits) == 0

    def test_all_rules_registered(self):
        from rules import ALL_RULES
        assert len(ALL_RULES) == 20, f"应有20条规则，实际{len(ALL_RULES)}条"
        names = [r[0] for r in ALL_RULES]
        assert "circular_flow" in names
        assert "structuring" in names
        assert "balance_drain" in names
