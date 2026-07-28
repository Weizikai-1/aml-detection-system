"""
多维度分析对比工具单元测试

覆盖:
- compare: 多记录对比（空/单条/不存在/正常/指纹/警告）
- compare_two: 两两详细对比
- find_trend: 趋势分析（rising/falling/stable）
- find_outliers: 离群值检测
- overview: 综合统计
"""
import os
import sys
import json
import copy
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.history_manager import HistoryManager
from tools.analysis_comparator import AnalysisComparator


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def history_mgr(tmp_path):
    """使用临时目录的 HistoryManager"""
    return HistoryManager(history_dir=str(tmp_path))


@pytest.fixture
def comparator(history_mgr):
    """使用临时历史管理器的对比器"""
    return AnalysisComparator(history_mgr)


def _make_state(
    execution_id: str,
    transactions_count: int = 100,
    rule_hit_count: int = 5,
    llm_confirmed_count: int = 3,
    report_count: int = 2,
    duration: float = 10.0,
    risk_distribution: dict = None,
    rule_details: dict = None,
    txns_hash: str = "abc123",
    interrupted: bool = False,
    error: str = "",
):
    """构造工作流最终状态"""
    transactions = [
        {"transaction_id": f"T{i}", "amount": 1000.0 + i}
        for i in range(transactions_count)
    ]
    if risk_distribution is None:
        risk_distribution = {"critical": 1, "high": 1, "medium": 0, "low": 0}
    if rule_details is None:
        rule_details = {"分拆转账": 3, "大额交易": 2}

    return {
        "execution_id": execution_id,
        "transactions": transactions,
        "analysis_date": "2026-07-27",
        "total_processing_time": duration,
        "rule_hit_count": rule_hit_count,
        "rule_details": rule_details,
        "llm_confirmed": [f"LLM_{i}" for i in range(llm_confirmed_count)],
        "str_reports": [
            {"risk_level": lvl, "primary_account": f"ACC_{i}"}
            for i, (lvl, n) in enumerate(risk_distribution.items())
            for _ in range(n)
        ],
        "report_count": report_count,
        "risk_distribution": risk_distribution,
        "interrupted": interrupted,
        "error": error,
    }


# ============================================================
# compare 测试
# ============================================================
class TestCompare:
    def test_empty_ids_returns_warning(self, comparator):
        """空ID列表应返回警告"""
        result = comparator.compare([])
        assert result["records"] == []
        assert len(result["warnings"]) > 0
        assert "未提供" in result["warnings"][0]

    def test_single_id_returns_warning(self, comparator, history_mgr):
        """单个ID无法对比"""
        history_mgr.save_run(_make_state("exec_1"))
        result = comparator.compare(["exec_1"])
        assert result["records"] == []
        assert any("至少需要2个" in w for w in result["warnings"])

    def test_nonexistent_id_returns_warning(self, comparator):
        """不存在的ID应提示"""
        result = comparator.compare(["ghost1", "ghost2"])
        assert result["records"] == []
        assert any("不存在" in w for w in result["warnings"])

    def test_compare_two_real_records(self, comparator, history_mgr):
        """对比两个真实记录"""
        history_mgr.save_run(_make_state(
            "exec_a", transactions_count=100, rule_hit_count=5, duration=10.0,
        ))
        history_mgr.save_run(_make_state(
            "exec_b", transactions_count=200, rule_hit_count=8, duration=15.0,
        ))

        result = comparator.compare(["exec_a", "exec_b"])
        assert len(result["records"]) == 2
        assert "metrics" in result
        assert "risk_distribution" in result
        assert "rule_details" in result
        assert "data_fingerprints" in result

        # 交易笔数指标
        txn_metric = result["metrics"]["transactions_count"]
        assert txn_metric["values"] == [100, 200]
        assert txn_metric["min"] == 100
        assert txn_metric["max"] == 200
        assert txn_metric["delta"] == 100

    def test_compare_three_records(self, comparator, history_mgr):
        """对比三个记录"""
        for i, n in enumerate([100, 150, 200]):
            history_mgr.save_run(_make_state(
                f"exec_{i}", transactions_count=n, rule_hit_count=n // 10,
            ))

        result = comparator.compare(["exec_0", "exec_1", "exec_2"])
        assert len(result["records"]) == 3
        assert len(result["metrics"]["transactions_count"]["values"]) == 3

    def test_data_fingerprint_same_dataset(self, comparator, history_mgr):
        """相同数据指纹的记录被识别为同一数据集"""
        # 用相同的交易数据生成两次记录
        state1 = _make_state("exec_1", txns_hash="hash_xyz")
        state2 = _make_state("exec_2", txns_hash="hash_xyz")
        # 强制设置 hash（save_run会重新计算，所以这里直接通过修改保存的文件来测试）
        history_mgr.save_run(state1)
        history_mgr.save_run(state2)

        # 直接修改保存的记录
        for eid in ["exec_1", "exec_2"]:
            path = os.path.join(history_mgr.history_dir, f"{eid}.json")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["transactions_hash"] = "hash_xyz"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        result = comparator.compare(["exec_1", "exec_2"])
        assert result["data_fingerprints"]["is_same_dataset"] is True
        assert result["data_fingerprints"]["all_same"] is True

    def test_data_fingerprint_different_dataset(self, comparator, history_mgr):
        """不同数据指纹的记录被识别为不同数据集"""
        history_mgr.save_run(_make_state("exec_1", transactions_count=100))
        history_mgr.save_run(_make_state("exec_2", transactions_count=200))

        # 修改 hash
        for eid, h in [("exec_1", "hash_a"), ("exec_2", "hash_b")]:
            path = os.path.join(history_mgr.history_dir, f"{eid}.json")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["transactions_hash"] = h
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        result = comparator.compare(["exec_1", "exec_2"])
        assert result["data_fingerprints"]["is_same_dataset"] is False
        # 不同数据集应产生警告
        assert any("不同" in w for w in result["warnings"])

    def test_risk_distribution_comparison(self, comparator, history_mgr):
        """风险分布对比正确"""
        history_mgr.save_run(_make_state(
            "exec_1",
            risk_distribution={"critical": 2, "high": 3, "medium": 1, "low": 0},
        ))
        history_mgr.save_run(_make_state(
            "exec_2",
            risk_distribution={"critical": 1, "high": 1, "medium": 2, "low": 1},
        ))

        result = comparator.compare(["exec_1", "exec_2"])
        assert result["risk_distribution"]["critical"]["values"] == [2, 1]
        assert result["risk_distribution"]["high"]["values"] == [3, 1]

    def test_rule_details_comparison(self, comparator, history_mgr):
        """各规则命中数对比"""
        history_mgr.save_run(_make_state(
            "exec_1",
            rule_details={"分拆转账": 3, "大额交易": 2, "对敲交易": 1},
        ))
        history_mgr.save_run(_make_state(
            "exec_2",
            rule_details={"分拆转账": 5, "大额交易": 1, "快进快出": 2},
        ))

        result = comparator.compare(["exec_1", "exec_2"])
        assert "分拆转账" in result["rule_details"]
        assert result["rule_details"]["分拆转账"]["values"] == [3, 5]
        assert result["rule_details"]["分拆转账"]["delta"] == 2
        # 只 exec_2 命中的规则
        assert "快进快出" in result["rule_details"]

    def test_interrupted_record_warning(self, comparator, history_mgr):
        """中断的记录应产生警告"""
        history_mgr.save_run(_make_state("exec_1", interrupted=False))
        history_mgr.save_run(_make_state("exec_2", interrupted=True))

        result = comparator.compare(["exec_1", "exec_2"])
        assert any("中断" in w for w in result["warnings"])

    def test_error_record_warning(self, comparator, history_mgr):
        """有错误的记录应产生警告"""
        history_mgr.save_run(_make_state("exec_1", error=""))
        history_mgr.save_run(_make_state("exec_2", error="LLM 调用失败"))

        result = comparator.compare(["exec_1", "exec_2"])
        assert any("LLM 调用失败" in w for w in result["warnings"])

    def test_high_risk_drop_warning(self, comparator, history_mgr):
        """高风险报告数从N降至0应警告（戒律 P1）"""
        history_mgr.save_run(_make_state(
            "exec_1",
            risk_distribution={"critical": 2, "high": 1, "medium": 0, "low": 0},
        ))
        history_mgr.save_run(_make_state(
            "exec_2",
            risk_distribution={"critical": 0, "high": 0, "medium": 3, "low": 0},
        ))

        result = comparator.compare(["exec_1", "exec_2"])
        # 后续高风险为0但之前不为0
        assert any("P1" in w or "高风险报告数" in w for w in result["warnings"])


# ============================================================
# compare_two 测试
# ============================================================
class TestCompareTwo:
    def test_nonexistent_a(self, comparator):
        """A不存在应返回错误"""
        result = comparator.compare_two("ghost", "ghost2")
        assert "error" in result

    def test_nonexistent_b(self, comparator, history_mgr):
        """B不存在应返回错误"""
        history_mgr.save_run(_make_state("exec_1"))
        result = comparator.compare_two("exec_1", "ghost")
        assert "error" in result

    def test_normal_comparison(self, comparator, history_mgr):
        """正常两两对比"""
        history_mgr.save_run(_make_state(
            "exec_a", transactions_count=100, rule_hit_count=5, duration=10.0,
        ))
        history_mgr.save_run(_make_state(
            "exec_b", transactions_count=120, rule_hit_count=8, duration=12.0,
        ))

        result = comparator.compare_two("exec_a", "exec_b")
        assert "record_a" in result
        assert "record_b" in result
        assert "metric_diffs" in result
        assert "risk_diff" in result
        assert "rule_diff" in result
        assert "summary" in result

        # 交易笔数差异
        txn_diff = result["metric_diffs"]["transactions_count"]
        assert txn_diff["value_a"] == 100
        assert txn_diff["value_b"] == 120
        assert txn_diff["diff"] == 20

    def test_same_dataset_detection(self, comparator, history_mgr):
        """相同数据集检测"""
        history_mgr.save_run(_make_state("exec_a"))
        history_mgr.save_run(_make_state("exec_b"))

        # 用相同 hash 覆盖
        for eid in ["exec_a", "exec_b"]:
            path = os.path.join(history_mgr.history_dir, f"{eid}.json")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["transactions_hash"] = "same_hash"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        result = comparator.compare_two("exec_a", "exec_b")
        assert result["is_same_dataset"] is True
        assert "相同数据集" in result["summary"]

    def test_different_dataset_summary(self, comparator, history_mgr):
        """不同数据集的摘要应体现"""
        history_mgr.save_run(_make_state("exec_a", transactions_count=100))
        history_mgr.save_run(_make_state("exec_b", transactions_count=200))

        result = comparator.compare_two("exec_a", "exec_b")
        assert result["is_same_dataset"] is False
        assert "不同数据集" in result["summary"]

    def test_rule_diff(self, comparator, history_mgr):
        """规则命中差异"""
        history_mgr.save_run(_make_state(
            "exec_a", rule_details={"分拆转账": 3, "大额交易": 2},
        ))
        history_mgr.save_run(_make_state(
            "exec_b", rule_details={"分拆转账": 5, "对敲交易": 1},
        ))

        result = comparator.compare_two("exec_a", "exec_b")
        assert result["rule_diff"]["分拆转账"]["value_a"] == 3
        assert result["rule_diff"]["分拆转账"]["value_b"] == 5
        assert result["rule_diff"]["分拆转账"]["diff"] == 2
        # 只 B 有的规则
        assert "对敲交易" in result["rule_diff"]


# ============================================================
# find_trend 测试
# ============================================================
class TestFindTrend:
    def test_unknown_metric_raises(self, comparator):
        """未知指标应报错"""
        with pytest.raises(ValueError, match="未知指标"):
            comparator.find_trend("unknown_metric")

    def test_empty_history(self, comparator):
        """空历史返回稳定趋势"""
        result = comparator.find_trend("rule_hit_count")
        assert result["trend"] == "stable"
        assert result["data_points"] == []
        assert result["avg"] == 0

    def test_rising_trend(self, comparator, history_mgr):
        """上升趋势"""
        for i, n in enumerate([10, 20, 30, 40, 50]):
            history_mgr.save_run(_make_state(
                f"exec_{i}", rule_hit_count=n,
            ))

        result = comparator.find_trend("rule_hit_count")
        assert result["trend"] == "rising"
        assert len(result["data_points"]) == 5
        assert result["min"] == 10
        assert result["max"] == 50

    def test_falling_trend(self, comparator, history_mgr):
        """下降趋势"""
        for i, n in enumerate([50, 40, 30, 20, 10]):
            history_mgr.save_run(_make_state(
                f"exec_{i}", rule_hit_count=n,
            ))

        result = comparator.find_trend("rule_hit_count")
        assert result["trend"] == "falling"

    def test_stable_trend(self, comparator, history_mgr):
        """稳定趋势"""
        for i in range(5):
            history_mgr.save_run(_make_state(
                f"exec_{i}", rule_hit_count=20,
            ))

        result = comparator.find_trend("rule_hit_count")
        assert result["trend"] == "stable"

    def test_composite_metric_trend(self, comparator, history_mgr):
        """复合指标趋势（高风险报告数）"""
        for i, n in enumerate([1, 2, 3, 4, 5]):
            history_mgr.save_run(_make_state(
                f"exec_{i}",
                risk_distribution={"critical": n, "high": 0, "medium": 0, "low": 0},
            ))

        result = comparator.find_trend("high_risk_reports")
        assert result["trend"] == "rising"
        # 高风险 = critical + high
        assert result["data_points"][0][1] == 1
        assert result["data_points"][-1][1] == 5

    def test_data_points_chronological_order(self, comparator, history_mgr):
        """数据点应按时间正序"""
        for i in range(5):
            history_mgr.save_run(_make_state(
                f"exec_{i}", rule_hit_count=i * 10,
            ))

        result = comparator.find_trend("rule_hit_count")
        # 第一个点应该是最早的（rule_hit_count=0）
        assert result["data_points"][0][1] == 0
        # 最后一个点应该是最近的（rule_hit_count=40）
        assert result["data_points"][-1][1] == 40


# ============================================================
# find_outliers 测试
# ============================================================
class TestFindOutliers:
    def test_unknown_metric_raises(self, comparator):
        """未知指标应报错"""
        with pytest.raises(ValueError, match="未知指标"):
            comparator.find_outliers("unknown_metric")

    def test_insufficient_data(self, comparator, history_mgr):
        """数据点不足应返回空离群列表"""
        history_mgr.save_run(_make_state("exec_1", duration=10.0))
        history_mgr.save_run(_make_state("exec_2", duration=12.0))

        result = comparator.find_outliers("duration_seconds")
        assert result["outliers"] == []
        assert "数据点不足" in result.get("message", "")

    def test_detect_outlier(self, comparator, history_mgr):
        """检测离群值"""
        # 正常值都在 10 左右，一个异常值为 1000
        for i, dur in enumerate([10, 11, 10, 12, 10, 1000, 11, 10]):
            history_mgr.save_run(_make_state(
                f"exec_{i}", duration=float(dur),
            ))

        result = comparator.find_outliers("duration_seconds")
        assert len(result["outliers"]) >= 1
        # 离群值应该包含 1000 那个
        outlier_values = [o["value"] for o in result["outliers"]]
        assert 1000 in outlier_values
        assert result["mean"] < 200  # 均值不会被离群值拉得太高

    def test_no_outliers_when_all_similar(self, comparator, history_mgr):
        """所有值相近时不应有离群值"""
        for i in range(5):
            history_mgr.save_run(_make_state(
                f"exec_{i}", duration=10.0 + i * 0.1,
            ))

        result = comparator.find_outliers("duration_seconds")
        assert result["outliers"] == []

    def test_outlier_z_score(self, comparator, history_mgr):
        """离群值有 z_score 字段"""
        for i, dur in enumerate([10, 10, 10, 1000, 10]):
            history_mgr.save_run(_make_state(
                f"exec_{i}", duration=float(dur),
            ))

        result = comparator.find_outliers("duration_seconds")
        if result["outliers"]:
            assert "z_score" in result["outliers"][0]
            assert result["outliers"][0]["z_score"] >= 2.0

    def test_outlier_deviation(self, comparator, history_mgr):
        """离群值有 deviation 字段（偏离均值）"""
        for i, dur in enumerate([10, 10, 10, 100, 10]):
            history_mgr.save_run(_make_state(
                f"exec_{i}", duration=float(dur),
            ))

        result = comparator.find_outliers("duration_seconds")
        for o in result["outliers"]:
            assert "deviation" in o
            # 偏离值应该等于 value - mean
            assert o["deviation"] == o["value"] - result["mean"]


# ============================================================
# overview 测试
# ============================================================
class TestOverview:
    def test_empty_history(self, comparator):
        """空历史"""
        result = comparator.overview()
        assert result["total_runs"] == 0
        assert result["date_range"] == []
        assert result["metric_stats"] == {}

    def test_normal_overview(self, comparator, history_mgr):
        """正常综合统计"""
        for i, n in enumerate([100, 150, 200]):
            history_mgr.save_run(_make_state(
                f"exec_{i}", transactions_count=n, rule_hit_count=n // 10,
            ))

        result = comparator.overview()
        assert result["total_runs"] == 3
        assert len(result["date_range"]) == 2
        # 各指标都有统计
        assert "transactions_count" in result["metric_stats"]
        assert "rule_hit_count" in result["metric_stats"]
        # 各指标都有趋势
        assert "transactions_count" in result["trends"]

    def test_overview_metric_stats(self, comparator, history_mgr):
        """指标统计正确"""
        for i, n in enumerate([10, 20, 30]):
            history_mgr.save_run(_make_state(
                f"exec_{i}", rule_hit_count=n,
            ))

        result = comparator.overview()
        stat = result["metric_stats"]["rule_hit_count"]
        assert stat["min"] == 10
        assert stat["max"] == 30
        assert stat["avg"] == 20
        assert stat["last"] == 30  # 最后一次运行


# ============================================================
# 端到端流程
# ============================================================
class TestEndToEnd:
    def test_full_workflow(self, comparator, history_mgr):
        """完整对比流程"""
        # 1. 保存多次运行
        history_mgr.save_run(_make_state(
            "v1", transactions_count=100, rule_hit_count=5, duration=10.0,
        ))
        history_mgr.save_run(_make_state(
            "v2", transactions_count=100, rule_hit_count=8, duration=12.0,
        ))
        history_mgr.save_run(_make_state(
            "v3", transactions_count=100, rule_hit_count=10, duration=15.0,
        ))

        # 2. 列出运行
        runs = history_mgr.list_runs()
        assert len(runs) == 3

        # 3. 多记录对比
        comparison = comparator.compare(["v1", "v2", "v3"])
        assert len(comparison["records"]) == 3

        # 4. 两两对比
        diff = comparator.compare_two("v1", "v3")
        assert diff["metric_diffs"]["rule_hit_count"]["diff"] == 5

        # 5. 趋势分析
        trend = comparator.find_trend("rule_hit_count")
        assert trend["trend"] == "rising"

        # 6. 离群值检测（数据点不足）
        outliers = comparator.find_outliers("duration_seconds")
        # 只有3条记录，数据点不足
        assert outliers["outliers"] == []

        # 7. 综合统计
        overview = comparator.overview()
        assert overview["total_runs"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
