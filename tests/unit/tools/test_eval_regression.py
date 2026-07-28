"""
回归对比脚本测试

覆盖:
- 基线保存与加载
- 评估结果对比（退化/提升/稳定）
- 回归检测报告生成
- 离线评估集成（mock）
"""
import json
import os
import tempfile

import pytest

from tools.evaluator import EvaluationResult, ConfusionMatrix
from tools.eval_regression import (
    EvaluationBaseline,
    compare_evaluations,
    RegressionReport,
    RegressionDelta,
)


# ============================================================
# EvaluationBaseline
# ============================================================
def test_baseline_save_load():
    """基线保存与加载"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import tools.eval_regression as reg_mod
        orig_file = reg_mod.BASELINE_FILE
        reg_mod.BASELINE_FILE = os.path.join(tmpdir, "_baseline.json")
        try:
            result = EvaluationResult(
                eval_id="BASE1",
                eval_time="2026-01-01T00:00:00",
                ground_truth_name="gt",
                ground_truth_version="1.0",
                total_evaluated=100,
                pending_skipped=0,
                overall=ConfusionMatrix(tp=80, fp=10, tn=5, fn=5),
            )
            mgr = EvaluationBaseline()
            mgr.set_baseline(result, notes="test baseline")

            mgr2 = EvaluationBaseline.load()
            assert mgr2 is not None
            assert mgr2.baseline.eval_id == "BASE1"
            assert mgr2.notes == "test baseline"
        finally:
            reg_mod.BASELINE_FILE = orig_file


def test_baseline_load_missing():
    """无基线文件时返回 None"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import tools.eval_regression as reg_mod
        orig_file = reg_mod.BASELINE_FILE
        reg_mod.BASELINE_FILE = os.path.join(tmpdir, "nonexistent.json")
        try:
            mgr = EvaluationBaseline.load()
            assert mgr is None
        finally:
            reg_mod.BASELINE_FILE = orig_file


# ============================================================
# compare_evaluations
# ============================================================
def test_compare_stable():
    """指标无变化时通过"""
    baseline = EvaluationResult(
        eval_id="B1", eval_time="", ground_truth_name="", ground_truth_version="",
        total_evaluated=100, pending_skipped=0,
        overall=ConfusionMatrix(tp=80, fp=10, tn=5, fn=5),
    )
    current = EvaluationResult(
        eval_id="C1", eval_time="", ground_truth_name="", ground_truth_version="",
        total_evaluated=100, pending_skipped=0,
        overall=ConfusionMatrix(tp=80, fp=10, tn=5, fn=5),
    )
    report = compare_evaluations(baseline, current)
    assert report.is_pass is True
    assert len(report.degraded) == 0
    assert report.summary == "回归通过，指标稳定"


def test_compare_improved():
    """指标提升时通过"""
    baseline = EvaluationResult(
        eval_id="B1", eval_time="", ground_truth_name="", ground_truth_version="",
        total_evaluated=100, pending_skipped=0,
        overall=ConfusionMatrix(tp=80, fp=20, tn=0, fn=0),
    )
    # precision 从 0.8 -> 1.0
    current = EvaluationResult(
        eval_id="C1", eval_time="", ground_truth_name="", ground_truth_version="",
        total_evaluated=100, pending_skipped=0,
        overall=ConfusionMatrix(tp=80, fp=0, tn=20, fn=0),
    )
    report = compare_evaluations(baseline, current)
    assert report.is_pass is True
    assert len(report.improved) > 0
    assert "提升" in report.summary


def test_compare_degraded_precision():
    """Precision 下降超过阈值时失败"""
    baseline = EvaluationResult(
        eval_id="B1", eval_time="", ground_truth_name="", ground_truth_version="",
        total_evaluated=100, pending_skipped=0,
        overall=ConfusionMatrix(tp=90, fp=10, tn=0, fn=0),
    )
    # precision 从 0.9 -> 0.5，下降 44%
    current = EvaluationResult(
        eval_id="C1", eval_time="", ground_truth_name="", ground_truth_version="",
        total_evaluated=100, pending_skipped=0,
        overall=ConfusionMatrix(tp=50, fp=50, tn=0, fn=0),
    )
    report = compare_evaluations(baseline, current)
    assert report.is_pass is False
    assert len(report.degraded) > 0
    degraded_metrics = [d.metric for d in report.degraded]
    assert "precision" in degraded_metrics
    assert "退化" in report.summary


def test_compare_degraded_f1():
    """F1 下降超过 5% 时失败"""
    baseline = EvaluationResult(
        eval_id="B1", eval_time="", ground_truth_name="", ground_truth_version="",
        total_evaluated=100, pending_skipped=0,
        overall=ConfusionMatrix(tp=90, fp=10, tn=0, fn=0),
    )
    # F1 从 ~0.947 -> ~0.857，下降约 9.5%
    current = EvaluationResult(
        eval_id="C1", eval_time="", ground_truth_name="", ground_truth_version="",
        total_evaluated=100, pending_skipped=0,
        overall=ConfusionMatrix(tp=80, fp=10, tn=0, fn=10),
    )
    report = compare_evaluations(baseline, current)
    assert report.is_pass is False
    degraded_metrics = [d.metric for d in report.degraded]
    assert "f1_score" in degraded_metrics


def test_compare_report_to_dict():
    """回归报告序列化"""
    baseline = EvaluationResult(
        eval_id="B1", eval_time="2026-01-01", ground_truth_name="gt", ground_truth_version="1",
        total_evaluated=10, pending_skipped=0,
        overall=ConfusionMatrix(tp=5, fp=2, tn=2, fn=1),
    )
    current = EvaluationResult(
        eval_id="C1", eval_time="2026-01-02", ground_truth_name="gt", ground_truth_version="1",
        total_evaluated=10, pending_skipped=0,
        overall=ConfusionMatrix(tp=6, fp=1, tn=2, fn=1),
    )
    report = compare_evaluations(baseline, current)
    d = report.to_dict()
    assert d["baseline_eval_id"] == "B1"
    assert d["current_eval_id"] == "C1"
    assert d["is_pass"] is True
    assert "deltas" in d
    assert "improved" in d
    assert "degraded" in d


# ============================================================
# RegressionDelta
# ============================================================
def test_delta_calculation():
    """变化量计算正确"""
    d = RegressionDelta(
        metric="f1", baseline=0.8, current=0.7,
        delta=-0.1, delta_pct=-0.125, is_degradation=True,
    )
    assert d.delta == -0.1
    assert d.is_degradation is True


# ============================================================
# 边界测试
# ============================================================
def test_compare_zero_baseline():
    """基线为 0 时不除零报错"""
    baseline = EvaluationResult(
        eval_id="B1", eval_time="", ground_truth_name="", ground_truth_version="",
        total_evaluated=100, pending_skipped=0,
        overall=ConfusionMatrix(tp=0, fp=0, tn=100, fn=0),
    )
    current = EvaluationResult(
        eval_id="C1", eval_time="", ground_truth_name="", ground_truth_version="",
        total_evaluated=100, pending_skipped=0,
        overall=ConfusionMatrix(tp=0, fp=0, tn=100, fn=0),
    )
    report = compare_evaluations(baseline, current)
    assert report.is_pass is True
