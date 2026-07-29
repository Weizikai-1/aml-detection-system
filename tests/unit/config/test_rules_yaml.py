"""
config/rules_yaml.py 单元测试

覆盖: RuleYAMLManager / AdaptiveRuleTuner / RuleABTest / create_rule_management_system
"""
import os
import time
import yaml
import pytest

from config.rules_yaml import (
    RuleYAMLManager,
    AdaptiveRuleTuner,
    RuleABTest,
    create_rule_management_system,
)


# ---- 简单测试用默认规则 ----
SIMPLE_DEFAULTS = {
    "smurfing": {
        "enabled": True,
        "hour_window": 1,
        "min_count": 5,
        "risk_score": 85,
    },
    "fast_in_fast_out": {
        "enabled": True,
        "min_minutes": 10,
        "risk_score": 80,
    },
}


# ============================================================
# RuleYAMLManager
# ============================================================


class TestRuleYAMLManager:

    def test_load_creates_yaml_when_missing(self, tmp_path):
        """首次无YAML文件时自动创建默认配置"""
        path = str(tmp_path / "rules.yaml")
        mgr = RuleYAMLManager(config_path=path, defaults=SIMPLE_DEFAULTS)
        rules = mgr.load()

        assert os.path.exists(path)
        assert "smurfing" in rules
        assert rules["smurfing"]["risk_score"] == 85

    def test_load_from_existing_yaml(self, tmp_path):
        """从已有YAML文件加载"""
        path = str(tmp_path / "rules.yaml")
        data = {"my_rule": {"enabled": False, "threshold": 999}}
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        mgr = RuleYAMLManager(config_path=path)
        rules = mgr.load()

        assert rules["my_rule"]["enabled"] is False
        assert rules["my_rule"]["threshold"] == 999

    def test_load_fallback_on_corrupt_yaml(self, tmp_path):
        """YAML损坏时回退到默认值"""
        path = str(tmp_path / "rules.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(":::invalid::yaml:::\n  [broken")

        mgr = RuleYAMLManager(config_path=path, defaults=SIMPLE_DEFAULTS)
        rules = mgr.load()

        # 回退到默认值
        assert "smurfing" in rules

    def test_save_creates_version(self, tmp_path):
        """保存时自动创建版本快照"""
        path = str(tmp_path / "rules.yaml")
        mgr = RuleYAMLManager(config_path=path, defaults=SIMPLE_DEFAULTS)
        mgr.load()  # load 时文件不存在会调 save()，已创建1个版本

        # 显式再 save 一次，创建第2个版本
        mgr.save(create_version=True)

        versions = mgr.get_version_history()
        assert len(versions) == 2
        assert all("rules" in v and "timestamp" in v for v in versions)

    def test_update_rule(self, tmp_path):
        """更新单个规则"""
        path = str(tmp_path / "rules.yaml")
        mgr = RuleYAMLManager(config_path=path, defaults=SIMPLE_DEFAULTS)
        mgr.load()

        result = mgr.update_rule("smurfing", {"risk_score": 90})

        assert result["risk_score"] == 90
        # 磁盘上也已更新
        with open(path, "r", encoding="utf-8") as f:
            saved = yaml.safe_load(f)
        assert saved["smurfing"]["risk_score"] == 90

    def test_get_rule(self, tmp_path):
        """获取指定规则"""
        path = str(tmp_path / "rules.yaml")
        mgr = RuleYAMLManager(config_path=path, defaults=SIMPLE_DEFAULTS)
        mgr.load()

        rule = mgr.get("smurfing")
        assert rule["risk_score"] == 85

        # 不存在的规则返回默认值
        assert mgr.get("nonexistent", "fallback") == "fallback"

    def test_rollback_to_version(self, tmp_path):
        """回滚到历史版本"""
        path = str(tmp_path / "rules.yaml")
        mgr = RuleYAMLManager(config_path=path, defaults=SIMPLE_DEFAULTS)
        mgr.load()

        # 原始值 risk_score=85
        mgr.update_rule("smurfing", {"risk_score": 90})
        # 现在值为 90，版本历史有2条

        rolled = mgr.rollback_to_version(0)
        assert rolled["smurfing"]["risk_score"] == 85

    def test_defaults_override_builtin(self, tmp_path):
        """传入defaults参数时优先使用外部默认值"""
        path = str(tmp_path / "rules.yaml")
        mgr = RuleYAMLManager(config_path=path, defaults=SIMPLE_DEFAULTS)
        rules = mgr.load()

        # 使用了外部传入的默认值
        assert "smurfing" in rules
        assert rules["smurfing"]["risk_score"] == 85
        # 不应包含内置默认中的 gnn_model 等
        assert "gnn_model" not in rules

    def test_reload_if_needed_cooldown(self, tmp_path):
        """冷却时间内不重复加载"""
        path = str(tmp_path / "rules.yaml")
        # 预先写入文件，使 load() 走"文件已存在"分支，正确设置 _last_reload
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(SIMPLE_DEFAULTS, f)

        mgr = RuleYAMLManager(config_path=path, defaults=SIMPLE_DEFAULTS)
        mgr.load()

        # 冷却时间内，应返回 False
        assert mgr.reload_if_needed(cooldown=60.0) is False

    def test_version_history_max_10(self, tmp_path):
        """版本历史最多保留10个"""
        path = str(tmp_path / "rules.yaml")
        mgr = RuleYAMLManager(config_path=path, defaults=SIMPLE_DEFAULTS)
        mgr.load()

        for i in range(15):
            mgr.update_rule("smurfing", {"risk_score": 50 + i})

        versions = mgr.get_version_history()
        assert len(versions) <= 10


# ============================================================
# AdaptiveRuleTuner
# ============================================================


class TestAdaptiveRuleTuner:

    @pytest.fixture
    def tuner(self, tmp_path):
        path = str(tmp_path / "rules.yaml")
        mgr = RuleYAMLManager(config_path=path, defaults=SIMPLE_DEFAULTS)
        mgr.load()
        return AdaptiveRuleTuner(mgr)

    def test_record_feedback_tp(self, tuner):
        """记录真阳性反馈"""
        tuner.record_feedback("smurfing", "TXN-001", is_correct=True, was_flagged=True, actual_fraud=True)

        stats = tuner.get_rule_stats("smurfing")
        assert stats["true_positives"] == 1
        assert stats["total_hits"] == 1

    def test_record_feedback_fp(self, tuner):
        """记录假阳性反馈"""
        tuner.record_feedback("smurfing", "TXN-002", is_correct=False, was_flagged=True, actual_fraud=False)

        stats = tuner.get_rule_stats("smurfing")
        assert stats["false_positives"] == 1
        assert stats["total_hits"] == 1

    def test_get_rule_stats(self, tuner):
        """获取规则统计"""
        tuner.record_feedback("smurfing", "TXN-001", is_correct=True, was_flagged=True, actual_fraud=True)
        tuner.record_feedback("smurfing", "TXN-002", is_correct=False, was_flagged=True, actual_fraud=False)

        stats = tuner.get_rule_stats("smurfing")
        assert stats["total_hits"] == 2
        assert stats["precision"] == 0.5
        assert stats["recall"] == 1.0

    def test_suggest_optimizations_high_fp(self, tuner):
        """误报率过高时建议优化"""
        # 构造高误报: 35条误报 + 15条真阳性 => fp_rate = 35/50 = 0.7 > 0.3
        for i in range(35):
            tuner.record_feedback("smurfing", f"TXN-FP-{i}", is_correct=False, was_flagged=True, actual_fraud=False)
        for i in range(15):
            tuner.record_feedback("smurfing", f"TXN-TP-{i}", is_correct=True, was_flagged=True, actual_fraud=True)

        suggestions = tuner.suggest_optimizations(min_feedback=50)
        assert any(s["issue"] == "high_false_positive_rate" for s in suggestions)

    def test_suggest_optimizations_min_feedback(self, tuner):
        """反馈不足时不建议"""
        # 只录入5条反馈，不满足 min_feedback=50
        for i in range(5):
            tuner.record_feedback("smurfing", f"TXN-{i}", is_correct=True, was_flagged=True, actual_fraud=True)

        suggestions = tuner.suggest_optimizations(min_feedback=50)
        assert len(suggestions) == 0

    def test_apply_optimization_high_fp(self, tuner):
        """应用误报率优化"""
        tuner.record_feedback("smurfing", "TXN-001", is_correct=True, was_flagged=True, actual_fraud=True)

        result = tuner.apply_optimization("smurfing", {"issue": "high_false_positive_rate"})
        assert result["success"] is True
        assert result["updates"]["risk_score"] == 90  # 85 + 5

    def test_apply_optimization_low_recall(self, tuner, tmp_path):
        """应用低召回优化 — 规则有 threshold 字段时降低阈值"""
        # 使用独立路径，避免与 fixture 的文件冲突
        path = str(tmp_path / "low_recall_rules.yaml")
        mgr = RuleYAMLManager(config_path=path, defaults={
            "large_amount": {"enabled": True, "threshold": 100000, "risk_score": 60},
        })
        mgr.load()
        t = AdaptiveRuleTuner(mgr)

        result = t.apply_optimization("large_amount", {"issue": "low_recall"})
        assert result["success"] is True
        assert result["updates"]["threshold"] == pytest.approx(100000 * 0.9)


# ============================================================
# RuleABTest
# ============================================================


class TestRuleABTest:

    @pytest.fixture
    def ab_tester(self, tmp_path):
        path = str(tmp_path / "rules.yaml")
        mgr = RuleYAMLManager(config_path=path, defaults=SIMPLE_DEFAULTS)
        mgr.load()
        return RuleABTest(mgr)

    def test_create_experiment(self, ab_tester):
        """创建实验"""
        exp = ab_tester.create_experiment(
            "exp_001", "smurfing",
            new_params={"hour_window": 2},
            traffic_split=0.3,
        )
        assert exp["rule_name"] == "smurfing"
        assert exp["traffic_split"] == 0.3
        assert exp["status"] == "running"

    def test_assign_group_deterministic(self, ab_tester):
        """确定性分组：同一 transaction_id 始终返回同一组"""
        ab_tester.create_experiment("exp_001", "smurfing", {"hour_window": 2}, traffic_split=0.3)

        g1 = ab_tester.assign_group("exp_001", "TXN-DETERMINISTIC")
        g2 = ab_tester.assign_group("exp_001", "TXN-DETERMINISTIC")
        assert g1 == g2

    def test_assign_group_unknown_experiment(self, ab_tester):
        """未知实验返回control"""
        group = ab_tester.assign_group("nonexistent_exp", "TXN-001")
        assert group == "control"

    def test_record_result(self, ab_tester):
        """记录实验结果"""
        ab_tester.create_experiment("exp_001", "smurfing", {"hour_window": 2})
        ab_tester.record_result("exp_001", "control", was_flagged=True, is_fraud=True)
        ab_tester.record_result("exp_001", "treatment", was_flagged=True, is_fraud=False)

        exp = ab_tester._experiments["exp_001"]
        assert exp["control_tp"] == 1
        assert exp["treatment_fp"] == 1

    def test_get_results(self, ab_tester):
        """获取实验结果和指标"""
        ab_tester.create_experiment("exp_001", "smurfing", {"hour_window": 2})
        ab_tester.record_result("exp_001", "control", was_flagged=True, is_fraud=True)
        ab_tester.record_result("exp_001", "treatment", was_flagged=True, is_fraud=True)

        results = ab_tester.get_results("exp_001")
        assert "control" in results
        assert "treatment" in results
        assert results["control"]["metrics"]["precision"] == 1.0
        assert results["treatment"]["metrics"]["precision"] == 1.0
        assert "improved" in results


# ============================================================
# create_rule_management_system
# ============================================================


class TestCreateRuleManagementSystem:

    def test_create_system_with_defaults(self, tmp_path):
        """传入defaults创建系统"""
        path = str(tmp_path / "rules.yaml")
        mgr, tuner, ab_tester = create_rule_management_system(
            config_path=path, defaults=SIMPLE_DEFAULTS,
        )

        assert isinstance(mgr, RuleYAMLManager)
        assert isinstance(tuner, AdaptiveRuleTuner)
        assert isinstance(ab_tester, RuleABTest)
        assert mgr.get("smurfing") is not None
