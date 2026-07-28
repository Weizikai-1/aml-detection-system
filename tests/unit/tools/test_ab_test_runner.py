"""
A/B 测试框架单元测试

覆盖:
- ABTestVariant / ABTestResult 数据结构与序列化
- run_test 基本流程（B 更优 / B 更差 / 平局）
- 戒律 P1 守护（recall 下降拒绝推荐 B）
- 戒律 P2 警告（总命中激增）
- 公平性（两变体使用同一数据）
- 持久化（save / get / list / delete）
- 损坏文件恢复
- 边界条件
"""
import copy
import json
import os
import tempfile

import pytest

from tools.ab_test_runner import (
    ABTestRunner,
    ABTestVariant,
    ABTestResult,
    DEFAULT_METRIC_WEIGHTS,
    RISK_RECALL_DROP_REJECT_RATIO,
    HITS_SURGE_WARNING_RATIO,
)
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


def _smurfing_txns(prefix: str, count: int, receiver: str = "RECV_A") -> list:
    """构造 count 笔分拆转账（4.5万，同一收款人，不同付款人，1小时内）"""
    return [
        _make_txn(
            f"{prefix}_{i}", f"PAYER_{prefix}_{i}", receiver, 45000.0,
            f"2026-07-01T10:{i:02d}:00"
        )
        for i in range(count)
    ]


# ============================================================
# ABTestVariant
# ============================================================
def test_variant_creation():
    """变体创建"""
    v = ABTestVariant("baseline", {"smurfing": {"min_count": 5}})
    assert v.name == "baseline"
    assert v.params == {"smurfing": {"min_count": 5}}
    assert v.metrics == {}


def test_variant_roundtrip():
    """变体序列化与反序列化"""
    v = ABTestVariant(
        "candidate",
        {"smurfing": {"min_count": 3}},
        {"precision": 0.8, "recall": 0.9, "weighted_score": 0.85},
    )
    d = v.to_dict()
    v2 = ABTestVariant.from_dict(d)
    assert v2.name == v.name
    assert v2.params == v.params
    assert v2.metrics == v.metrics


def test_variant_params_deep_copied():
    """变体参数深拷贝"""
    params = {"smurfing": {"min_count": 5}}
    v = ABTestVariant("baseline", params)
    params["smurfing"]["min_count"] = 99
    assert v.params["smurfing"]["min_count"] == 5


def test_variant_weighted_score():
    """加权得分从 metrics 读取"""
    v = ABTestVariant("a", {}, {"weighted_score": 0.75})
    assert v.weighted_score == 0.75
    v2 = ABTestVariant("b", {})
    assert v2.weighted_score == 0.0


# ============================================================
# ABTestResult
# ============================================================
def test_result_roundtrip():
    """结果序列化与反序列化"""
    va = ABTestVariant("baseline", {"smurfing": {"min_count": 5}}, {"recall": 0.8})
    vb = ABTestVariant("candidate", {"smurfing": {"min_count": 3}}, {"recall": 0.9})
    result = ABTestResult(
        test_id="ABT-TEST01",
        test_name="test",
        timestamp="2026-07-28T10:00:00",
        variant_a=va,
        variant_b=vb,
        comparison={"recall": {"a": 0.8, "b": 0.9, "delta": 0.1}},
        decision={"recommendation": "B", "reason": "B更好"},
        metric_weights=DEFAULT_METRIC_WEIGHTS,
        ground_truth_name="gt_v1",
    )
    d = result.to_dict()
    result2 = ABTestResult.from_dict(d)
    assert result2.test_id == result.test_id
    assert result2.variant_a.name == "baseline"
    assert result2.variant_b.metrics["recall"] == 0.9
    assert result2.decision["recommendation"] == "B"


# ============================================================
# run_test - 基本流程
# ============================================================
def test_run_test_b_better():
    """B 参数更优（降低分拆最小笔数，捕获更多可疑）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        # 3 笔分拆转账（真实可疑）
        txns = _smurfing_txns("SMURF", 3)
        gt = {f"SMURF_{i}": True for i in range(3)}

        # A: min_count=5（默认，3笔不够，不命中）
        # B: min_count=3（3笔刚好命中）
        result = runner.run_test(
            test_name="smurfing_lower_threshold",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 3}},
        )
        # B 应命中 3 笔，A 应命中 0 笔
        assert result.variant_a.metrics["total_hits"] == 0
        assert result.variant_b.metrics["total_hits"] == 3
        # B 召回率 > A 召回率
        assert result.variant_b.metrics["recall"] > result.variant_a.metrics["recall"]
        # 推荐 B
        assert result.decision["recommendation"] == "B"
        assert result.decision["guardrail_violations"] == []


def test_run_test_b_worse():
    """B 参数更差（提高分拆最小笔数，遗漏可疑）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        # 5 笔分拆转账（真实可疑）
        txns = _smurfing_txns("SMURF", 5)
        gt = {f"SMURF_{i}": True for i in range(5)}

        # A: min_count=5（命中 5 笔）
        # B: min_count=8（5笔不够，不命中）
        result = runner.run_test(
            test_name="smurfing_raise_threshold",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 8}},
        )
        # A 命中 5 笔，B 命中 0 笔
        assert result.variant_a.metrics["total_hits"] == 5
        assert result.variant_b.metrics["total_hits"] == 0
        # B recall 下降 100% -> 戒律 P1 违反 -> 推荐 A
        assert result.decision["recommendation"] == "A"
        assert any("P1" in v for v in result.decision["guardrail_violations"])


def test_run_test_tie():
    """两变体产生相同结果（参数变化不影响命中的规则）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        # 5 笔分拆转账
        txns = _smurfing_txns("SMURF", 5)
        gt = {f"SMURF_{i}": True for i in range(5)}

        # 改变 round_trip 参数（但无对敲交易，不影响命中）
        result = runner.run_test(
            test_name="round_trip_no_effect",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"round_trip": {"max_days": 7}},
            variant_b_params={"round_trip": {"max_days": 14}},
        )
        # 两变体指标相同
        assert result.variant_a.metrics["total_hits"] == result.variant_b.metrics["total_hits"]
        assert result.variant_a.weighted_score == result.variant_b.weighted_score
        # 推荐 review（平局）
        assert result.decision["recommendation"] == "review"


# ============================================================
# 戒律守护
# ============================================================
def test_guardrail_p1_recall_drop_rejected():
    """戒律 P1: recall 下降超过 30% 拒绝推荐 B"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        txns = _smurfing_txns("SMURF", 5)
        gt = {f"SMURF_{i}": True for i in range(5)}

        result = runner.run_test(
            test_name="p1_violation",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 8}},
        )
        assert result.variant_a.metrics["recall"] == 1.0
        assert result.variant_b.metrics["recall"] == 0.0
        # recall 下降 100% > 30% -> P1 违反
        assert len(result.decision["guardrail_violations"]) >= 1
        assert result.decision["recommendation"] == "A"


def test_guardrail_p2_hits_surge_warning():
    """戒律 P2: 总命中激增超过 200% 给出警告"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        # 构造数据：A 命中少量，B 命中大量（但都是误报）
        # 用大额交易：A 阈值高（少量命中），B 阈值低（大量命中）
        txns = []
        # 1 笔大额（A、B 都命中）
        txns.append(_make_txn("LARGE_1", "A", "B", 200000, "2026-07-01T10:00:00"))
        # 5 笔中等金额（只有 B 低阈值命中）
        for i in range(5):
            txns.append(_make_txn(
                f"MID_{i}", f"C{i}", f"D{i}", 50000,
                f"2026-07-01T11:0{i}:00"
            ))
        # 全部标记为正常（非可疑）-> B 的命中都是误报
        gt = {t["transaction_id"]: False for t in txns}

        # A: threshold=100000（只命中 LARGE_1，1笔）
        # B: threshold=40000（命中全部 6 笔）
        result = runner.run_test(
            test_name="p2_surge",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"large_amount": {"threshold": 100000}},
            variant_b_params={"large_amount": {"threshold": 40000}},
        )
        # A 命中 1 笔，B 命中 6 笔 -> 激增 500% > 200%
        assert result.variant_a.metrics["total_hits"] == 1
        assert result.variant_b.metrics["total_hits"] == 6
        assert len(result.decision["guardrail_warnings"]) >= 1
        assert any("P2" in w for w in result.decision["guardrail_warnings"])


def test_no_guardrail_when_a_has_zero_recall():
    """A 的 recall 为 0 时不触发 P1（无基线可对比下降）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        # 全部正常交易，无可疑
        txns = [_make_txn("N1", "A", "B", 200000, "2026-07-01T10:00:00")]
        gt = {"N1": False}

        result = runner.run_test(
            test_name="no_recall_baseline",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"large_amount": {"threshold": 100000}},
            variant_b_params={"large_amount": {"threshold": 200000}},
        )
        # A 和 B 的 recall 都是 0（无可疑）-> 不触发 P1
        assert result.variant_a.metrics["recall"] == 0.0
        assert result.variant_b.metrics["recall"] == 0.0
        assert result.decision["guardrail_violations"] == []


# ============================================================
# 公平性
# ============================================================
def test_same_data_for_both_variants():
    """两变体使用同一交易数据（戒律 M1: 公平对比）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        txns = _smurfing_txns("SMURF", 5)
        gt = {f"SMURF_{i}": True for i in range(5)}

        result = runner.run_test(
            test_name="fairness",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 3}},
        )
        # 两变体的 tp+fn 应该相同（因为 ground_truth 相同）
        a_total = result.variant_a.metrics["tp"] + result.variant_a.metrics["fn"]
        b_total = result.variant_b.metrics["tp"] + result.variant_b.metrics["fn"]
        assert a_total == b_total == 5


# ============================================================
# 对比与指标
# ============================================================
def test_comparison_contains_all_metrics():
    """对比结果包含所有数值型指标"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        txns = _smurfing_txns("SMURF", 5)
        gt = {f"SMURF_{i}": True for i in range(5)}

        result = runner.run_test(
            test_name="comparison",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 3}},
        )
        # 至少包含 precision, recall, f1, total_hits, weighted_score
        for key in ["precision", "recall", "f1", "total_hits", "weighted_score"]:
            assert key in result.comparison, f"对比缺少指标: {key}"
            entry = result.comparison[key]
            assert "a" in entry
            assert "b" in entry
            assert "delta" in entry
            assert "relative_change" in entry


def test_comparison_delta_correctness():
    """对比 delta = b - a"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        txns = _smurfing_txns("SMURF", 3)
        gt = {f"SMURF_{i}": True for i in range(3)}

        result = runner.run_test(
            test_name="delta",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 3}},
        )
        total_hits_cmp = result.comparison["total_hits"]
        assert total_hits_cmp["delta"] == total_hits_cmp["b"] - total_hits_cmp["a"]


# ============================================================
# 持久化
# ============================================================
def test_persistence_save_and_get():
    """保存与获取测试结果"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        txns = _smurfing_txns("SMURF", 5)
        gt = {f"SMURF_{i}": True for i in range(5)}

        result = runner.run_test(
            test_name="persistence",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 3}},
        )
        # 获取
        fetched = runner.get_test(result.test_id)
        assert fetched is not None
        assert fetched.test_id == result.test_id
        assert fetched.test_name == "persistence"
        assert fetched.variant_a.name == "baseline"
        assert fetched.variant_b.name == "candidate"
        assert fetched.decision["recommendation"] == result.decision["recommendation"]


def test_persistence_list_tests():
    """列出测试结果"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        txns = _smurfing_txns("SMURF", 5)
        gt = {f"SMURF_{i}": True for i in range(5)}

        runner.run_test(
            test_name="test1",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 3}},
        )
        runner.run_test(
            test_name="test2",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 8}},
        )
        tests = runner.list_tests()
        assert len(tests) == 2
        # 包含摘要字段
        for t in tests:
            assert "test_id" in t
            assert "test_name" in t
            assert "recommendation" in t
            assert "weighted_score_a" in t
            assert "weighted_score_b" in t


def test_persistence_delete():
    """删除测试结果"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        txns = _smurfing_txns("SMURF", 5)
        gt = {f"SMURF_{i}": True for i in range(5)}

        result = runner.run_test(
            test_name="to_delete",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 3}},
        )
        assert runner.delete_test(result.test_id) is True
        assert runner.get_test(result.test_id) is None
        # 再次删除返回 False
        assert runner.delete_test(result.test_id) is False


def test_atomic_write_no_tmp_left():
    """原子写入不残留 .tmp 文件"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        txns = _smurfing_txns("SMURF", 5)
        gt = {f"SMURF_{i}": True for i in range(5)}

        runner.run_test(
            test_name="atomic",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 3}},
        )
        files = os.listdir(tmpdir)
        assert not any(f.endswith(".tmp") for f in files)


def test_get_test_unknown_returns_none():
    """获取不存在的测试返回 None"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        assert runner.get_test("ABT-NONEXIST") is None


def test_get_test_corrupted_returns_none():
    """损坏的测试结果文件返回 None"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        path = os.path.join(tmpdir, "ABT-CORRUPT.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{invalid json")
        assert runner.get_test("ABT-CORRUPT") is None


# ============================================================
# 自定义权重与元数据
# ============================================================
def test_custom_metric_weights():
    """自定义指标权重影响加权得分"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        txns = _smurfing_txns("SMURF", 3)
        gt = {f"SMURF_{i}": True for i in range(3)}

        weights = {"precision": 0.5, "recall": 0.5, "f1": 0.0}
        result = runner.run_test(
            test_name="custom_weights",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 3}},
            metric_weights=weights,
        )
        assert result.metric_weights == weights
        # B 命中 3 笔全部正确 -> precision=1, recall=1 -> weighted=1.0
        assert result.variant_b.weighted_score == 1.0


def test_metadata_persisted():
    """元数据被持久化"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        txns = _smurfing_txns("SMURF", 5)
        gt = {f"SMURF_{i}": True for i in range(5)}

        result = runner.run_test(
            test_name="with_metadata",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 3}},
            metadata={"author": "tester", "ticket": "AML-001"},
        )
        fetched = runner.get_test(result.test_id)
        assert fetched.metadata["author"] == "tester"
        assert fetched.metadata["ticket"] == "AML-001"


def test_custom_variant_names():
    """自定义变体名称"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        txns = _smurfing_txns("SMURF", 5)
        gt = {f"SMURF_{i}": True for i in range(5)}

        result = runner.run_test(
            test_name="custom_names",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 3}},
            variant_a_name="strict",
            variant_b_name="loose",
        )
        assert result.variant_a.name == "strict"
        assert result.variant_b.name == "loose"


# ============================================================
# 决策理由（戒律 M2）
# ============================================================
def test_decision_includes_reason():
    """戒律 M2: 决策附带理由"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(storage_dir=tmpdir)
        txns = _smurfing_txns("SMURF", 5)
        gt = {f"SMURF_{i}": True for i in range(5)}

        result = runner.run_test(
            test_name="reason_test",
            transactions=txns,
            ground_truth=gt,
            variant_a_params={"smurfing": {"min_count": 5}},
            variant_b_params={"smurfing": {"min_count": 3}},
        )
        assert result.decision["reason"]
        assert isinstance(result.decision["reason"], str)
        assert len(result.decision["reason"]) > 0
