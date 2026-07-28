"""
规则自适应学习器测试 (B2-1)

覆盖:
- 指标计算（FP/FN 率）
- 建议生成（收紧/放宽/冲突告警/无需调整）
- 各规则收紧/放宽策略正确性
- learn_from_feedback 主流程
- compare_effect 验证（通过/高风险下降/规则失效/异常）
- 持久化（保存/查询/列表）
- 应用/拒绝建议
- 过期检查
- 统计
- 戒律遵守（M1/M2/M4/P1/P2/P3/P4）

戒律:
- M1: 学习基于真实反馈数据
- M2: 每条建议附理由
- M4: 完整记录（算法版本/时间戳）
- P1: FN 高时放宽
- P2: FP 高时收紧；冲突仅告警
- P3: 验证失败拒绝
- P4: 单条失败不影响其他
"""
import os
import json
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tools.rule_auto_learner import (
    RuleAutoLearner,
    RULE_NAME_MAPPING,
    RULE_NAME_REVERSE,
)


# ============================================================
# 夹具
# ============================================================
@pytest.fixture()
def suggestions_dir(tmp_path):
    d = tmp_path / "rule_suggestions"
    d.mkdir()
    return str(d)


@pytest.fixture()
def mock_feedback_manager():
    """Mock FeedbackManager，返回可控的 rule_stats"""
    fm = MagicMock()
    fm.get_rule_stats.return_value = {}
    return fm


@pytest.fixture()
def mock_rule_tuner():
    """Mock RuleTuner，返回可控的当前参数"""
    rt = MagicMock()
    # 默认参数（与 RuleTuner.TUNABLE_SCHEMA 默认值一致）
    rt.get_tunable_params.return_value = {
        "smurfing": {
            "hour_window": 1, "min_count": 5,
            "amount_low": 40000, "amount_high": 50000, "risk_score": 70,
        },
        "fast_in_fast_out": {
            "max_minutes": 10, "min_ratio": 0.95,
            "min_amount": 10000,
            "risk_score_primary": 60, "risk_score_secondary": 50,
        },
        "round_trip": {
            "max_days": 7, "max_amount_diff_ratio": 0.2,
            "min_amount": 10000, "risk_score": 65,
        },
        "large_amount": {
            "threshold": 100000, "risk_score": 40,
        },
        "baseline_deviation": {
            "min_txns_for_baseline": 5, "amount_zscore_threshold": 3.0,
            "max_risk_score": 60,
        },
    }
    rt.validate_params.return_value = (True, [], [])
    rt.apply_config.return_value = None
    rt.compare_effect.return_value = {
        "before": {"rule_counts": {"分拆转账": 5}, "total_hits": 5, "high_risk_hits": 3},
        "after": {"rule_counts": {"分拆转账": 4}, "total_hits": 4, "high_risk_hits": 3},
        "diff": {"total_hits_delta": -1, "high_risk_hits_delta": 0},
        "warnings": [],
    }
    return rt


@pytest.fixture()
def learner(suggestions_dir, mock_feedback_manager, mock_rule_tuner):
    """带 Mock 依赖的学习器"""
    return RuleAutoLearner(
        feedback_manager=mock_feedback_manager,
        rule_tuner=mock_rule_tuner,
        storage_dir=suggestions_dir,
    )


def _make_stats(fp=0, fn=0, confirmed=0):
    """构造反馈统计"""
    return {
        "false_positive": fp,
        "false_negative": fn,
        "confirmed": confirmed,
    }


# ============================================================
# 指标计算测试
# ============================================================
@pytest.mark.unit
def test_compute_metrics_fp_rate(learner):
    """FP 率 = FP / (FP + confirmed)"""
    metrics = learner._compute_rule_metrics("分拆转账", _make_stats(fp=3, confirmed=7))
    assert metrics["fp_rate"] == 0.3
    assert metrics["fn_rate"] == 0.0
    assert metrics["total"] == 10


@pytest.mark.unit
def test_compute_metrics_fn_rate(learner):
    """FN 率 = FN / (FN + confirmed)"""
    metrics = learner._compute_rule_metrics("分拆转账", _make_stats(fn=2, confirmed=8))
    assert metrics["fn_rate"] == 0.2
    assert metrics["fp_rate"] == 0.0


@pytest.mark.unit
def test_compute_metrics_all_zero(learner):
    """全 0 反馈时比率为 0"""
    metrics = learner._compute_rule_metrics("分拆转账", _make_stats())
    assert metrics["fp_rate"] == 0.0
    assert metrics["fn_rate"] == 0.0
    assert metrics["total"] == 0


@pytest.mark.unit
def test_compute_metrics_only_confirmed(learner):
    """仅 confirmed 时 FP/FN 率都为 0"""
    metrics = learner._compute_rule_metrics("分拆转账", _make_stats(confirmed=10))
    assert metrics["fp_rate"] == 0.0
    assert metrics["fn_rate"] == 0.0


# ============================================================
# 建议生成测试
# ============================================================
@pytest.mark.unit
def test_generate_suggestion_fp_high_tighten(learner):
    """FP 率高 → tighten"""
    metrics = learner._compute_rule_metrics("分拆转账", _make_stats(fp=5, confirmed=5))
    suggestion = learner._generate_suggestion("分拆转账", "smurfing", metrics)
    assert suggestion is not None
    assert suggestion["action"] == "tighten"
    assert "FP率" in suggestion["reason"]
    assert "smurfing" in suggestion["params"]


@pytest.mark.unit
def test_generate_suggestion_fn_high_loosen(learner):
    """FN 率高 → loosen"""
    metrics = learner._compute_rule_metrics("分拆转账", _make_stats(fn=5, confirmed=5))
    suggestion = learner._generate_suggestion("分拆转账", "smurfing", metrics)
    assert suggestion is not None
    assert suggestion["action"] == "loosen"
    assert "FN率" in suggestion["reason"]


@pytest.mark.unit
def test_generate_suggestion_conflict_warning(learner):
    """FP+FN 都高 → conflict_warning"""
    metrics = learner._compute_rule_metrics("分拆转账", _make_stats(fp=5, fn=5, confirmed=5))
    suggestion = learner._generate_suggestion("分拆转账", "smurfing", metrics)
    assert suggestion is not None
    assert suggestion["action"] == "conflict_warning"
    assert suggestion["params"] == {}
    assert "同时偏高" in suggestion["reason"]


@pytest.mark.unit
def test_generate_suggestion_no_adjustment_needed(learner):
    """FP/FN 都在阈值内 → None"""
    metrics = learner._compute_rule_metrics("分拆转账", _make_stats(fp=1, fn=1, confirmed=20))
    suggestion = learner._generate_suggestion("分拆转账", "smurfing", metrics)
    assert suggestion is None


# ============================================================
# 收紧策略测试（各规则）
# ============================================================
@pytest.mark.unit
def test_tighten_smurfing_increases_min_count(learner):
    """smurfing 收紧 → 提高 min_count"""
    metrics = learner._compute_rule_metrics("分拆转账", _make_stats(fp=5, confirmed=5))
    suggestion = learner._tighten_rule("分拆转账", "smurfing", metrics)
    assert suggestion["action"] == "tighten"
    # 默认 5 * 1.2 = 6
    assert suggestion["params"]["smurfing"]["min_count"] == 6


@pytest.mark.unit
def test_tighten_fast_in_fast_out_increases_ratio(learner):
    """fast_in_fast_out 收紧 → 提高 min_ratio"""
    metrics = learner._compute_rule_metrics("快进快出", _make_stats(fp=5, confirmed=5))
    suggestion = learner._tighten_rule("快进快出", "fast_in_fast_out", metrics)
    # 0.95 * 1.2 = 1.14，但不超过 1.0
    assert suggestion["params"]["fast_in_fast_out"]["min_ratio"] == 1.0


@pytest.mark.unit
def test_tighten_round_trip_decreases_diff_ratio(learner):
    """round_trip 收紧 → 降低 max_amount_diff_ratio"""
    metrics = learner._compute_rule_metrics("对敲交易", _make_stats(fp=5, confirmed=5))
    suggestion = learner._tighten_rule("对敲交易", "round_trip", metrics)
    # 0.2 * 0.8 = 0.16
    assert suggestion["params"]["round_trip"]["max_amount_diff_ratio"] == 0.16


@pytest.mark.unit
def test_tighten_large_amount_increases_threshold(learner):
    """large_amount 收紧 → 提高 threshold"""
    metrics = learner._compute_rule_metrics("大额交易", _make_stats(fp=5, confirmed=5))
    suggestion = learner._tighten_rule("大额交易", "large_amount", metrics)
    # 100000 * 1.2 = 120000
    assert suggestion["params"]["large_amount"]["threshold"] == 120000.0


@pytest.mark.unit
def test_tighten_baseline_increases_zscore(learner):
    """baseline_deviation 收紧 → 提高 amount_zscore_threshold"""
    metrics = learner._compute_rule_metrics("基线偏离", _make_stats(fp=5, confirmed=5))
    suggestion = learner._tighten_rule("基线偏离", "baseline_deviation", metrics)
    # 3.0 * 1.2 = 3.6
    assert suggestion["params"]["baseline_deviation"]["amount_zscore_threshold"] == 3.6


# ============================================================
# 放宽策略测试
# ============================================================
@pytest.mark.unit
def test_loosen_smurfing_decreases_min_count(learner):
    """smurfing 放宽 → 降低 min_count"""
    metrics = learner._compute_rule_metrics("分拆转账", _make_stats(fn=5, confirmed=5))
    suggestion = learner._loosen_rule("分拆转账", "smurfing", metrics)
    # 5 * 0.8 = 4
    assert suggestion["params"]["smurfing"]["min_count"] == 4


@pytest.mark.unit
def test_loosen_large_amount_decreases_threshold(learner):
    """large_amount 放宽 → 降低 threshold"""
    metrics = learner._compute_rule_metrics("大额交易", _make_stats(fn=5, confirmed=5))
    suggestion = learner._loosen_rule("大额交易", "large_amount", metrics)
    # 100000 * 0.8 = 80000
    assert suggestion["params"]["large_amount"]["threshold"] == 80000.0


# ============================================================
# learn_from_feedback 主流程测试
# ============================================================
@pytest.mark.unit
def test_learn_no_feedback_returns_empty(learner, mock_feedback_manager):
    """无反馈数据 → 返回空"""
    mock_feedback_manager.get_rule_stats.return_value = {}
    suggestions = learner.learn_from_feedback()
    assert suggestions == []


@pytest.mark.unit
def test_learn_disabled_returns_empty(suggestions_dir, mock_feedback_manager, mock_rule_tuner):
    """配置禁用 → 返回空"""
    learner = RuleAutoLearner(
        feedback_manager=mock_feedback_manager,
        rule_tuner=mock_rule_tuner,
        storage_dir=suggestions_dir,
        config={"enabled": False, "min_feedback_count": 10},
    )
    mock_feedback_manager.get_rule_stats.return_value = {"分拆转账": _make_stats(fp=10, confirmed=5)}
    suggestions = learner.learn_from_feedback()
    assert suggestions == []


@pytest.mark.unit
def test_learn_insufficient_feedback_skipped(learner, mock_feedback_manager):
    """反馈数不足 → 跳过"""
    mock_feedback_manager.get_rule_stats.return_value = {
        "分拆转账": _make_stats(fp=2, confirmed=3)  # total=5 < 10
    }
    suggestions = learner.learn_from_feedback()
    assert suggestions == []


@pytest.mark.unit
def test_learn_generates_and_persists(learner, mock_feedback_manager, suggestions_dir):
    """生成建议并持久化"""
    mock_feedback_manager.get_rule_stats.return_value = {
        "分拆转账": _make_stats(fp=8, confirmed=2)  # fp_rate=0.8 > 0.3
    }
    suggestions = learner.learn_from_feedback()
    assert len(suggestions) == 1
    assert suggestions[0]["action"] == "tighten"
    # 验证持久化
    assert os.path.exists(os.path.join(suggestions_dir, "index.json"))
    sid = suggestions[0]["suggestion_id"]
    assert os.path.exists(os.path.join(suggestions_dir, f"{sid}.json"))


@pytest.mark.unit
def test_learn_skips_unmapped_rule(learner, mock_feedback_manager):
    """不在映射表的规则（如虚拟货币）跳过"""
    mock_feedback_manager.get_rule_stats.return_value = {
        "虚拟货币OTC": _make_stats(fp=10, confirmed=5)
    }
    suggestions = learner.learn_from_feedback()
    assert suggestions == []


# ============================================================
# 验证测试
# ============================================================
@pytest.mark.unit
def test_validate_suggestion_passes(learner, mock_rule_tuner):
    """验证通过（高风险命中数未下降）"""
    suggestion = {
        "params": {"smurfing": {"min_count": 6}},
    }
    mock_rule_tuner.compare_effect.return_value = {
        "before": {"rule_counts": {"分拆转账": 5}, "total_hits": 5, "high_risk_hits": 3},
        "after": {"rule_counts": {"分拆转账": 4}, "total_hits": 4, "high_risk_hits": 3},
        "diff": {"total_hits_delta": -1, "high_risk_hits_delta": 0},
        "warnings": [],
    }
    is_valid, result = learner._validate_suggestion(suggestion, [MagicMock()])
    assert is_valid is True
    assert "验证通过" in result["reason"]


@pytest.mark.unit
def test_validate_suggestion_rejects_high_risk_drop(learner, mock_rule_tuner):
    """高风险命中数下降过多 → 拒绝（戒律 P1）"""
    suggestion = {
        "params": {"smurfing": {"min_count": 6}},
    }
    mock_rule_tuner.compare_effect.return_value = {
        "before": {"rule_counts": {"分拆转账": 5}, "total_hits": 5, "high_risk_hits": 3},
        "after": {"rule_counts": {"分拆转账": 2}, "total_hits": 2, "high_risk_hits": 1},
        "diff": {"total_hits_delta": -3, "high_risk_hits_delta": -2},
        "warnings": [],
    }
    is_valid, result = learner._validate_suggestion(suggestion, [MagicMock()])
    assert is_valid is False
    assert "高风险" in result["reason"]


@pytest.mark.unit
def test_validate_suggestion_rejects_rule_invalidation(learner, mock_rule_tuner):
    """规则完全失效 → 拒绝"""
    suggestion = {
        "params": {"smurfing": {"min_count": 20}},
    }
    mock_rule_tuner.compare_effect.return_value = {
        "before": {"rule_counts": {"分拆转账": 5}, "total_hits": 5, "high_risk_hits": 3},
        "after": {"rule_counts": {"分拆转账": 0}, "total_hits": 0, "high_risk_hits": 0},
        "diff": {"total_hits_delta": -5, "high_risk_hits_delta": -3},
        "warnings": [],
    }
    is_valid, result = learner._validate_suggestion(suggestion, [MagicMock()])
    assert is_valid is False
    assert "不再命中" in result["reason"]


@pytest.mark.unit
def test_validate_suggestion_handles_exception(learner, mock_rule_tuner):
    """compare_effect 异常 → 拒绝（戒律 P4）"""
    suggestion = {"params": {"smurfing": {"min_count": 6}}}
    mock_rule_tuner.compare_effect.side_effect = RuntimeError("test error")
    is_valid, result = learner._validate_suggestion(suggestion, [MagicMock()])
    assert is_valid is False
    assert "compare_effect 执行失败" in result["reason"]


# ============================================================
# 持久化与查询测试
# ============================================================
@pytest.mark.unit
def test_save_and_list_pending(learner):
    """保存建议并查询待审核列表"""
    suggestions = [
        {
            "suggestion_id": "SG-TEST0001",
            "rule_name": "分拆转账",
            "rule_key": "smurfing",
            "action": "tighten",
            "reason": "测试",
            "params": {"smurfing": {"min_count": 6}},
            "metrics": {},
            "validated": True,
            "validation_result": None,
            "status": "pending",
            "created_at": "2026-07-27 12:00:00",
            "_created_at_ts": time.time(),
            "algorithm_version": "1.0.0",
            "ttl_days": 30,
        }
    ]
    learner.save_suggestions(suggestions)
    pending = learner.list_pending_suggestions()
    assert len(pending) == 1
    assert pending[0]["suggestion_id"] == "SG-TEST0001"


@pytest.mark.unit
def test_get_suggestion(learner):
    """获取单条建议"""
    suggestions = [
        {
            "suggestion_id": "SG-TEST0002",
            "rule_name": "大额交易",
            "rule_key": "large_amount",
            "action": "tighten",
            "reason": "测试",
            "params": {},
            "metrics": {},
            "validated": False,
            "validation_result": None,
            "status": "pending",
            "created_at": "2026-07-27 12:00:00",
            "_created_at_ts": time.time(),
            "algorithm_version": "1.0.0",
            "ttl_days": 30,
        }
    ]
    learner.save_suggestions(suggestions)
    suggestion = learner.get_suggestion("SG-TEST0002")
    assert suggestion is not None
    assert suggestion["rule_name"] == "大额交易"


@pytest.mark.unit
def test_get_suggestion_not_found(learner):
    """查询不存在的建议 → None"""
    assert learner.get_suggestion("SG-NOTEXIST") is None


# ============================================================
# 应用 / 拒绝建议测试
# ============================================================
@pytest.mark.unit
def test_apply_suggestion_success(learner, mock_rule_tuner):
    """成功应用建议"""
    suggestions = [
        {
            "suggestion_id": "SG-APPLY001",
            "rule_name": "分拆转账",
            "rule_key": "smurfing",
            "action": "tighten",
            "reason": "测试",
            "params": {"smurfing": {"min_count": 6}},
            "metrics": {},
            "validated": True,
            "validation_result": None,
            "status": "pending",
            "created_at": "2026-07-27 12:00:00",
            "_created_at_ts": time.time(),
            "algorithm_version": "1.0.0",
            "ttl_days": 30,
        }
    ]
    learner.save_suggestions(suggestions)
    ok, msg = learner.apply_suggestion("SG-APPLY001")
    assert ok is True
    mock_rule_tuner.apply_config.assert_called_once_with({"smurfing": {"min_count": 6}})
    # 状态应更新为 applied
    suggestion = learner.get_suggestion("SG-APPLY001")
    assert suggestion["status"] == "applied"


@pytest.mark.unit
def test_apply_suggestion_not_pending(learner):
    """应用非 pending 状态的建议 → 失败"""
    suggestions = [
        {
            "suggestion_id": "SG-APPLY002",
            "rule_name": "分拆转账",
            "rule_key": "smurfing",
            "action": "tighten",
            "reason": "测试",
            "params": {},
            "metrics": {},
            "validated": False,
            "validation_result": None,
            "status": "applied",
            "created_at": "2026-07-27 12:00:00",
            "_created_at_ts": time.time(),
            "algorithm_version": "1.0.0",
            "ttl_days": 30,
        }
    ]
    learner.save_suggestions(suggestions)
    ok, msg = learner.apply_suggestion("SG-APPLY002")
    assert ok is False
    assert "applied" in msg


@pytest.mark.unit
def test_apply_suggestion_not_found(learner):
    """应用不存在的建议 → 失败"""
    ok, msg = learner.apply_suggestion("SG-NOTEXIST")
    assert ok is False
    assert "不存在" in msg


@pytest.mark.unit
def test_apply_suggestion_expired(learner):
    """应用过期建议 → 失败"""
    suggestions = [
        {
            "suggestion_id": "SG-EXPIRE01",
            "rule_name": "分拆转账",
            "rule_key": "smurfing",
            "action": "tighten",
            "reason": "测试",
            "params": {"smurfing": {"min_count": 6}},
            "metrics": {},
            "validated": True,
            "validation_result": None,
            "status": "pending",
            "created_at": "2026-06-01 12:00:00",
            "_created_at_ts": time.time() - 31 * 86400,  # 31天前
            "algorithm_version": "1.0.0",
            "ttl_days": 30,
        }
    ]
    learner.save_suggestions(suggestions)
    ok, msg = learner.apply_suggestion("SG-EXPIRE01")
    assert ok is False
    assert "过期" in msg


@pytest.mark.unit
def test_reject_suggestion_success(learner):
    """成功拒绝建议"""
    suggestions = [
        {
            "suggestion_id": "SG-REJECT01",
            "rule_name": "分拆转账",
            "rule_key": "smurfing",
            "action": "tighten",
            "reason": "测试",
            "params": {},
            "metrics": {},
            "validated": False,
            "validation_result": None,
            "status": "pending",
            "created_at": "2026-07-27 12:00:00",
            "_created_at_ts": time.time(),
            "algorithm_version": "1.0.0",
            "ttl_days": 30,
        }
    ]
    learner.save_suggestions(suggestions)
    result = learner.reject_suggestion("SG-REJECT01", reason="人工判断不需要")
    assert result is True
    suggestion = learner.get_suggestion("SG-REJECT01")
    assert suggestion["status"] == "rejected"
    assert suggestion["rejection_reason"] == "人工判断不需要"


@pytest.mark.unit
def test_reject_suggestion_not_pending(learner):
    """拒绝非 pending 建议 → False"""
    suggestions = [
        {
            "suggestion_id": "SG-REJECT02",
            "rule_name": "分拆转账",
            "rule_key": "smurfing",
            "action": "tighten",
            "reason": "测试",
            "params": {},
            "metrics": {},
            "validated": False,
            "validation_result": None,
            "status": "applied",
            "created_at": "2026-07-27 12:00:00",
            "_created_at_ts": time.time(),
            "algorithm_version": "1.0.0",
            "ttl_days": 30,
        }
    ]
    learner.save_suggestions(suggestions)
    assert learner.reject_suggestion("SG-REJECT02") is False


# ============================================================
# 过期过滤测试
# ============================================================
@pytest.mark.unit
def test_list_pending_filters_expired(learner):
    """list_pending_suggestions 过滤过期建议"""
    suggestions = [
        {
            "suggestion_id": "SG-OLD00001",
            "rule_name": "分拆转账",
            "rule_key": "smurfing",
            "action": "tighten",
            "reason": "旧建议",
            "params": {},
            "metrics": {},
            "validated": False,
            "validation_result": None,
            "status": "pending",
            "created_at": "2026-06-01 12:00:00",
            "_created_at_ts": time.time() - 31 * 86400,  # 31天前
            "algorithm_version": "1.0.0",
            "ttl_days": 30,
        },
        {
            "suggestion_id": "SG-NEW00001",
            "rule_name": "分拆转账",
            "rule_key": "smurfing",
            "action": "tighten",
            "reason": "新建议",
            "params": {},
            "metrics": {},
            "validated": False,
            "validation_result": None,
            "status": "pending",
            "created_at": "2026-07-27 12:00:00",
            "_created_at_ts": time.time(),  # 刚才
            "algorithm_version": "1.0.0",
            "ttl_days": 30,
        },
    ]
    learner.save_suggestions(suggestions)
    pending = learner.list_pending_suggestions()
    assert len(pending) == 1
    assert pending[0]["suggestion_id"] == "SG-NEW00001"


# ============================================================
# 统计测试
# ============================================================
@pytest.mark.unit
def test_get_stats_empty(learner):
    """无建议时统计全 0"""
    stats = learner.get_stats()
    assert stats["total"] == 0
    assert stats["pending"] == 0
    assert stats["applied"] == 0


@pytest.mark.unit
def test_get_stats_with_suggestions(learner):
    """有建议时统计正确"""
    suggestions = [
        {
            "suggestion_id": "SG-STAT0001",
            "rule_name": "分拆转账",
            "rule_key": "smurfing",
            "action": "tighten",
            "reason": "测试",
            "params": {},
            "metrics": {},
            "validated": False,
            "validation_result": None,
            "status": "pending",
            "created_at": "2026-07-27 12:00:00",
            "_created_at_ts": time.time(),
            "algorithm_version": "1.0.0",
            "ttl_days": 30,
        },
        {
            "suggestion_id": "SG-STAT0002",
            "rule_name": "大额交易",
            "rule_key": "large_amount",
            "action": "loosen",
            "reason": "测试",
            "params": {},
            "metrics": {},
            "validated": False,
            "validation_result": None,
            "status": "applied",
            "created_at": "2026-07-27 12:00:00",
            "_created_at_ts": time.time(),
            "algorithm_version": "1.0.0",
            "ttl_days": 30,
        },
    ]
    learner.save_suggestions(suggestions)
    stats = learner.get_stats()
    assert stats["total"] == 2
    assert stats["pending"] == 1
    assert stats["applied"] == 1
    assert stats["by_action"]["tighten"] == 1
    assert stats["by_action"]["loosen"] == 1
    assert stats["by_rule"]["分拆转账"] == 1
    assert stats["by_rule"]["大额交易"] == 1


# ============================================================
# 规则名映射测试
# ============================================================
@pytest.mark.unit
def test_rule_name_mapping_completeness():
    """规则名映射覆盖 5 条核心规则"""
    assert len(RULE_NAME_MAPPING) == 5
    assert RULE_NAME_MAPPING["分拆转账"] == "smurfing"
    assert RULE_NAME_MAPPING["快进快出"] == "fast_in_fast_out"
    assert RULE_NAME_MAPPING["对敲交易"] == "round_trip"
    assert RULE_NAME_MAPPING["大额交易"] == "large_amount"
    assert RULE_NAME_MAPPING["基线偏离"] == "baseline_deviation"
    # 反向映射
    assert RULE_NAME_REVERSE["smurfing"] == "分拆转账"


# ============================================================
# 建议字段完整性测试（戒律 M2/M4）
# ============================================================
@pytest.mark.unit
def test_suggestion_has_required_fields(learner):
    """建议包含必填字段（戒律 M2: reason, M4: algorithm_version/created_at）"""
    metrics = learner._compute_rule_metrics("分拆转账", _make_stats(fp=5, confirmed=5))
    suggestion = learner._generate_suggestion("分拆转账", "smurfing", metrics)
    assert "reason" in suggestion  # M2: 必须有理由
    assert "algorithm_version" in suggestion  # M4: 算法版本
    assert "created_at" in suggestion  # M4: 时间戳
    assert "suggestion_id" in suggestion
    assert "rule_name" in suggestion
    assert "action" in suggestion
    assert suggestion["status"] == "pending"


# ============================================================
# 调整幅度限制测试（戒律: 保守策略）
# ============================================================
@pytest.mark.unit
def test_adjust_ratio_capped(learner, mock_rule_tuner):
    """调整幅度不超过 max_adjust_ratio（20%）"""
    # 设置当前 min_count 为 10，收紧后应为 12（10 * 1.2 = 12）
    mock_rule_tuner.get_tunable_params.return_value = {
        "smurfing": {
            "hour_window": 1, "min_count": 10,
            "amount_low": 40000, "amount_high": 50000, "risk_score": 70,
        },
        "fast_in_fast_out": {
            "max_minutes": 10, "min_ratio": 0.95,
            "min_amount": 10000,
            "risk_score_primary": 60, "risk_score_secondary": 50,
        },
        "round_trip": {
            "max_days": 7, "max_amount_diff_ratio": 0.2,
            "min_amount": 10000, "risk_score": 65,
        },
        "large_amount": {
            "threshold": 100000, "risk_score": 40,
        },
        "baseline_deviation": {
            "min_txns_for_baseline": 5, "amount_zscore_threshold": 3.0,
            "max_risk_score": 60,
        },
    }
    metrics = learner._compute_rule_metrics("分拆转账", _make_stats(fp=8, confirmed=2))
    suggestion = learner._tighten_rule("分拆转账", "smurfing", metrics)
    # 10 * 1.2 = 12（恰好 20% 上限）
    assert suggestion["params"]["smurfing"]["min_count"] == 12


# ============================================================
# 端到端学习流程测试
# ============================================================
@pytest.mark.unit
def test_end_to_end_learn_and_apply(learner, mock_feedback_manager, mock_rule_tuner):
    """端到端：学习 → 查询 → 应用"""
    # 构造反馈：10 FP + 5 confirmed → fp_rate=0.67 > 0.3
    mock_feedback_manager.get_rule_stats.return_value = {
        "分拆转账": _make_stats(fp=10, confirmed=5)
    }
    # compare_effect 返回安全结果
    mock_rule_tuner.compare_effect.return_value = {
        "before": {"rule_counts": {"分拆转账": 5}, "total_hits": 5, "high_risk_hits": 3},
        "after": {"rule_counts": {"分拆转账": 4}, "total_hits": 4, "high_risk_hits": 3},
        "diff": {"total_hits_delta": -1, "high_risk_hits_delta": 0},
        "warnings": [],
    }

    # 1. 学习（带验证）
    suggestions = learner.learn_from_feedback(transactions=[MagicMock()])
    assert len(suggestions) == 1
    assert suggestions[0]["validated"] is True
    assert suggestions[0]["status"] == "pending"

    # 2. 查询待审核
    pending = learner.list_pending_suggestions()
    assert len(pending) == 1

    # 3. 应用
    sid = suggestions[0]["suggestion_id"]
    ok, msg = learner.apply_suggestion(sid)
    assert ok is True
    mock_rule_tuner.apply_config.assert_called_once()

    # 4. 验证状态
    suggestion = learner.get_suggestion(sid)
    assert suggestion["status"] == "applied"
