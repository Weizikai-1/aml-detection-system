"""
交叉影响分析器单元测试

覆盖:
- ParamChange / RuleImpact / CrossImpactResult 数据结构与序列化
- analyze 基本流程
- 直接影响检测（变更参数所属规则的命中变化）
- 交叉影响检测（其他规则的命中变化）
- 强影响识别
- 影响矩阵构建
- 持久化（save / get / list / delete）
- 损坏文件恢复
- 边界条件
"""
import copy
import json
import os
import tempfile

import pytest

from tools.cross_impact_analyzer import (
    CrossImpactAnalyzer,
    CrossImpactResult,
    ParamChange,
    RuleImpact,
    STRONG_IMPACT_ABSOLUTE_THRESHOLD,
    STRONG_IMPACT_RELATIVE_THRESHOLD,
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


def _smurfing_txns(count: int, receiver: str = "RECV_A") -> list:
    """count 笔分拆转账（4.5万，同一收款人，不同付款人，1小时内）"""
    return [
        _make_txn(
            f"SMURF_{i}", f"PAYER_{i}", receiver, 45000.0,
            f"2026-07-01T10:{i:02d}:00"
        )
        for i in range(count)
    ]


def _baseline_params() -> dict:
    """基线参数（与 RuleTuner 默认一致）"""
    return {
        "smurfing": {"min_count": 5, "hour_window": 1, "amount_low": 40000, "amount_high": 50000, "risk_score": 70},
        "fast_in_fast_out": {"max_minutes": 10, "min_ratio": 0.95, "min_amount": 10000, "risk_score_primary": 60, "risk_score_secondary": 50},
        "round_trip": {"max_days": 7, "max_amount_diff_ratio": 0.2, "min_amount": 10000, "risk_score": 65},
        "large_amount": {"threshold": 100000, "risk_score": 40},
    }


# ============================================================
# ParamChange
# ============================================================
def test_param_change_key():
    """参数变更 key 唯一标识"""
    pc = ParamChange("smurfing", "min_count", 5, 3)
    assert pc.key == "smurfing.min_count"


def test_param_change_description():
    """参数变更描述"""
    pc = ParamChange("large_amount", "threshold", 100000, 50000)
    assert "100000" in pc.description
    assert "50000" in pc.description


def test_param_change_roundtrip():
    """参数变更序列化与反序列化"""
    pc = ParamChange("smurfing", "min_count", 5, 3)
    d = pc.to_dict()
    pc2 = ParamChange.from_dict(d)
    assert pc2.group == "smurfing"
    assert pc2.param == "min_count"
    assert pc2.old_value == 5
    assert pc2.new_value == 3


# ============================================================
# RuleImpact
# ============================================================
def test_rule_impact_delta():
    """命中数变化计算"""
    ri = RuleImpact("分拆转账", baseline_hits=5, modified_hits=3)
    assert ri.delta == -2
    assert ri.relative_change == -0.4


def test_rule_impact_baseline_zero():
    """基线为 0 时有新增命中"""
    ri = RuleImpact("大额交易", baseline_hits=0, modified_hits=2)
    assert ri.delta == 2
    assert ri.relative_change == 1.0


def test_rule_impact_baseline_zero_no_change():
    """基线为 0 且无变化"""
    ri = RuleImpact("大额交易", baseline_hits=0, modified_hits=0)
    assert ri.delta == 0
    assert ri.relative_change == 0.0


def test_rule_impact_is_strong_absolute():
    """强影响判定（绝对变化）"""
    ri = RuleImpact("分拆转账", 5, 3)
    assert abs(ri.delta) >= STRONG_IMPACT_ABSOLUTE_THRESHOLD
    assert ri.is_strong is True


def test_rule_impact_not_strong():
    """非强影响（无变化）"""
    ri = RuleImpact("分拆转账", 5, 5)
    assert ri.delta == 0
    assert ri.is_strong is False


# ============================================================
# CrossImpactResult 序列化
# ============================================================
def test_result_roundtrip():
    """结果序列化与反序列化"""
    pc = ParamChange("smurfing", "min_count", 5, 3)
    ri = RuleImpact("分拆转账", 5, 3)
    result = CrossImpactResult(
        analysis_id="CIA-TEST01",
        timestamp="2026-07-28T10:00:00",
        baseline_params=_baseline_params(),
        param_changes=[pc],
        impacts={"smurfing.min_count": {"分拆转账": ri}},
        strong_impacts=[],
    )
    d = result.to_dict()
    result2 = CrossImpactResult.from_dict(d)
    assert result2.analysis_id == "CIA-TEST01"
    assert len(result2.param_changes) == 1
    assert result2.param_changes[0].key == "smurfing.min_count"
    assert "分拆转账" in result2.impacts["smurfing.min_count"]
    assert result2.impacts["smurfing.min_count"]["分拆转账"].delta == -2


# ============================================================
# analyze 基本流程
# ============================================================
def test_analyze_direct_impact():
    """直接影响：变更 smurfing.min_count 影响分拆转账命中"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        # 5 笔分拆转账
        txns = _smurfing_txns(5)
        baseline = _baseline_params()

        result = analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[
                ParamChange("smurfing", "min_count", 5, 8),
            ],
        )
        # 分拆转账: min_count=5 命中 5 笔，min_count=8 命中 0 笔
        impact = result.impacts["smurfing.min_count"]["分拆转账"]
        assert impact.baseline_hits == 5
        assert impact.modified_hits == 0
        assert impact.delta == -5


def test_analyze_no_cross_impact_independent_rules():
    """规则独立时无交叉影响：变更 large_amount 不影响分拆转账"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        # 5 笔分拆转账（45000，不触发大额）
        txns = _smurfing_txns(5)
        baseline = _baseline_params()

        result = analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[
                ParamChange("large_amount", "threshold", 100000, 50000),
            ],
        )
        # large_amount 变更不影响分拆转账（规则独立）
        smurfing_impact = result.impacts["large_amount.threshold"].get("分拆转账")
        assert smurfing_impact is not None
        assert smurfing_impact.delta == 0


def test_analyze_multiple_param_changes():
    """多个参数变更同时分析"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        txns = _smurfing_txns(5)
        # 加一笔大额
        txns.append(_make_txn("LARGE", "A", "B", 200000, "2026-07-01T12:00:00"))
        baseline = _baseline_params()

        result = analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[
                ParamChange("smurfing", "min_count", 5, 8),
                ParamChange("large_amount", "threshold", 100000, 50000),
            ],
        )
        # 两个变更都有结果
        assert "smurfing.min_count" in result.impacts
        assert "large_amount.threshold" in result.impacts
        # smurfing 变更: 分拆转账命中减少
        assert result.impacts["smurfing.min_count"]["分拆转账"].delta == -5
        # large_amount 变更: 大额交易命中不变（LARGE 200000 仍然 > 50000）
        assert result.impacts["large_amount.threshold"]["大额交易"].delta == 0


def test_analyze_empty_param_changes_rejected():
    """空参数变更列表拒绝分析"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        with pytest.raises(ValueError, match="不能为空"):
            analyzer.analyze(
                transactions=[],
                baseline_params={},
                param_changes=[],
            )


# ============================================================
# 强影响识别
# ============================================================
def test_strong_impact_detected():
    """强影响被识别"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        txns = _smurfing_txns(5)
        baseline = _baseline_params()

        result = analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[
                ParamChange("smurfing", "min_count", 5, 8),
            ],
        )
        # 分拆转账命中从 5 降到 0 是强影响
        assert len(result.strong_impacts) >= 1
        si = result.strong_impacts[0]
        assert si["rule_name"] == "分拆转账"
        assert si["delta"] == -5
        assert si["direction"] == "减少"
        assert "P1" in si["guardrail_note"]  # 命中减少 -> 戒律 P1


def test_strong_impact_increase_guardrail():
    """命中增加的强影响标注戒律 P2"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        # 3 笔分拆（min_count=5 不命中，min_count=3 命中）
        txns = _smurfing_txns(3)
        baseline = _baseline_params()

        result = analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[
                ParamChange("smurfing", "min_count", 5, 3),
            ],
        )
        # 分拆转账命中从 0 升到 3 是强影响
        assert len(result.strong_impacts) >= 1
        si = next(s for s in result.strong_impacts if s["rule_name"] == "分拆转账")
        assert si["delta"] == 3
        assert si["direction"] == "增加"
        assert "P2" in si["guardrail_note"]


def test_no_strong_impact_when_no_change():
    """无变化时不产生强影响"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        txns = _smurfing_txns(5)
        baseline = _baseline_params()

        # 变更 round_trip 参数（无对敲交易，无任何命中变化）
        result = analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[
                ParamChange("round_trip", "max_days", 7, 14),
            ],
        )
        # 无强影响（round_trip 无命中，变更不影响其他规则）
        strong_for_round_trip = [
            s for s in result.strong_impacts
            if s["param_change"] == "round_trip.max_days"
        ]
        assert len(strong_for_round_trip) == 0


# ============================================================
# 交叉影响标识
# ============================================================
def test_is_cross_impact_flag():
    """is_cross_impact 标识正确"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        txns = _smurfing_txns(5)
        baseline = _baseline_params()

        result = analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[
                ParamChange("smurfing", "min_count", 5, 8),
            ],
        )
        # 分拆转账是 smurfing 的直接规则 -> is_cross_impact=False
        for si in result.strong_impacts:
            if si["rule_name"] == "分拆转账":
                assert si["is_cross_impact"] is False


def test_is_cross_impact_for_other_rule():
    """变更 smurfing 参数时大额交易视为交叉影响"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        txns = _smurfing_txns(5)
        baseline = _baseline_params()

        result = analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[
                ParamChange("smurfing", "min_count", 5, 8),
            ],
        )
        # 大额交易是 smurfing 变更的交叉规则
        # 但大额交易命中为 0 且不变 -> 不会出现在 strong_impacts
        # 检查 impacts 中的标识
        large_impact = result.impacts["smurfing.min_count"].get("大额交易")
        if large_impact:
            assert analyzer._is_cross_impact("smurfing", "大额交易") is True


# ============================================================
# 影响矩阵
# ============================================================
def test_build_impact_matrix():
    """构建影响矩阵"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        txns = _smurfing_txns(5)
        baseline = _baseline_params()

        result = analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[
                ParamChange("smurfing", "min_count", 5, 8),
                ParamChange("large_amount", "threshold", 100000, 50000),
            ],
        )
        matrix = analyzer.build_impact_matrix(result)
        assert "smurfing.min_count" in matrix
        assert "large_amount.threshold" in matrix
        # smurfing 变更导致分拆转账 -5
        assert matrix["smurfing.min_count"]["分拆转账"] == -5


# ============================================================
# 持久化
# ============================================================
def test_persistence_save_and_get():
    """保存与获取分析结果"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        txns = _smurfing_txns(5)
        baseline = _baseline_params()

        result = analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[
                ParamChange("smurfing", "min_count", 5, 8),
            ],
        )
        fetched = analyzer.get_analysis(result.analysis_id)
        assert fetched is not None
        assert fetched.analysis_id == result.analysis_id
        assert len(fetched.param_changes) == 1
        assert "smurfing.min_count" in fetched.impacts


def test_persistence_list_analyses():
    """列出分析结果"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        txns = _smurfing_txns(5)
        baseline = _baseline_params()

        analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[ParamChange("smurfing", "min_count", 5, 8)],
        )
        analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[ParamChange("large_amount", "threshold", 100000, 50000)],
        )
        analyses = analyzer.list_analyses()
        assert len(analyses) == 2
        for a in analyses:
            assert "analysis_id" in a
            assert "timestamp" in a
            assert "param_change_count" in a
            assert "strong_impact_count" in a


def test_persistence_delete():
    """删除分析结果"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        txns = _smurfing_txns(5)
        baseline = _baseline_params()

        result = analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[ParamChange("smurfing", "min_count", 5, 8)],
        )
        assert analyzer.delete_analysis(result.analysis_id) is True
        assert analyzer.get_analysis(result.analysis_id) is None
        assert analyzer.delete_analysis(result.analysis_id) is False


def test_atomic_write_no_tmp_left():
    """原子写入不残留 .tmp 文件"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        txns = _smurfing_txns(5)
        baseline = _baseline_params()

        analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[ParamChange("smurfing", "min_count", 5, 8)],
        )
        files = os.listdir(tmpdir)
        assert not any(f.endswith(".tmp") for f in files)


def test_get_analysis_unknown_returns_none():
    """获取不存在的分析返回 None"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        assert analyzer.get_analysis("CIA-NONEXIST") is None


def test_get_analysis_corrupted_returns_none():
    """损坏的分析结果文件返回 None"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        path = os.path.join(tmpdir, "CIA-CORRUPT.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{invalid json")
        assert analyzer.get_analysis("CIA-CORRUPT") is None


# ============================================================
# 基线参数不被修改（戒律 P4）
# ============================================================
def test_baseline_params_not_mutated():
    """分析过程不修改基线参数"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        txns = _smurfing_txns(5)
        baseline = _baseline_params()
        baseline_copy = copy.deepcopy(baseline)

        analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[
                ParamChange("smurfing", "min_count", 5, 8),
                ParamChange("large_amount", "threshold", 100000, 50000),
            ],
        )
        assert baseline == baseline_copy


# ============================================================
# 元数据持久化
# ============================================================
def test_metadata_persisted():
    """元数据被持久化"""
    with tempfile.TemporaryDirectory() as tmpdir:
        analyzer = CrossImpactAnalyzer(storage_dir=tmpdir)
        txns = _smurfing_txns(5)
        baseline = _baseline_params()

        result = analyzer.analyze(
            transactions=txns,
            baseline_params=baseline,
            param_changes=[ParamChange("smurfing", "min_count", 5, 8)],
            metadata={"author": "analyst", "purpose": "调参前安全检查"},
        )
        fetched = analyzer.get_analysis(result.analysis_id)
        assert fetched.metadata["author"] == "analyst"
        assert fetched.metadata["purpose"] == "调参前安全检查"
