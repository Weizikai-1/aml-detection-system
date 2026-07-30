"""配置管理单元测试"""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import AML_CONFIG


class TestAMLConfig:
    def test_config_loaded(self):
        assert AML_CONFIG is not None
        assert isinstance(AML_CONFIG, dict)

    def test_rules_config_exists(self):
        rules = AML_CONFIG["rules"]
        required_rules = ["smurfing", "fast_in_fast_out", "round_trip", "large_amount"]
        for rule in required_rules:
            assert rule in rules, f"Missing rule: {rule}"

    def test_smurfing_config(self):
        cfg = AML_CONFIG["rules"]["smurfing"]
        assert cfg["hour_window"] == 1
        assert cfg["min_count"] == 5
        assert cfg["amount_low"] > 0
        assert cfg["amount_high"] > cfg["amount_low"]

    def test_large_amount_config(self):
        cfg = AML_CONFIG["rules"]["large_amount"]
        assert cfg["threshold"] > 0
        assert 0 <= cfg["risk_score"] <= 100

    def test_fast_in_fast_out_config(self):
        cfg = AML_CONFIG["rules"]["fast_in_fast_out"]
        assert cfg["max_minutes"] > 0
        assert 0 < cfg["min_ratio"] <= 1.0

    def test_round_trip_config(self):
        cfg = AML_CONFIG["rules"]["round_trip"]
        assert cfg["max_days"] > 0
        assert 0 < cfg["max_amount_diff_ratio"] < 1.0

    def test_shell_company_config(self):
        cfg = AML_CONFIG["rules"]["shell_company"]
        assert "enabled" in cfg
        assert "required_dimensions" in cfg

    def test_empty_rules_not_allowed(self):
        """确保关键配置都有值，不是空字典"""
        for rule_name in ["smurfing", "fast_in_fast_out", "round_trip", "large_amount"]:
            assert len(AML_CONFIG["rules"][rule_name]) > 0, f"{rule_name} config is empty"

    def test_risk_scores_in_range(self):
        """所有规则的风险评分在 [0, 100] 范围内"""
        score_keys = ["risk_score", "risk_score_primary", "risk_score_secondary", "high_risk_score"]
        for rule_name, rule_cfg in AML_CONFIG["rules"].items():
            if isinstance(rule_cfg, dict):
                for key in score_keys:
                    if key in rule_cfg:
                        val = rule_cfg[key]
                        if isinstance(val, (int, float)):
                            assert 0 <= val <= 100, f"{rule_name}.{key}={val} out of range"
