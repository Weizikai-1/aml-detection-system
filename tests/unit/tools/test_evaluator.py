"""
评估器测试

覆盖:
- 混淆矩阵计算 (Precision/Recall/F1/Accuracy/Specificity)
- 预测结果评估 (总体/规则级/阈值扫描)
- 工作流状态评估
- 报告格式化
"""
import os
import tempfile

import pytest

from tools.ground_truth_builder import GroundTruthDataset, GroundTruthRecord
from tools.evaluator import (
    ConfusionMatrix,
    EvaluationResult,
    evaluate_predictions,
    evaluate_workflow_state,
    format_evaluation_report,
    save_evaluation,
)


# ============================================================
# ConfusionMatrix
# ============================================================
def test_confusion_matrix_perfect():
    """完美预测的指标"""
    m = ConfusionMatrix(tp=10, fp=0, tn=90, fn=0)
    assert m.precision == 1.0
    assert m.recall == 1.0
    assert m.f1_score == 1.0
    assert m.accuracy == 1.0
    assert m.specificity == 1.0


def test_confusion_matrix_zero():
    """全部分类为正常的指标"""
    m = ConfusionMatrix(tp=0, fp=0, tn=100, fn=10)
    assert m.precision == 0.0
    assert m.recall == 0.0
    assert m.f1_score == 0.0
    assert abs(m.accuracy - 100 / 110) < 0.001


def test_confusion_matrix_balanced():
    """平衡情况的指标计算"""
    m = ConfusionMatrix(tp=8, fp=2, tn=88, fn=2)
    assert m.precision == 0.8
    assert m.recall == 0.8
    assert m.f1_score == 0.8


def test_confusion_matrix_to_dict():
    """序列化包含所有字段"""
    m = ConfusionMatrix(tp=1, fp=1, tn=1, fn=1)
    d = m.to_dict()
    assert d["tp"] == 1
    assert d["precision"] == 0.5
    assert d["recall"] == 0.5


# ============================================================
# evaluate_predictions
# ============================================================
def _make_gt(suspicious_ids: set, normal_ids: set) -> GroundTruthDataset:
    """构造真值集"""
    ds = GroundTruthDataset(name="test_gt", description="unit test")
    for tid in suspicious_ids:
        ds.add_record(GroundTruthRecord(tid, is_suspicious=True))
    for tid in normal_ids:
        ds.add_record(GroundTruthRecord(tid, is_suspicious=False))
    return ds


def test_evaluate_perfect_predictions():
    """预测完全匹配真值"""
    gt = _make_gt({"S1", "S2"}, {"N1", "N2"})
    predictions = [
        {"transaction_id": "S1", "rule_hits": ["rule1"], "risk_score": 80},
        {"transaction_id": "S2", "rule_hits": ["rule1"], "risk_score": 75},
    ]
    result = evaluate_predictions(gt, predictions)
    assert result.overall.tp == 2
    assert result.overall.tn == 2
    assert result.overall.fp == 0
    assert result.overall.fn == 0
    assert result.overall.f1_score == 1.0


def test_evaluate_miss_one():
    """漏报一个可疑交易"""
    gt = _make_gt({"S1", "S2"}, {"N1"})
    predictions = [
        {"transaction_id": "S1", "rule_hits": ["rule1"], "risk_score": 80},
    ]
    result = evaluate_predictions(gt, predictions)
    assert result.overall.tp == 1
    assert result.overall.fn == 1
    assert result.overall.tn == 1
    assert result.overall.fp == 0
    assert result.overall.recall == 0.5


def test_evaluate_false_positive():
    """误报一个正常交易"""
    gt = _make_gt({"S1"}, {"N1", "N2"})
    predictions = [
        {"transaction_id": "S1", "rule_hits": ["rule1"], "risk_score": 80},
        {"transaction_id": "N1", "rule_hits": ["rule1"], "risk_score": 60},
    ]
    result = evaluate_predictions(gt, predictions)
    assert result.overall.tp == 1
    assert result.overall.fp == 1
    assert result.overall.tn == 1
    assert result.overall.fn == 0
    assert result.overall.precision == 0.5


def test_evaluate_with_pending_skipped():
    """待定记录不参与评估"""
    ds = GroundTruthDataset(name="pending_test")
    ds.add_record(GroundTruthRecord("S1", is_suspicious=True))
    ds.add_record(GroundTruthRecord("N1", is_suspicious=False))
    ds.add_record(GroundTruthRecord("U1", is_suspicious=None))  # pending

    predictions = [{"transaction_id": "S1", "rule_hits": ["r1"]}]
    result = evaluate_predictions(ds, predictions)
    assert result.total_evaluated == 2
    assert result.pending_skipped == 1


def test_evaluate_rule_level():
    """规则级评估"""
    gt = _make_gt({"S1", "S2"}, {"N1", "N2"})
    predictions = [
        {"transaction_id": "S1", "rule_hits": ["rule_a"], "risk_score": 80},
        {"transaction_id": "S2", "rule_hits": ["rule_b"], "risk_score": 75},
        {"transaction_id": "N1", "rule_hits": ["rule_a"], "risk_score": 60},  # FP for rule_a
    ]
    result = evaluate_predictions(gt, predictions)
    assert "rule_a" in result.by_rule
    assert "rule_b" in result.by_rule

    rule_a = result.by_rule["rule_a"].matrix
    assert rule_a.tp == 1  # S1
    assert rule_a.fp == 1  # N1
    assert rule_a.fn == 1  # S2 missed


def test_evaluate_threshold_scan():
    """阈值扫描"""
    gt = _make_gt({"S1", "S2"}, {"N1", "N2"})
    predictions = [
        {"transaction_id": "S1", "rule_hits": ["r1"], "risk_score": 80},
        {"transaction_id": "S2", "rule_hits": ["r1"], "risk_score": 50},
        {"transaction_id": "N1", "rule_hits": ["r1"], "risk_score": 60},
    ]
    result = evaluate_predictions(gt, predictions, scan_thresholds=[40, 60, 80])
    assert len(result.threshold_scan) == 3

    # threshold=80: 只命中 S1
    t80 = [t for t in result.threshold_scan if t.threshold == 80][0]
    assert t80.matrix.tp == 1
    assert t80.matrix.fn == 1

    # threshold=40: 命中 S1, S2, N1
    t40 = [t for t in result.threshold_scan if t.threshold == 40][0]
    assert t40.matrix.tp == 2
    assert t40.matrix.fp == 1


def test_evaluate_suspicious_transaction_format():
    """支持 SuspiciousTransaction 格式（含 transaction 嵌套）"""
    gt = _make_gt({"S1"}, {"N1"})
    predictions = [
        {
            "transaction": {"transaction_id": "S1"},
            "rule_hits": ["r1"],
            "risk_score": 80,
        },
    ]
    result = evaluate_predictions(gt, predictions)
    assert result.overall.tp == 1  # S1 正确命中
    assert result.overall.tn == 1  # N1 正确未命中
    assert result.overall.fp == 0
    assert result.overall.fn == 0


# ============================================================
# evaluate_workflow_state
# ============================================================
def test_evaluate_workflow_state_fallback():
    """优先使用 llm_reviewed，fallback 到 rule_hits"""
    gt = _make_gt({"S1"}, {"N1"})
    state = {
        "llm_reviewed": [{"transaction_id": "S1", "rule_hits": ["r1"], "risk_score": 80}],
        "rule_hits": [{"transaction_id": "S1", "rule_hits": ["r1"], "risk_score": 80}],
    }
    result = evaluate_workflow_state(gt, state)
    assert result.overall.tp == 1


def test_evaluate_workflow_state_rule_fallback():
    """llm_reviewed 为空时 fallback 到 rule_hits"""
    gt = _make_gt({"S1"}, {"N1"})
    state = {
        "llm_reviewed": [],
        "rule_hits": [{"transaction_id": "S1", "rule_hits": ["r1"], "risk_score": 80}],
    }
    result = evaluate_workflow_state(gt, state)
    assert result.overall.tp == 1


# ============================================================
# 报告与持久化
# ============================================================
def test_format_report_contains_key_metrics():
    """报告包含关键指标"""
    result = EvaluationResult(
        eval_id="E1",
        eval_time="2026-01-01T00:00:00",
        ground_truth_name="gt",
        ground_truth_version="1.0",
        total_evaluated=10,
        pending_skipped=0,
        overall=ConfusionMatrix(tp=8, fp=2, tn=88, fn=2),
    )
    report = format_evaluation_report(result)
    assert "Precision" in report
    assert "Recall" in report
    assert "F1 Score" in report
    assert "混淆矩阵" in report
    assert "E1" in report


def test_save_evaluation():
    """评估结果保存为 JSON 和 Markdown"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import tools.evaluator as ev_mod
        orig_dir = ev_mod.EVALUATIONS_DIR
        ev_mod.EVALUATIONS_DIR = tmpdir
        try:
            result = EvaluationResult(
                eval_id="E_SAVE",
                eval_time="2026-01-01T00:00:00",
                ground_truth_name="gt",
                ground_truth_version="1.0",
                total_evaluated=10,
                pending_skipped=0,
                overall=ConfusionMatrix(tp=5, fp=1, tn=3, fn=1),
            )
            path = save_evaluation(result, name="test_save")
            assert os.path.exists(path)
            assert os.path.exists(path.replace(".json", ".md"))
        finally:
            ev_mod.EVALUATIONS_DIR = orig_dir


# ============================================================
# 边界测试
# ============================================================
def test_empty_predictions():
    """空预测结果"""
    gt = _make_gt({"S1"}, {"N1"})
    result = evaluate_predictions(gt, [])
    assert result.overall.tp == 0
    assert result.overall.tn == 1
    assert result.overall.fn == 1
    assert result.overall.fp == 0
