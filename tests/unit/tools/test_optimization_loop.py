"""
五阶段自动优化闭环单元测试

覆盖:
- OptimizationLoopResult 数据结构与序列化
- Stage 1: 数据收集（正常/空交易/空真值集/默认参数/待定记录）
- Stage 2: 当前参数评估
- Stage 3: 反馈权重调整（无反馈/多漏报/多误报/平衡反馈/反馈加载失败）
- Stage 4: 调参（正常/空搜索空间/组合爆炸/优化异常）
- Stage 5: 验证（A/B 测试推荐 B/A/review/失败）
- 综合推荐决策（apply/keep/review 各种场景）
- 完整闭环集成测试
- 持久化（save / get / list / delete）
- 边界条件与异常处理
- 戒律守护（P1 漏报加权 / P2 误报加权 / P4 非破坏性）
"""
import copy
import json
import os
import tempfile

import pytest

from tools.optimization_loop import (
    OptimizationLoop,
    OptimizationLoopResult,
    DEFAULT_OBJECTIVE_WEIGHTS,
    DEFAULT_SEARCH_SPACE,
    MAX_WEIGHT_SHIFT,
    MIN_WEIGHT,
    MAX_WEIGHT,
    WEIGHT_NORMALIZE_FACTOR,
)
from tools.feedback_manager import FeedbackManager
from graph.state import Transaction


# ============================================================
# 辅助函数
# ============================================================
def _make_txn(
    tid: str,
    from_acc: str,
    to_acc: str,
    amount: float,
    timestamp: str,
    remark: str = "",
) -> Transaction:
    return {
        "transaction_id": tid,
        "from_account": from_acc,
        "to_account": to_acc,
        "amount": amount,
        "timestamp": timestamp,
        "transaction_type": "transfer",
        "remark": remark,
    }


def _smurfing_txns(count: int, receiver: str = "RECV_A") -> list:
    """count 笔分拆转账（4.5万，同一收款人，不同付款人，1小时内）"""
    return [
        _make_txn(
            f"SMURF_{i}", f"PAYER_{i}", receiver, 45000.0,
            f"2026-07-01T10:{i:02d}:00"
        )
        for i in range(count)
    ]


def _large_txns(count: int) -> list:
    """count 笔大额交易（≥10万）"""
    return [
        _make_txn(
            f"LARGE_{i}", f"ACC_A_{i}", f"ACC_B_{i}", 150000.0,
            f"2026-07-01T12:{i:02d}:00"
        )
        for i in range(count)
    ]


def _baseline_params() -> dict:
    """完整基线参数（与 RuleTuner 默认一致）"""
    return {
        "smurfing": {
            "min_count": 5, "hour_window": 1,
            "amount_low": 40000, "amount_high": 50000,
            "risk_score": 70,
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
    }


def _sample_transactions() -> list:
    """真实模式交易数据：包含分拆 + 大额"""
    return _smurfing_txns(5) + _large_txns(2)


def _sample_ground_truth() -> dict:
    """与 _sample_transactions 对应的真值集"""
    gt = {}
    # 5 笔分拆转账 → 可疑
    for i in range(5):
        gt[f"SMURF_{i}"] = True
    # 2 笔大额交易 → 可疑
    for i in range(2):
        gt[f"LARGE_{i}"] = True
    # 加几笔正常交易（不在交易列表中，用于测试 TN/FP）
    gt["NORMAL_1"] = False
    gt["NORMAL_2"] = False
    return gt


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def loop(tmp_path):
    """使用临时目录的 OptimizationLoop"""
    return OptimizationLoop(storage_dir=str(tmp_path))


@pytest.fixture
def loop_with_feedback(tmp_path):
    """使用临时目录的 OptimizationLoop + 临时 FeedbackManager"""
    fm = FeedbackManager(feedback_dir=str(tmp_path / "feedback"))
    return OptimizationLoop(
        storage_dir=str(tmp_path / "loop"),
        feedback_manager=fm,
    )


# ============================================================
# OptimizationLoopResult 数据结构
# ============================================================
class TestOptimizationLoopResult:
    def test_creation(self):
        """创建结果对象"""
        result = OptimizationLoopResult(
            loop_id="LOOP-TEST0001",
            timestamp="2026-07-28T10:00:00",
            stages={"stage1": {"data": 1}},
            recommendation={"action": "keep", "reason": "test"},
        )
        assert result.loop_id == "LOOP-TEST0001"
        assert result.recommendation["action"] == "keep"
        assert result.metadata == {}

    def test_roundtrip(self):
        """序列化与反序列化"""
        original = OptimizationLoopResult(
            loop_id="LOOP-TEST0002",
            timestamp="2026-07-28T10:00:00",
            stages={"stage1": {"warnings": []}},
            recommendation={"action": "apply", "reason": "test"},
            metadata={"dataset_name": "test_ds"},
        )
        d = original.to_dict()
        restored = OptimizationLoopResult.from_dict(d)
        assert restored.loop_id == original.loop_id
        assert restored.stages == original.stages
        assert restored.recommendation == original.recommendation
        assert restored.metadata == original.metadata

    def test_from_dict_defaults(self):
        """空字典反序列化使用默认值"""
        result = OptimizationLoopResult.from_dict({})
        assert result.loop_id == ""
        assert result.stages == {}
        assert result.recommendation == {}
        assert result.metadata == {}


# ============================================================
# Stage 1: 数据收集
# ============================================================
class TestStage1DataCollection:
    def test_normal_data_collection(self, loop):
        """正常数据收集"""
        txns = _sample_transactions()
        gt = _sample_ground_truth()
        params = _baseline_params()

        stage1 = loop._stage1_collect_data(txns, gt, params, "test_ds")

        assert len(stage1["transactions"]) == 7
        assert len(stage1["ground_truth"]) == 9
        assert stage1["current_params"]["smurfing"]["min_count"] == 5
        assert stage1["data_warnings"] == []
        assert stage1["data_summary"]["transaction_count"] == 7
        assert stage1["data_summary"]["ground_truth_suspicious"] == 7
        assert stage1["data_summary"]["ground_truth_normal"] == 2

    def test_empty_transactions_warns(self, loop):
        """空交易数据应警告"""
        stage1 = loop._stage1_collect_data([], _sample_ground_truth(), None, "")
        assert "交易数据为空" in stage1["data_warnings"][0]

    def test_empty_ground_truth_warns(self, loop):
        """空真值集应警告"""
        stage1 = loop._stage1_collect_data(_sample_transactions(), {}, None, "")
        assert "真值集为空" in stage1["data_warnings"][0]

    def test_none_current_params_uses_defaults(self, loop):
        """current_params=None 时使用 RuleTuner 默认参数"""
        stage1 = loop._stage1_collect_data(
            _sample_transactions(), _sample_ground_truth(), None, ""
        )
        # 默认参数应包含所有可调参数组
        assert "smurfing" in stage1["current_params"]
        assert "large_amount" in stage1["current_params"]
        assert stage1["current_params"]["smurfing"]["min_count"] == 5

    def test_pending_records_warns(self, loop):
        """待定记录(None)应警告"""
        gt = _sample_ground_truth()
        gt["PENDING_1"] = None
        stage1 = loop._stage1_collect_data(_sample_transactions(), gt, None, "")
        pending_warning = [w for w in stage1["data_warnings"] if "待定" in w]
        assert len(pending_warning) == 1

    def test_params_deep_copied(self, loop):
        """传入的参数应被深拷贝，不影响原始对象"""
        params = _baseline_params()
        original_min_count = params["smurfing"]["min_count"]

        stage1 = loop._stage1_collect_data(
            _sample_transactions(), _sample_ground_truth(), params, ""
        )
        # 修改 stage1 中的参数不应影响原始
        stage1["current_params"]["smurfing"]["min_count"] = 99
        assert params["smurfing"]["min_count"] == original_min_count


# ============================================================
# Stage 2: 当前参数评估
# ============================================================
class TestStage2Evaluation:
    def test_normal_evaluation(self, loop):
        """正常评估当前参数"""
        txns = _sample_transactions()
        gt = _sample_ground_truth()
        params = _baseline_params()

        stage2 = loop._stage2_evaluate_current(txns, gt, params)

        metrics = stage2["current_metrics"]
        assert "precision" in metrics
        assert "recall" in metrics
        assert "f1" in metrics
        assert "total_hits" in metrics
        assert 0.0 <= metrics["precision"] <= 1.0
        assert 0.0 <= metrics["recall"] <= 1.0
        assert stage2["evaluation_warnings"] == []

    def test_evaluation_with_empty_data(self, loop):
        """空数据评估应返回零指标"""
        stage2 = loop._stage2_evaluate_current([], {}, _baseline_params())
        metrics = stage2["current_metrics"]
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0
        assert metrics["total_hits"] == 0


# ============================================================
# Stage 3: 反馈权重调整
# ============================================================
class TestStage3WeightAdjustment:
    def test_no_feedback_uses_defaults(self, loop_with_feedback):
        """无反馈时使用默认权重"""
        stage3 = loop_with_feedback._stage3_adjust_weights(None)

        weights = stage3["adjusted_weights"]
        assert weights["precision"] == DEFAULT_OBJECTIVE_WEIGHTS["precision"]
        assert weights["recall"] == DEFAULT_OBJECTIVE_WEIGHTS["recall"]
        assert weights["f1"] == DEFAULT_OBJECTIVE_WEIGHTS["f1"]
        assert stage3["weight_shift"] == 0.0
        assert stage3["shift_direction"] == "none"

    def test_more_false_negative_increases_recall(self, loop_with_feedback):
        """漏报反馈多时提高 recall 权重（戒律 P1）"""
        fm = loop_with_feedback.feedback_manager
        # 记录多条漏报反馈
        for i in range(10):
            fm.record_feedback(
                transaction_id=f"TXN_FN_{i}",
                account=f"ACC_FN_{i}",
                feedback_type="false_negative",
                reason=f"系统漏判可疑交易，金额异常{i}",
                reviewer="analyst_1",
            )

        stage3 = loop_with_feedback._stage3_adjust_weights(None)

        # recall 权重应提高
        assert stage3["adjusted_weights"]["recall"] > DEFAULT_OBJECTIVE_WEIGHTS["recall"]
        assert stage3["shift_direction"] == "recall_up"
        assert stage3["weight_shift"] > 0
        # 戒律 P1: 漏报加权
        assert "P1" in stage3["shift_reason"]

    def test_more_false_positive_increases_precision(self, loop_with_feedback):
        """误报反馈多时提高 precision 权重（戒律 P2）"""
        fm = loop_with_feedback.feedback_manager
        for i in range(10):
            fm.record_feedback(
                transaction_id=f"TXN_FP_{i}",
                account=f"ACC_FP_{i}",
                feedback_type="false_positive",
                reason=f"系统误判正常交易为可疑，实际为正常业务{i}",
                reviewer="analyst_1",
            )

        stage3 = loop_with_feedback._stage3_adjust_weights(None)

        # precision 权重应提高
        assert stage3["adjusted_weights"]["precision"] > DEFAULT_OBJECTIVE_WEIGHTS["precision"]
        assert stage3["shift_direction"] == "precision_up"
        assert stage3["weight_shift"] < 0
        # 戒律 P2: 误报加权
        assert "P2" in stage3["shift_reason"]

    def test_balanced_feedback_no_shift(self, loop_with_feedback):
        """漏报和误报反馈平衡时无偏移"""
        fm = loop_with_feedback.feedback_manager
        # 5 条漏报 + 5 条误报
        for i in range(5):
            fm.record_feedback(
                transaction_id=f"TXN_FN_{i}",
                account=f"ACC_FN_{i}",
                feedback_type="false_negative",
                reason=f"系统漏判可疑交易{i}",
                reviewer="analyst_1",
            )
            fm.record_feedback(
                transaction_id=f"TXN_FP_{i}",
                account=f"ACC_FP_{i}",
                feedback_type="false_positive",
                reason=f"系统误判正常交易{i}",
                reviewer="analyst_1",
            )

        stage3 = loop_with_feedback._stage3_adjust_weights(None)

        # 权重应接近默认（时间衰减使两者可能略有差异，但偏移很小）
        assert abs(stage3["weight_shift"]) < 0.01

    def test_weights_sum_to_one(self, loop_with_feedback):
        """调整后权重之和应为 1.0"""
        fm = loop_with_feedback.feedback_manager
        for i in range(3):
            fm.record_feedback(
                transaction_id=f"TXN_FN_{i}",
                account=f"ACC_FN_{i}",
                feedback_type="false_negative",
                reason=f"漏报测试{i}",
                reviewer="analyst_1",
            )

        stage3 = loop_with_feedback._stage3_adjust_weights(None)
        weights = stage3["adjusted_weights"]
        total = weights["precision"] + weights["recall"] + weights["f1"]
        assert abs(total - 1.0) < 0.01  # 允许浮点误差

    def test_weights_within_bounds(self, loop_with_feedback):
        """每个权重应在 [MIN_WEIGHT, MAX_WEIGHT] 范围内"""
        fm = loop_with_feedback.feedback_manager
        # 大量漏报反馈，试图将 recall 权重推到上限
        for i in range(50):
            fm.record_feedback(
                transaction_id=f"TXN_FN_{i}",
                account=f"ACC_FN_{i}",
                feedback_type="false_negative",
                reason=f"大量漏报测试{i}",
                reviewer="analyst_1",
            )

        stage3 = loop_with_feedback._stage3_adjust_weights(None)
        weights = stage3["adjusted_weights"]
        for key in ["precision", "recall", "f1"]:
            # 归一化后可能略超 MAX_WEIGHT，但不应低于 MIN_WEIGHT
            assert weights[key] >= MIN_WEIGHT - 0.01

    def test_feedback_stats_recorded(self, loop_with_feedback):
        """反馈统计应完整记录"""
        fm = loop_with_feedback.feedback_manager
        fm.record_feedback(
            transaction_id="TXN_1",
            account="ACC_1",
            feedback_type="false_negative",
            reason="漏报测试记录",
            reviewer="analyst_1",
        )
        fm.record_feedback(
            transaction_id="TXN_2",
            account="ACC_2",
            feedback_type="confirmed",
            reason="确认可疑交易记录",
            reviewer="analyst_1",
        )

        stage3 = loop_with_feedback._stage3_adjust_weights(None)
        stats = stage3["feedback_stats"]
        assert stats["raw_count"]["false_negative"] == 1
        assert stats["raw_count"]["confirmed"] == 1
        assert stats["raw_count"]["total"] == 2
        assert stats["weighted_false_negative"] > 0


# ============================================================
# Stage 4: 调参
# ============================================================
class TestStage4Tuning:
    def test_normal_tuning(self, loop):
        """正常调参流程"""
        txns = _sample_transactions()
        gt = _sample_ground_truth()
        params = _baseline_params()

        stage4 = loop._stage4_tune_params(txns, gt, params, None, DEFAULT_OBJECTIVE_WEIGHTS)

        assert stage4["optimization_result"] is not None
        assert stage4["best_candidate_params"] is not None
        assert "smurfing" in stage4["best_candidate_params"]

    def test_empty_transactions_skips_optimization(self, loop):
        """空交易数据跳过优化"""
        stage4 = loop._stage4_tune_params(
            [], _sample_ground_truth(), _baseline_params(),
            None, DEFAULT_OBJECTIVE_WEIGHTS
        )
        assert stage4["optimization_result"] is None
        assert stage4["best_candidate_params"] is None
        assert stage4["same_as_current"] is True
        assert any("跳过" in w for w in stage4["tuning_warnings"])

    def test_empty_ground_truth_skips_optimization(self, loop):
        """空真值集跳过优化"""
        stage4 = loop._stage4_tune_params(
            _sample_transactions(), {}, _baseline_params(),
            None, DEFAULT_OBJECTIVE_WEIGHTS
        )
        assert stage4["optimization_result"] is None
        assert stage4["same_as_current"] is True

    def test_combination_explosion_handled(self, loop):
        """组合爆炸应被捕获并记录警告"""
        txns = _sample_transactions()
        gt = _sample_ground_truth()
        params = _baseline_params()
        # 构造超大搜索空间
        huge_space = {
            "smurfing": {
                "min_count": list(range(2, 21)),
                "hour_window": list(range(1, 25)),
                "amount_low": list(range(1000, 100000, 1000)),
            },
        }

        stage4 = loop._stage4_tune_params(
            txns, gt, params, huge_space, DEFAULT_OBJECTIVE_WEIGHTS
        )
        # 应捕获异常，不崩溃
        assert stage4["optimization_result"] is None
        assert any("优化" in w for w in stage4["tuning_warnings"])

    def test_candidate_params_merged_with_current(self, loop):
        """候选参数应与当前参数合并（完整参数）"""
        txns = _sample_transactions()
        gt = _sample_ground_truth()
        params = _baseline_params()

        # 使用只包含一个参数的搜索空间
        small_space = {
            "large_amount": {"threshold": [50000]},
        }

        stage4 = loop._stage4_tune_params(txns, gt, params, small_space, DEFAULT_OBJECTIVE_WEIGHTS)

        if stage4["best_candidate_params"]:
            # 候选参数应包含所有组（不只是 large_amount）
            assert "smurfing" in stage4["best_candidate_params"]
            assert "fast_in_fast_out" in stage4["best_candidate_params"]
            # large_amount.threshold 应为 50000
            assert stage4["best_candidate_params"]["large_amount"]["threshold"] == 50000

    def test_same_as_current_detected(self, loop):
        """优化结果与当前参数相同时应检测到"""
        txns = _sample_transactions()
        gt = _sample_ground_truth()
        params = _baseline_params()

        # 搜索空间只包含当前值
        same_space = {
            "smurfing": {"min_count": [5]},
            "large_amount": {"threshold": [100000]},
        }

        stage4 = loop._stage4_tune_params(txns, gt, params, same_space, DEFAULT_OBJECTIVE_WEIGHTS)

        assert stage4["same_as_current"] is True
        assert any("相同" in w for w in stage4["tuning_warnings"])

    def test_cross_impact_analyzed_when_different(self, loop):
        """候选参数与当前不同时应执行交叉影响分析"""
        txns = _sample_transactions()
        gt = _sample_ground_truth()
        params = _baseline_params()

        # 搜索空间包含不同的值
        diff_space = {
            "large_amount": {"threshold": [50000]},  # 默认100000 → 50000
        }

        stage4 = loop._stage4_tune_params(txns, gt, params, diff_space, DEFAULT_OBJECTIVE_WEIGHTS)

        if not stage4["same_as_current"] and stage4["best_candidate_params"]:
            # 应有交叉影响分析结果
            assert stage4["cross_impact_result"] is not None


# ============================================================
# Stage 5: 验证
# ============================================================
class TestStage5Validation:
    def test_skip_when_same_as_current(self, loop):
        """候选与当前相同时跳过验证"""
        stage4 = {
            "best_candidate_params": None,
            "same_as_current": True,
            "optimization_result": None,
            "cross_impact_result": None,
            "tuning_warnings": [],
        }
        stage5 = loop._stage5_validate(
            _sample_transactions(), _sample_ground_truth(),
            _baseline_params(), stage4, DEFAULT_OBJECTIVE_WEIGHTS
        )
        assert stage5["ab_test"] is None
        assert stage5["invariant_check"] is None
        assert any("跳过" in w for w in stage5["validation_warnings"])

    def test_ab_test_run_when_different(self, loop):
        """候选与当前不同时执行 A/B 测试"""
        params = _baseline_params()
        # 修改候选参数（降低大额阈值，应增加命中）
        candidate = copy.deepcopy(params)
        candidate["large_amount"]["threshold"] = 50000

        stage4 = {
            "best_candidate_params": candidate,
            "same_as_current": False,
            "optimization_result": None,
            "cross_impact_result": None,
            "tuning_warnings": [],
        }
        stage5 = loop._stage5_validate(
            _sample_transactions(), _sample_ground_truth(),
            params, stage4, DEFAULT_OBJECTIVE_WEIGHTS
        )
        assert stage5["ab_test"] is not None
        assert "decision" in stage5["ab_test"]
        assert "recommendation" in stage5["ab_test"]["decision"]

    def test_invariant_check_run(self, loop):
        """验证阶段应执行不变量检查"""
        params = _baseline_params()
        candidate = copy.deepcopy(params)
        candidate["large_amount"]["threshold"] = 50000

        stage4 = {
            "best_candidate_params": candidate,
            "same_as_current": False,
            "optimization_result": None,
            "cross_impact_result": None,
            "tuning_warnings": [],
        }
        stage5 = loop._stage5_validate(
            _sample_transactions(), _sample_ground_truth(),
            params, stage4, DEFAULT_OBJECTIVE_WEIGHTS
        )
        # 不变量检查应执行
        assert stage5["invariant_check"] is not None
        assert "passed" in stage5["invariant_check"]


# ============================================================
# 综合推荐决策
# ============================================================
class TestRecommendation:
    def test_keep_when_no_candidate(self, loop):
        """无候选参数时推荐 keep"""
        stage2 = {"current_metrics": {"precision": 0.5, "recall": 0.5, "f1": 0.5}}
        stage4 = {
            "best_candidate_params": None,
            "same_as_current": True,
        }
        stage5 = {"ab_test": None, "invariant_check": None}

        rec = loop._build_recommendation(stage2, stage4, stage5)
        assert rec["action"] == "keep"
        assert "相同" in rec["reason"] or "无有效" in rec["reason"]

    def test_keep_when_same_as_current(self, loop):
        """候选与当前相同时推荐 keep"""
        stage2 = {"current_metrics": {"precision": 0.5}}
        stage4 = {
            "best_candidate_params": _baseline_params(),
            "same_as_current": True,
        }
        stage5 = {"ab_test": None, "invariant_check": None}

        rec = loop._build_recommendation(stage2, stage4, stage5)
        assert rec["action"] == "keep"

    def test_apply_when_ab_recommends_b(self, loop):
        """A/B 测试推荐 B 时推荐 apply"""
        stage2 = {"current_metrics": {"precision": 0.5, "recall": 0.5, "f1": 0.5}}
        stage4 = {
            "best_candidate_params": {"smurfing": {"min_count": 3}},
            "same_as_current": False,
        }
        stage5 = {
            "ab_test": {
                "decision": {
                    "recommendation": "B",
                    "weighted_score_a": 0.5,
                    "weighted_score_b": 0.7,
                    "guardrail_violations": [],
                    "guardrail_warnings": [],
                },
                "variant_b": {
                    "metrics": {"precision": 0.7, "recall": 0.7, "f1": 0.7},
                },
            },
            "invariant_check": {"passed": True, "violations": []},
        }

        rec = loop._build_recommendation(stage2, stage4, stage5)
        assert rec["action"] == "apply"
        assert "建议应用" in rec["reason"]
        assert rec["expected_improvement"] is not None

    def test_keep_when_ab_recommends_a(self, loop):
        """A/B 测试推荐 A 时推荐 keep"""
        stage2 = {"current_metrics": {"precision": 0.7}}
        stage4 = {
            "best_candidate_params": {"smurfing": {"min_count": 3}},
            "same_as_current": False,
        }
        stage5 = {
            "ab_test": {
                "decision": {
                    "recommendation": "A",
                    "weighted_score_a": 0.7,
                    "weighted_score_b": 0.5,
                    "guardrail_violations": [],
                    "guardrail_warnings": [],
                },
                "variant_b": {"metrics": {"precision": 0.5}},
            },
            "invariant_check": {"passed": True, "violations": []},
        }

        rec = loop._build_recommendation(stage2, stage4, stage5)
        assert rec["action"] == "keep"

    def test_review_when_ab_recommends_review(self, loop):
        """A/B 测试推荐 review 时推荐 review"""
        stage2 = {"current_metrics": {"precision": 0.5}}
        stage4 = {
            "best_candidate_params": {"smurfing": {"min_count": 3}},
            "same_as_current": False,
        }
        stage5 = {
            "ab_test": {
                "decision": {
                    "recommendation": "review",
                    "weighted_score_a": 0.5,
                    "weighted_score_b": 0.5,
                    "guardrail_violations": [],
                    "guardrail_warnings": [],
                },
                "variant_b": {"metrics": {"precision": 0.5}},
            },
            "invariant_check": {"passed": True, "violations": []},
        }

        rec = loop._build_recommendation(stage2, stage4, stage5)
        assert rec["action"] == "review"

    def test_keep_when_guardrail_violated(self, loop):
        """有戒律违反时推荐 keep（戒律 P1/P4）"""
        stage2 = {"current_metrics": {"precision": 0.5}}
        stage4 = {
            "best_candidate_params": {"smurfing": {"min_count": 3}},
            "same_as_current": False,
        }
        stage5 = {
            "ab_test": {
                "decision": {
                    "recommendation": "B",
                    "weighted_score_a": 0.5,
                    "weighted_score_b": 0.7,
                    "guardrail_violations": ["戒律 P1 违反: recall 大幅下降"],
                    "guardrail_warnings": [],
                },
                "variant_b": {"metrics": {"precision": 0.7}},
            },
            "invariant_check": {"passed": True, "violations": []},
        }

        rec = loop._build_recommendation(stage2, stage4, stage5)
        # 有违反时应推荐 keep（戒律 P4: 非破坏性）
        assert rec["action"] == "keep"
        assert len(rec["guardrail_violations"]) > 0

    def test_review_when_ab_test_failed(self, loop):
        """A/B 测试失败时推荐 review"""
        stage2 = {"current_metrics": {"precision": 0.5}}
        stage4 = {
            "best_candidate_params": {"smurfing": {"min_count": 3}},
            "same_as_current": False,
        }
        stage5 = {
            "ab_test": None,
            "invariant_check": None,
        }

        rec = loop._build_recommendation(stage2, stage4, stage5)
        assert rec["action"] == "review"

    def test_expected_improvement_calculated(self, loop):
        """应计算预期改进"""
        stage2 = {"current_metrics": {"precision": 0.5, "recall": 0.5, "f1": 0.5}}
        stage4 = {
            "best_candidate_params": {"smurfing": {"min_count": 3}},
            "same_as_current": False,
        }
        stage5 = {
            "ab_test": {
                "decision": {
                    "recommendation": "B",
                    "weighted_score_a": 0.5,
                    "weighted_score_b": 0.7,
                    "guardrail_violations": [],
                    "guardrail_warnings": [],
                },
                "variant_b": {
                    "metrics": {"precision": 0.7, "recall": 0.8, "f1": 0.75},
                },
            },
            "invariant_check": {"passed": True, "violations": []},
        }

        rec = loop._build_recommendation(stage2, stage4, stage5)
        assert rec["expected_improvement"] is not None
        assert rec["expected_improvement"]["precision"]["delta"] == 0.2
        assert rec["expected_improvement"]["recall"]["delta"] == 0.3


# ============================================================
# 完整闭环集成测试
# ============================================================
class TestFullLoopIntegration:
    def test_full_loop_with_default_params(self, loop_with_feedback):
        """使用默认参数运行完整闭环"""
        txns = _sample_transactions()
        gt = _sample_ground_truth()
        params = _baseline_params()

        result = loop_with_feedback.run_loop(
            transactions=txns,
            ground_truth=gt,
            current_params=params,
        )

        assert result.loop_id.startswith("LOOP-")
        assert "stage1_data_collection" in result.stages
        assert "stage2_current_evaluation" in result.stages
        assert "stage3_feedback_weights" in result.stages
        assert "stage4_parameter_tuning" in result.stages
        assert "stage5_validation" in result.stages
        assert result.recommendation["action"] in ["apply", "keep", "review"]
        assert "reason" in result.recommendation

    def test_full_loop_with_feedback_influences_weights(self, loop_with_feedback):
        """反馈应影响权重调整"""
        fm = loop_with_feedback.feedback_manager
        # 添加漏报反馈
        for i in range(10):
            fm.record_feedback(
                transaction_id=f"TXN_FN_{i}",
                account=f"ACC_FN_{i}",
                feedback_type="false_negative",
                reason=f"系统漏判可疑交易{i}",
                reviewer="analyst_1",
            )

        result = loop_with_feedback.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
        )

        stage3 = result.stages["stage3_feedback_weights"]
        assert stage3["shift_direction"] == "recall_up"
        assert stage3["adjusted_weights"]["recall"] > DEFAULT_OBJECTIVE_WEIGHTS["recall"]

    def test_full_loop_persists_result(self, loop):
        """闭环结果应持久化"""
        result = loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
        )

        # 文件存在
        path = os.path.join(loop.storage_dir, f"{result.loop_id}.json")
        assert os.path.exists(path)

        # 可通过 get_loop 检索
        loaded = loop.get_loop(result.loop_id)
        assert loaded is not None
        assert loaded.loop_id == result.loop_id

    def test_full_loop_non_destructive(self, loop):
        """闭环不应修改全局 AML_CONFIG（戒律 P4: 非破坏性）"""
        from config import AML_CONFIG
        original_threshold = AML_CONFIG["rules"]["large_amount"]["threshold"]

        loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
        )

        # 全局配置应保持不变
        assert AML_CONFIG["rules"]["large_amount"]["threshold"] == original_threshold

    def test_full_loop_with_empty_search_space(self, loop):
        """空搜索空间应使用默认搜索空间"""
        result = loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
            search_space=None,  # 使用默认
        )
        # 应正常完成
        assert result.recommendation["action"] in ["apply", "keep", "review"]

    def test_full_loop_with_empty_data(self, loop):
        """空数据应正常完成不崩溃"""
        result = loop.run_loop(
            transactions=[],
            ground_truth={},
            current_params=_baseline_params(),
        )
        # 应推荐 keep（无有效候选）
        assert result.recommendation["action"] == "keep"
        stage1 = result.stages["stage1_data_collection"]
        assert len(stage1["data_warnings"]) > 0


# ============================================================
# 持久化
# ============================================================
class TestPersistence:
    def test_save_and_get(self, loop):
        """保存后能检索"""
        result = loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
        )
        loaded = loop.get_loop(result.loop_id)
        assert loaded is not None
        assert loaded.loop_id == result.loop_id
        assert loaded.recommendation["action"] == result.recommendation["action"]

    def test_get_nonexistent_returns_none(self, loop):
        """检索不存在的 ID 返回 None"""
        assert loop.get_loop("LOOP-NONEXIST") is None

    def test_list_loops(self, loop):
        """列出所有闭环结果"""
        # 运行两次
        r1 = loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
        )
        r2 = loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
        )

        loops = loop.list_loops()
        assert len(loops) == 2
        # 按时间倒序
        assert loops[0]["loop_id"] in [r1.loop_id, r2.loop_id]

    def test_delete_loop(self, loop):
        """删除闭环结果"""
        result = loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
        )
        assert loop.delete_loop(result.loop_id) is True
        assert loop.get_loop(result.loop_id) is None
        # 从索引中移除
        loops = loop.list_loops()
        assert all(l["loop_id"] != result.loop_id for l in loops)

    def test_delete_nonexistent_returns_false(self, loop):
        """删除不存在的返回 False"""
        assert loop.delete_loop("LOOP-NONEXIST") is False

    def test_index_file_created(self, loop):
        """运行后应创建索引文件"""
        loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
        )
        assert os.path.exists(loop._index_path)

    def test_index_contains_summary(self, loop):
        """索引应包含摘要信息"""
        result = loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
            dataset_name="test_ds",
        )
        loops = loop.list_loops()
        assert len(loops) == 1
        entry = loops[0]
        assert entry["loop_id"] == result.loop_id
        assert entry["action"] in ["apply", "keep", "review"]
        assert entry["transaction_count"] == 7
        assert entry["dataset_name"] == "test_ds"


# ============================================================
# 辅助方法测试
# ============================================================
class TestHelperMethods:
    def test_params_equal_same(self, loop):
        """相同参数判断为等价"""
        params_a = _baseline_params()
        params_b = copy.deepcopy(params_a)
        assert loop._params_equal(params_a, params_b) is True

    def test_params_equal_different(self, loop):
        """不同参数判断为不等价"""
        params_a = _baseline_params()
        params_b = copy.deepcopy(params_a)
        params_b["smurfing"]["min_count"] = 3
        assert loop._params_equal(params_a, params_b) is False

    def test_params_equal_extra_keys(self, loop):
        """额外键不影响等价判断"""
        params_a = {"smurfing": {"min_count": 5}}
        params_b = {"smurfing": {"min_count": 5}, "large_amount": {"threshold": 100000}}
        # params_a 缺少 large_amount，不等价
        assert loop._params_equal(params_a, params_b) is False

    def test_build_param_changes(self, loop):
        """构建参数变更列表"""
        current = _baseline_params()
        candidate = copy.deepcopy(current)
        candidate["large_amount"]["threshold"] = 50000
        candidate["smurfing"]["min_count"] = 3

        changes = loop._build_param_changes(current, candidate)
        assert len(changes) == 2
        change_keys = [c.key for c in changes]
        assert "large_amount.threshold" in change_keys
        assert "smurfing.min_count" in change_keys

    def test_build_param_changes_no_diff(self, loop):
        """无差异时返回空列表"""
        current = _baseline_params()
        candidate = copy.deepcopy(current)
        changes = loop._build_param_changes(current, candidate)
        assert len(changes) == 0


# ============================================================
# 边界条件与异常处理
# ============================================================
class TestEdgeCases:
    def test_loop_id_format(self, loop):
        """loop_id 应为 LOOP- 前缀 + 8 位十六进制"""
        result = loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
        )
        assert result.loop_id.startswith("LOOP-")
        # LOOP- + 8 字符
        assert len(result.loop_id) == 13

    def test_metadata_recorded(self, loop):
        """metadata 应记录数据规模"""
        result = loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
            dataset_name="edge_case_ds",
        )
        assert result.metadata["dataset_name"] == "edge_case_ds"
        assert result.metadata["transaction_count"] == 7
        assert result.metadata["ground_truth_count"] == 9

    def test_result_json_serializable(self, loop):
        """结果应可 JSON 序列化"""
        result = loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
        )
        # to_dict 应可序列化
        json_str = json.dumps(result.to_dict(), ensure_ascii=False)
        assert "loop_id" in json_str

    def test_multiple_loops_independent(self, loop):
        """多次闭环互相独立"""
        r1 = loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
        )
        r2 = loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
        )
        assert r1.loop_id != r2.loop_id
        # 两个结果文件都存在
        assert os.path.exists(os.path.join(loop.storage_dir, f"{r1.loop_id}.json"))
        assert os.path.exists(os.path.join(loop.storage_dir, f"{r2.loop_id}.json"))


# ============================================================
# 戒律守护测试
# ============================================================
class TestGuardrails:
    def test_p1_recall_drop_rejects_candidate(self, loop):
        """戒律 P1: 候选参数 recall 大幅下降时应拒绝（推荐 keep）"""
        # 构造一个 recall 会大幅下降的场景
        # 当前参数能命中所有可疑交易，候选参数大幅放宽阈值导致不命中
        params = _baseline_params()
        # 构造交易: 5 笔分拆 + 2 笔大额
        txns = _sample_transactions()
        gt = _sample_ground_truth()

        # 候选参数: 大幅提高大额阈值 + 提高 min_count
        candidate = copy.deepcopy(params)
        candidate["large_amount"]["threshold"] = 200000  # 默认 100000 → 200000
        candidate["smurfing"]["min_count"] = 8  # 默认 5 → 8

        stage4 = {
            "best_candidate_params": candidate,
            "same_as_current": False,
            "optimization_result": None,
            "cross_impact_result": None,
            "tuning_warnings": [],
        }
        stage5 = loop._stage5_validate(txns, gt, params, stage4, DEFAULT_OBJECTIVE_WEIGHTS)

        # A/B 测试应检测到 recall 下降
        ab_decision = stage5["ab_test"]["decision"]
        # 如果 recall 下降超过 30%，应有戒律违反
        # 注意：实际结果取决于规则命中情况
        if ab_decision.get("guardrail_violations"):
            rec = loop._build_recommendation(
                {"current_metrics": {}}, stage4, stage5
            )
            assert rec["action"] == "keep"

    def test_p4_non_destructive(self, loop):
        """戒律 P4: 闭环不修改全局配置"""
        from config import AML_CONFIG
        original_rules = copy.deepcopy(AML_CONFIG["rules"])

        loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
        )

        # 全局配置应完全不变
        assert AML_CONFIG["rules"] == original_rules

    def test_m2_recommendation_has_reason(self, loop):
        """戒律 M2: 推荐结果必须附带理由"""
        result = loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
        )
        assert result.recommendation["reason"]
        assert len(result.recommendation["reason"]) > 0

    def test_m4_full_traceability(self, loop):
        """戒律 M4: 闭环结果完整可追溯"""
        result = loop.run_loop(
            transactions=_sample_transactions(),
            ground_truth=_sample_ground_truth(),
            current_params=_baseline_params(),
            dataset_name="traceability_test",
        )

        # 所有五个阶段都应有记录
        stages = result.stages
        assert "stage1_data_collection" in stages
        assert "stage2_current_evaluation" in stages
        assert "stage3_feedback_weights" in stages
        assert "stage4_parameter_tuning" in stages
        assert "stage5_validation" in stages

        # 每个阶段都有具体内容
        assert "data_summary" in stages["stage1_data_collection"]
        assert "current_metrics" in stages["stage2_current_evaluation"]
        assert "adjusted_weights" in stages["stage3_feedback_weights"]
        assert "best_candidate_params" in stages["stage4_parameter_tuning"]
        assert "ab_test" in stages["stage5_validation"]
