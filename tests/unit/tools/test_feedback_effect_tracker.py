"""
反馈效果追踪报告测试（阶段二-2.4）

覆盖:
- 快照记录（保存/列表/时间过滤）
- 报告生成（指标对比/改进评估/趋势分析）
- 反馈统计集成
- 报告查询
- 戒律验证（M1/M2/M4/P1/P2/P4）
"""
import os
import json
import time

import pytest

from tools.feedback_effect_tracker import FeedbackEffectTracker
from tools.feedback_manager import FeedbackManager


# ============================================================
# 夹具
# ============================================================
@pytest.fixture()
def tracker(tmp_path):
    """临时效果追踪器"""
    return FeedbackEffectTracker(feedback_dir=str(tmp_path))


@pytest.fixture()
def fm(tmp_path):
    """临时反馈管理器"""
    fb_dir = tmp_path / "feedback"
    fb_dir.mkdir()
    return FeedbackManager(feedback_dir=str(fb_dir))


# ============================================================
# 快照记录测试
# ============================================================
@pytest.mark.unit
def test_record_snapshot_returns_id(tracker):
    """记录快照应返回 snapshot_id"""
    sid = tracker.record_snapshot({"accuracy_rate": 0.8}, description="初始快照")
    assert sid.startswith("S-")
    assert len(sid) == 10  # S- + 8 hex


@pytest.mark.unit
def test_record_snapshot_persists(tracker):
    """快照应持久化到文件"""
    tracker.record_snapshot({"accuracy_rate": 0.8})
    assert os.path.exists(tracker.snapshots_path)
    snapshots = tracker._load_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0]["metrics"]["accuracy_rate"] == 0.8


@pytest.mark.unit
def test_record_snapshot_atomic_write(tracker):
    """快照写入后不应残留临时文件（戒律 P4）"""
    tracker.record_snapshot({"accuracy_rate": 0.8})
    assert not os.path.exists(tracker.snapshots_path + ".tmp")


@pytest.mark.unit
def test_list_snapshots_empty(tracker):
    """空追踪器应返回空列表"""
    assert tracker.list_snapshots() == []


@pytest.mark.unit
def test_list_snapshots_time_filter(tracker):
    """快照应按时间过滤"""
    start_ts = time.time()
    tracker.record_snapshot({"accuracy_rate": 0.7})
    time.sleep(0.05)  # 确保时间间隔足够大
    mid_ts = time.time()
    time.sleep(0.05)
    tracker.record_snapshot({"accuracy_rate": 0.8})
    time.sleep(0.05)
    end_ts = time.time()

    # 全部
    assert len(tracker.list_snapshots()) == 2
    # 仅第一个（end_ts=mid_ts 排除第二个）
    before_mid = tracker.list_snapshots(end_ts=mid_ts)
    assert len(before_mid) == 1
    # 仅第二个（start_ts=mid_ts 排除第一个）
    after_mid = tracker.list_snapshots(start_ts=mid_ts)
    assert len(after_mid) == 1


# ============================================================
# 报告生成测试
# ============================================================
@pytest.mark.unit
def test_generate_report_no_snapshots(tracker):
    """无快照时应生成空报告"""
    report = tracker.generate_report()
    assert report["snapshot_count"] == 0
    assert "无法生成" in report["summary"]
    assert report["metrics_comparison"] is None


@pytest.mark.unit
def test_generate_report_with_improvement(tracker):
    """改进场景：误报率下降+准确率上升"""
    tracker.record_snapshot({
        "false_positive_rate": 0.35,
        "false_negative_rate": 0.12,
        "accuracy_rate": 0.78,
    })
    time.sleep(0.01)
    tracker.record_snapshot({
        "false_positive_rate": 0.20,  # 下降（改进）
        "false_negative_rate": 0.12,  # 不变
        "accuracy_rate": 0.88,  # 上升（改进）
    })

    report = tracker.generate_report()
    assert report["snapshot_count"] == 2
    improvement = report["improvement"]
    assert improvement["net_improvement"] > 0
    assert any("误报率" in i for i in improvement["improvements"])
    assert any("准确率" in i for i in improvement["improvements"])


@pytest.mark.unit
def test_generate_report_with_regression(tracker):
    """恶化场景：误报率上升+漏报率上升"""
    tracker.record_snapshot({
        "false_positive_rate": 0.20,
        "false_negative_rate": 0.10,
        "accuracy_rate": 0.85,
    })
    time.sleep(0.01)
    tracker.record_snapshot({
        "false_positive_rate": 0.30,  # 上升（恶化，戒律 P2）
        "false_negative_rate": 0.15,  # 上升（恶化，戒律 P1）
        "accuracy_rate": 0.85,
    })

    report = tracker.generate_report()
    improvement = report["improvement"]
    assert improvement["net_improvement"] < 0
    assert any("误报率" in r and "恶化" in r for r in improvement["regressions"])
    assert any("漏报率" in r and "恶化" in r for r in improvement["regressions"])


@pytest.mark.unit
def test_generate_report_metrics_comparison(tracker):
    """报告应包含指标对比"""
    tracker.record_snapshot({"accuracy_rate": 0.70})
    time.sleep(0.01)
    tracker.record_snapshot({"accuracy_rate": 0.85})

    report = tracker.generate_report()
    comparison = report["metrics_comparison"]
    assert "accuracy_rate" in comparison
    assert comparison["accuracy_rate"]["before"] == 0.70
    assert comparison["accuracy_rate"]["after"] == 0.85
    assert comparison["accuracy_rate"]["delta"] == pytest.approx(0.15, abs=0.01)


@pytest.mark.unit
def test_generate_report_trend(tracker):
    """报告应包含趋势分析"""
    for acc in [0.7, 0.75, 0.82, 0.88]:
        tracker.record_snapshot({"accuracy_rate": acc})
        time.sleep(0.01)

    report = tracker.generate_report()
    trend = report["trend"]
    assert "accuracy_trend" in trend
    assert len(trend["accuracy_trend"]) == 4


@pytest.mark.unit
def test_generate_report_with_feedback_stats(tracker, fm):
    """报告应包含反馈统计（提供 feedback_manager 时）"""
    tracker.record_snapshot({"accuracy_rate": 0.7})
    fm.record_feedback("T1", "ACC_A", "false_positive", "正常业务", "analyst1")
    fm.record_feedback("T2", "ACC_B", "false_negative", "漏报可疑", "analyst1")

    report = tracker.generate_report(feedback_manager=fm)
    assert report["feedback_stats"] is not None
    assert report["feedback_stats"]["total_in_period"] == 2
    assert report["feedback_stats"]["false_positive"] == 1
    assert report["feedback_stats"]["false_negative"] == 1
    assert "feedback_manager" in report["data_sources"]


@pytest.mark.unit
def test_generate_report_saves_to_file(tracker):
    """报告应保存到文件"""
    tracker.record_snapshot({"accuracy_rate": 0.7})
    report = tracker.generate_report()
    report_path = os.path.join(tracker.reports_dir, f"{report['report_id']}.json")
    assert os.path.exists(report_path)


@pytest.mark.unit
def test_generate_report_atomic_write(tracker):
    """报告写入后不应残留临时文件（戒律 P4）"""
    tracker.record_snapshot({"accuracy_rate": 0.7})
    tracker.generate_report()
    for fname in os.listdir(tracker.reports_dir):
        assert not fname.endswith(".tmp")


# ============================================================
# 报告查询测试
# ============================================================
@pytest.mark.unit
def test_list_reports_empty(tracker):
    """无报告时应返回空列表"""
    assert tracker.list_reports() == []


@pytest.mark.unit
def test_list_reports_ordered_desc(tracker):
    """报告应按生成时间倒序"""
    tracker.record_snapshot({"accuracy_rate": 0.7})
    report1 = tracker.generate_report()
    time.sleep(0.01)
    report2 = tracker.generate_report()

    reports = tracker.list_reports()
    assert len(reports) == 2
    # 最新生成的在前
    assert reports[0]["report_id"] == report2["report_id"]
    assert reports[1]["report_id"] == report1["report_id"]


@pytest.mark.unit
def test_get_report(tracker):
    """获取指定报告"""
    tracker.record_snapshot({"accuracy_rate": 0.7})
    generated = tracker.generate_report()

    fetched = tracker.get_report(generated["report_id"])
    assert fetched is not None
    assert fetched["report_id"] == generated["report_id"]


@pytest.mark.unit
def test_get_nonexistent_report(tracker):
    """获取不存在的报告应返回None"""
    assert tracker.get_report("RPT-NONEXIST") is None


# ============================================================
# 戒律验证测试
# ============================================================
@pytest.mark.unit
def test_report_no_fabricated_data(tracker):
    """报告不应有编造标记（戒律 M1）"""
    tracker.record_snapshot({"accuracy_rate": 0.7}, description="真实指标")
    report = tracker.generate_report()
    report_str = json.dumps(report, ensure_ascii=False)
    assert "编造" not in report_str
    assert "假数据" not in report_str


@pytest.mark.unit
def test_report_traceable(tracker):
    """报告应可追溯（戒律 M4: 包含生成时间和数据来源）"""
    tracker.record_snapshot({"accuracy_rate": 0.7})
    report = tracker.generate_report()
    assert "generated_at" in report
    assert report["generated_at"] != ""
    assert "data_sources" in report
    assert isinstance(report["data_sources"], list)


@pytest.mark.unit
def test_report_includes_period(tracker):
    """报告应包含分析时段（戒律 M4: 可追溯）"""
    start = time.time()
    tracker.record_snapshot({"accuracy_rate": 0.7})
    end = time.time()

    report = tracker.generate_report(start_ts=start, end_ts=end)
    assert report["period"]["start_ts"] == start
    assert report["period"]["end_ts"] == end


@pytest.mark.unit
def test_report_summary_positive(tracker):
    """净改进时摘要应为积极"""
    tracker.record_snapshot({"false_positive_rate": 0.4})
    time.sleep(0.01)
    tracker.record_snapshot({"false_positive_rate": 0.2})  # 下降，改进

    report = tracker.generate_report()
    assert "积极" in report["summary"]


@pytest.mark.unit
def test_report_summary_needs_attention(tracker):
    """净恶化时摘要应为需关注"""
    tracker.record_snapshot({"false_negative_rate": 0.1})
    time.sleep(0.01)
    tracker.record_snapshot({"false_negative_rate": 0.3})  # 上升，恶化

    report = tracker.generate_report()
    assert "需关注" in report["summary"]
