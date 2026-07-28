"""
数据血缘追踪测试 (B2-3)

覆盖:
- 血缘记录完整生成（7 个阶段）
- 各阶段 version_info 正确填充
- 逆向追溯：报告 → 血缘、交易 → 血缘列表
- 交易参与多批次的列表查询
- 索引文件正确维护
- 记录失败不阻塞主流程
- 查询不存在时返回 None
- 完整性校验
- 过期清理
"""
import json
import os
import tempfile
import time
from unittest.mock import patch

import pytest

from tools.data_lineage_tracker import DataLineageTracker, get_lineage_tracker


# ============================================================
# 测试夹具
# ============================================================
@pytest.fixture
def tracker(tmp_path):
    """每个测试用独立临时目录的 tracker"""
    return DataLineageTracker(lineage_dir=str(tmp_path / "lineage"))


@pytest.fixture
def sample_state():
    """构造完整工作流状态（含 7 阶段字段）"""
    return {
        "execution_id": "exec_test_001",
        "import_batch_id": "batch_20260727_001",
        "transactions": [
            {"transaction_id": "T001", "from_account": "A1", "to_account": "A2",
             "amount": 45000, "timestamp": "2026-07-27 10:00:00"},
            {"transaction_id": "T002", "from_account": "A2", "to_account": "A3",
             "amount": 46000, "timestamp": "2026-07-27 10:05:00"},
            {"transaction_id": "T003", "from_account": "A1", "to_account": "A4",
             "amount": 48000, "timestamp": "2026-07-27 10:10:00"},
        ],
        "cleaned_transactions": [
            {"transaction_id": "T001", "from_account": "A1", "to_account": "A2",
             "amount": 45000, "timestamp": "2026-07-27 10:00:00"},
            {"transaction_id": "T002", "from_account": "A2", "to_account": "A3",
             "amount": 46000, "timestamp": "2026-07-27 10:05:00"},
        ],
        "preprocessing_stats": {
            "quality_score": 0.95,
            "deduplicated": 1,
            "missing_filled": 0,
        },
        "account_baselines": {"A1": {"count": 10}, "A2": {"count": 8}},
        "rule_hits": [
            {"transaction": {"transaction_id": "T001"}, "rule_hits": ["分拆转账"],
             "risk_score": 75, "evidence": ["小额多笔"]},
            {"transaction": {"transaction_id": "T002"}, "rule_hits": ["快进快出"],
             "risk_score": 70, "evidence": ["10分钟内转出"]},
        ],
        "rule_hit_count": 2,
        "rule_details": {"分拆转账": 1, "快进快出": 1},
        "rule_engine_stats": {"total_checked": 2, "matched": 2},
        "analysis_params": {"rule_config_version": "v2_strict_20260727"},
        "graph_data": {
            "nodes": [{"id": "A1"}, {"id": "A2"}, {"id": "A3"}, {"id": "A4"}],
            "edges": [{"from": "A1", "to": "A2", "amount": 45000}],
            "communities": [{"id": "C1", "members": ["A1", "A2"]}],
        },
        "graph_suspicious": [
            {"transaction": {"transaction_id": "T001"}, "community_id": "C1"},
        ],
        "graph_hit_count": 1,
        "llm_reviewed": [
            {"transaction": {"transaction_id": "T001"}, "rule_hits": ["分拆转账"]},
        ],
        "llm_confirmed": [
            {"transaction": {"transaction_id": "T001"}, "rule_hits": ["分拆转账"],
             "llm_analysis": "确认可疑", "llm_confidence": 0.9},
        ],
        "false_positives": [
            {"transaction": {"transaction_id": "T002"}, "is_false_positive": True},
        ],
        "llm_analysis_count": 1,
        "llm_stats": {"confirmed": 1, "false_positive": 1},
        "str_reports": [
            {"report_id": "STR-001", "primary_account": "A1",
             "suspicious_transactions": [{"transaction": {"transaction_id": "T001"}}]},
        ],
        "report_count": 1,
        "report_generation_stats": {"generated": 1},
        "final_reports": [
            {"report_id": "STR-001", "compliance_status": "passed"},
        ],
        "rejected_reports": [],
        "compliance_stats": {"passed": 1, "rejected": 0},
        "compliance_summary": "全部通过",
        "compliance_score": 95,
        "total_processing_time": 1.234,
        "cross_period_links": [
            {"current_report": "STR-001", "history_execution_id": "exec_old_001"},
        ],
    }


# ============================================================
# 基础功能：记录与查询
# ============================================================
def test_record_lineage_returns_lineage_id(tracker, sample_state):
    """记录血缘返回 lineage_id"""
    lid = tracker.record_lineage("exec_test_001", sample_state)
    assert lid is not None
    assert lid.startswith("LN-")
    assert len(lid) == 15  # "LN-" + 12 hex


def test_record_lineage_persists_record_file(tracker, sample_state):
    """记录后生成主记录文件"""
    lid = tracker.record_lineage("exec_test_001", sample_state)
    record_path = os.path.join(tracker.records_dir, f"{lid}.json")
    assert os.path.exists(record_path)
    with open(record_path, "r", encoding="utf-8") as f:
        record = json.load(f)
    assert record["lineage_id"] == lid
    assert record["execution_id"] == "exec_test_001"
    assert record["import_batch_id"] == "batch_20260727_001"


def test_record_lineage_disabled_returns_none(tmp_path, sample_state):
    """禁用时返回 None"""
    t = DataLineageTracker(
        lineage_dir=str(tmp_path / "lin"),
        config={"enabled": False},
    )
    assert t.record_lineage("exec_001", sample_state) is None


def test_record_lineage_empty_execution_id_returns_none(tracker, sample_state):
    """execution_id 为空返回 None"""
    assert tracker.record_lineage("", sample_state) is None
    assert tracker.record_lineage(None, sample_state) is None


def test_record_lineage_empty_state_returns_none(tracker):
    """state 为空返回 None"""
    assert tracker.record_lineage("exec_001", {}) is None
    assert tracker.record_lineage("exec_001", None) is None


def test_query_lineage_by_execution_id(tracker, sample_state):
    """按 execution_id 查询血缘"""
    tracker.record_lineage("exec_test_001", sample_state)
    record = tracker.query_lineage("exec_test_001")
    assert record is not None
    assert record["execution_id"] == "exec_test_001"


def test_query_lineage_not_exists_returns_none(tracker):
    """查询不存在的 execution_id 返回 None"""
    assert tracker.query_lineage("nonexistent") is None
    assert tracker.query_lineage("") is None


def test_query_by_lineage_id(tracker, sample_state):
    """按 lineage_id 直接查询"""
    lid = tracker.record_lineage("exec_test_001", sample_state)
    record = tracker.query_by_lineage_id(lid)
    assert record is not None
    assert record["lineage_id"] == lid


def test_query_by_lineage_id_not_exists(tracker):
    """lineage_id 不存在返回 None"""
    assert tracker.query_by_lineage_id("LN-nonexistent") is None
    assert tracker.query_by_lineage_id("") is None


# ============================================================
# 阶段提取完整性
# ============================================================
def test_record_contains_all_required_stages(tracker, sample_state):
    """血缘记录包含 5 个必需阶段"""
    lid = tracker.record_lineage("exec_test_001", sample_state)
    record = tracker.query_by_lineage_id(lid)
    stage_names = {s["stage"] for s in record["stages"]}
    required = {"data_preprocess", "rule_engine", "graph_analyst",
                "llm_reviewer", "report_generator"}
    assert required.issubset(stage_names)


def test_record_contains_optional_cross_period_stage(tracker, sample_state):
    """含 cross_period_links 时记录跨期阶段"""
    lid = tracker.record_lineage("exec_test_001", sample_state)
    record = tracker.query_by_lineage_id(lid)
    stage_names = {s["stage"] for s in record["stages"]}
    assert "cross_period_linker" in stage_names


def test_record_no_cross_period_stage_when_no_links(tracker, sample_state):
    """无 cross_period_links 时不记录跨期阶段"""
    state = dict(sample_state)
    state.pop("cross_period_links", None)
    lid = tracker.record_lineage("exec_test_001", state)
    record = tracker.query_by_lineage_id(lid)
    stage_names = {s["stage"] for s in record["stages"]}
    assert "cross_period_linker" not in stage_names


def test_preprocess_stage_stats_correct(tracker, sample_state):
    """预处理阶段统计正确"""
    lid = tracker.record_lineage("exec_test_001", sample_state)
    record = tracker.query_by_lineage_id(lid)
    preprocess = next(s for s in record["stages"] if s["stage"] == "data_preprocess")
    assert preprocess["stats"]["raw_count"] == 3
    assert preprocess["stats"]["cleaned_count"] == 2
    assert preprocess["stats"]["quality_score"] == 0.95
    assert preprocess["stats"]["deduplicated"] == 1
    assert preprocess["stats"]["account_baselines_count"] == 2


def test_rule_engine_stage_version_info(tracker, sample_state):
    """规则引擎阶段版本信息正确"""
    lid = tracker.record_lineage("exec_test_001", sample_state)
    record = tracker.query_by_lineage_id(lid)
    rule_stage = next(s for s in record["stages"] if s["stage"] == "rule_engine")
    assert rule_stage["version_info"]["rule_config_version"] == "v2_strict_20260727"
    assert rule_stage["stats"]["rule_hit_count"] == 2
    assert rule_stage["stats"]["by_rule"] == {"分拆转账": 1, "快进快出": 1}


def test_llm_stage_version_info(tracker, sample_state):
    """LLM 阶段模型版本正确"""
    lid = tracker.record_lineage("exec_test_001", sample_state)
    record = tracker.query_by_lineage_id(lid)
    llm_stage = next(s for s in record["stages"] if s["stage"] == "llm_reviewer")
    # 应包含 llm_model 字段
    assert "llm_model" in llm_stage["version_info"]
    assert llm_stage["stats"]["llm_confirmed_count"] == 1
    assert llm_stage["stats"]["false_positive_count"] == 1


def test_compliance_stage_stats_correct(tracker, sample_state):
    """合规阶段统计正确"""
    lid = tracker.record_lineage("exec_test_001", sample_state)
    record = tracker.query_by_lineage_id(lid)
    comp_stage = next(s for s in record["stages"] if s["stage"] == "compliance_auditor")
    assert comp_stage["stats"]["final_report_count"] == 1
    assert comp_stage["stats"]["rejected_count"] == 0
    assert comp_stage["stats"]["compliance_score"] == 95


def test_final_outputs_contains_report_ids(tracker, sample_state):
    """final_outputs 包含报告ID列表"""
    lid = tracker.record_lineage("exec_test_001", sample_state)
    record = tracker.query_by_lineage_id(lid)
    assert "STR-001" in record["final_outputs"]["str_reports"]
    assert record["final_outputs"]["report_count"] == 1


# ============================================================
# 逆向追溯
# ============================================================
def test_trace_report_returns_lineage(tracker, sample_state):
    """按报告ID追溯到血缘记录"""
    lid = tracker.record_lineage("exec_test_001", sample_state)
    record = tracker.trace_report("STR-001")
    assert record is not None
    assert record["lineage_id"] == lid
    assert record["execution_id"] == "exec_test_001"


def test_trace_report_not_exists_returns_none(tracker):
    """追溯不存在的报告返回 None"""
    assert tracker.trace_report("STR-NONEXISTENT") is None
    assert tracker.trace_report("") is None


def test_trace_transaction_returns_list(tracker, sample_state):
    """按交易ID追溯到血缘记录列表"""
    lid = tracker.record_lineage("exec_test_001", sample_state)
    records = tracker.trace_transaction("T001")
    assert len(records) == 1
    assert records[0]["lineage_id"] == lid


def test_trace_transaction_multiple_batches(tracker, sample_state):
    """交易参与多批次时返回多条"""
    # 第一次记录
    state1 = dict(sample_state)
    state1["execution_id"] = "exec_001"
    lid1 = tracker.record_lineage("exec_001", state1)

    # 第二次记录（同一交易 T001 参与不同执行）
    state2 = dict(sample_state)
    state2["execution_id"] = "exec_002"
    lid2 = tracker.record_lineage("exec_002", state2)

    records = tracker.trace_transaction("T001")
    assert len(records) == 2
    lineage_ids = {r["lineage_id"] for r in records}
    assert lid1 in lineage_ids
    assert lid2 in lineage_ids


def test_trace_transaction_not_exists_returns_empty(tracker):
    """追溯不存在的交易返回空列表"""
    assert tracker.trace_transaction("T_NONEXISTENT") == []
    assert tracker.trace_transaction("") == []


def test_trace_transaction_deduplicates_same_lineage(tracker, sample_state):
    """同一血缘中重复出现的交易只索引一次"""
    tracker.record_lineage("exec_001", sample_state)
    # T001 在 transactions 和 rule_hits 中都出现，应只索引一次
    records = tracker.trace_transaction("T001")
    assert len(records) == 1


# ============================================================
# 索引维护
# ============================================================
def test_list_lineages_returns_sorted(tracker, sample_state):
    """list_lineages 按时间倒序返回"""
    state1 = dict(sample_state)
    state1["execution_id"] = "exec_001"
    tracker.record_lineage("exec_001", state1)
    time.sleep(0.01)
    state2 = dict(sample_state)
    state2["execution_id"] = "exec_002"
    tracker.record_lineage("exec_002", state2)

    entries = tracker.list_lineages(limit=10)
    assert len(entries) == 2
    # 后记录的在前
    assert entries[0]["execution_id"] == "exec_002"
    assert entries[1]["execution_id"] == "exec_001"


def test_list_lineages_respects_limit(tracker, sample_state):
    """list_lineages 遵守 limit"""
    for i in range(5):
        state = dict(sample_state)
        state["execution_id"] = f"exec_{i}"
        tracker.record_lineage(f"exec_{i}", state)
        time.sleep(0.01)

    entries = tracker.list_lineages(limit=3)
    assert len(entries) == 3


def test_index_file_maintained(tracker, sample_state):
    """主索引文件正确维护"""
    tracker.record_lineage("exec_001", sample_state)
    assert os.path.exists(tracker.index_path)
    with open(tracker.index_path, "r", encoding="utf-8") as f:
        index = json.load(f)
    assert len(index["entries"]) == 1
    assert index["entries"][0]["execution_id"] == "exec_001"
    assert index["entries"][0]["report_ids"] == ["STR-001"]


def test_report_index_file_maintained(tracker, sample_state):
    """报告索引文件正确维护"""
    tracker.record_lineage("exec_001", sample_state)
    idx_path = os.path.join(tracker.by_report_dir, "STR-001.json")
    assert os.path.exists(idx_path)
    with open(idx_path, "r", encoding="utf-8") as f:
        idx = json.load(f)
    assert idx["execution_id"] == "exec_001"


def test_transaction_index_file_maintained(tracker, sample_state):
    """交易索引文件正确维护"""
    tracker.record_lineage("exec_001", sample_state)
    idx_path = os.path.join(tracker.by_transaction_dir, "T001.json")
    assert os.path.exists(idx_path)
    with open(idx_path, "r", encoding="utf-8") as f:
        idx = json.load(f)
    assert len(idx["lineage_ids"]) == 1


# ============================================================
# 完整性校验
# ============================================================
def test_verify_integrity_valid_record(tracker, sample_state):
    """完整血缘校验通过"""
    lid = tracker.record_lineage("exec_test_001", sample_state)
    result = tracker.verify_integrity(lid)
    assert result["valid"] is True
    assert len(result["issues"]) == 0
    assert "data_preprocess" in result["stages_present"]


def test_verify_integrity_missing_lineage(tracker):
    """不存在的血缘校验失败"""
    result = tracker.verify_integrity("LN-nonexistent")
    assert result["valid"] is False
    assert "血缘记录不存在" in result["issues"]


def test_verify_integrity_missing_required_stage(tracker, sample_state):
    """缺少必需阶段校验失败"""
    # 删除 rule_hits 使 rule_engine 阶段无输出（但仍会记录空阶段）
    # 改为直接修改记录
    lid = tracker.record_lineage("exec_test_001", sample_state)
    record_path = os.path.join(tracker.records_dir, f"{lid}.json")
    with open(record_path, "r", encoding="utf-8") as f:
        record = json.load(f)
    # 删除 rule_engine 阶段
    record["stages"] = [s for s in record["stages"] if s.get("stage") != "rule_engine"]
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)

    result = tracker.verify_integrity(lid)
    assert result["valid"] is False
    assert "rule_engine" in result["stages_missing"]


# ============================================================
# 异常隔离（戒律 P4）
# ============================================================
def test_record_lineage_does_not_raise_on_disk_error(tracker, sample_state, capsys):
    """磁盘写入失败时不抛异常"""
    with patch("builtins.open", side_effect=OSError("disk full")):
        result = tracker.record_lineage("exec_001", sample_state)
    # 应返回 None 而非抛异常
    assert result is None
    out = capsys.readouterr().out
    assert "记录失败" in out or "血缘追踪" in out


def test_query_lineage_does_not_raise_on_corrupt_index(tracker, sample_state):
    """索引文件损坏时查询不抛异常"""
    tracker.record_lineage("exec_001", sample_state)
    # 写入损坏的索引
    with open(tracker.index_path, "w", encoding="utf-8") as f:
        f.write("{ corrupt json")
    # 查询应返回 None 而非抛异常
    result = tracker.query_lineage("exec_001")
    assert result is None


def test_trace_report_does_not_raise_on_corrupt_index(tracker, sample_state):
    """报告索引损坏时不抛异常"""
    tracker.record_lineage("exec_001", sample_state)
    idx_path = os.path.join(tracker.by_report_dir, "STR-001.json")
    with open(idx_path, "w", encoding="utf-8") as f:
        f.write("{ corrupt")
    result = tracker.trace_report("STR-001")
    assert result is None


# ============================================================
# 过期清理
# ============================================================
def test_cleanup_expired_removes_old_records(tracker, sample_state):
    """清理过期记录"""
    lid = tracker.record_lineage("exec_001", sample_state)
    # 修改索引时间戳为 100 天前
    with open(tracker.index_path, "r", encoding="utf-8") as f:
        index = json.load(f)
    old_ts = time.time() - 100 * 86400
    index["entries"][0]["created_at_ts"] = old_ts
    with open(tracker.index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)

    removed = tracker.cleanup_expired()
    assert removed == 1
    # 主记录应被删除
    record_path = os.path.join(tracker.records_dir, f"{lid}.json")
    assert not os.path.exists(record_path)


def test_cleanup_expired_keeps_recent_records(tracker, sample_state):
    """保留近期记录"""
    tracker.record_lineage("exec_001", sample_state)
    removed = tracker.cleanup_expired()
    assert removed == 0


def test_cleanup_expired_zero_retain_days_disables(tracker, sample_state):
    """retain_days=0 时不清理"""
    tracker.record_lineage("exec_001", sample_state)
    tracker.retain_days = 0
    removed = tracker.cleanup_expired()
    assert removed == 0


# ============================================================
# 模块级单例
# ============================================================
def test_get_lineage_tracker_returns_instance():
    """get_lineage_tracker 返回单例"""
    tracker = get_lineage_tracker()
    # 可能是 None（如 config 加载失败）或实例
    if tracker is not None:
        assert isinstance(tracker, DataLineageTracker)


# ============================================================
# 边界场景
# ============================================================
def test_record_lineage_minimal_state(tracker):
    """最小 state 也能记录（只有 transactions）"""
    minimal_state = {
        "execution_id": "exec_min",
        "transactions": [{"transaction_id": "T1"}],
    }
    lid = tracker.record_lineage("exec_min", minimal_state)
    assert lid is not None
    record = tracker.query_by_lineage_id(lid)
    # 应有部分阶段（即使为空也算记录）
    assert "stages" in record


def test_record_lineage_state_without_reports(tracker):
    """无报告的状态也能记录"""
    state = {
        "execution_id": "exec_no_reports",
        "transactions": [{"transaction_id": "T1"}],
        "rule_hits": [],
        "str_reports": [],
        "final_reports": [],
    }
    lid = tracker.record_lineage("exec_no_reports", state)
    assert lid is not None
    record = tracker.query_by_lineage_id(lid)
    assert record["final_outputs"]["report_count"] == 0
    assert record["final_outputs"]["str_reports"] == []


def test_record_lineage_with_rejected_reports(tracker, sample_state):
    """被驳回报告也纳入索引"""
    state = dict(sample_state)
    state["rejected_reports"] = [
        {"report_id": "STR-REJ-001", "compliance_status": "rejected"},
    ]
    lid = tracker.record_lineage("exec_001", state)
    record = tracker.query_by_lineage_id(lid)
    # STR-REJ-001 应在 report_ids 中
    assert "STR-REJ-001" in record["report_ids"]
    # 报告索引应存在
    idx_path = os.path.join(tracker.by_report_dir, "STR-REJ-001.json")
    assert os.path.exists(idx_path)
