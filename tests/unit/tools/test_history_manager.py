"""
分析历史记录管理测试 (Task 7-4)

覆盖:
- 保存运行记录（单次、多次、相同ID覆盖）
- 列表查询（limit、倒序）
- 获取单次记录
- 搜索（日期范围、报告数、账户）
- 删除、清空
- 统计
- 戒律M1/P1: 数据完整性、不遗漏
"""
import os
import json
import time
from typing import Dict, Any, List

import pytest

from tools.history_manager import HistoryManager


# ============================================================
# 夹具
# ============================================================
@pytest.fixture()
def history_dir(tmp_path):
    d = tmp_path / "history"
    d.mkdir()
    return str(d)


@pytest.fixture()
def sample_state():
    """工作流最终状态样本"""
    return {
        "execution_id": "abc12345",
        "analysis_date": "2025-01-15",
        "transactions": [
            {"transaction_id": "T001", "amount": 45000.0},
            {"transaction_id": "T002", "amount": 48000.0},
        ],
        "rule_hit_count": 5,
        "rule_details": {"分拆转账": 3, "大额交易": 2},
        "llm_confirmed": [{"transaction": {"transaction_id": "T001"}}],
        "str_reports": [
            {"report_id": "STR-001", "primary_account": "A002", "risk_level": "high"},
            {"report_id": "STR-002", "primary_account": "A005", "risk_level": "medium"},
        ],
        "report_count": 2,
        "total_processing_time": 12.34,
        "step_times": {"rule_engine": 2.1, "llm_reviewer": 8.5},
        "interrupted": False,
        "error": "",
    }


@pytest.fixture()
def multi_states():
    """多份运行状态"""
    states = []
    for i in range(3):
        s = {
            "execution_id": f"run{i:04d}",
            "analysis_date": f"2025-01-{i+10:02d}",
            "transactions": [{"transaction_id": f"T{i}", "amount": 1000.0 * i}],
            "rule_hit_count": i + 1,
            "rule_details": {"大额交易": i + 1},
            "llm_confirmed": [],
            "str_reports": [
                {"report_id": f"STR-{i}", "primary_account": f"A{i:03d}", "risk_level": "high" if i > 0 else "low"},
            ],
            "report_count": 1,
            "total_processing_time": 1.0 + i,
            "step_times": {},
            "interrupted": False,
            "error": "",
        }
        states.append(s)
    return states


# ============================================================
# 保存测试
# ============================================================
@pytest.mark.unit
def test_save_run_creates_files(history_dir, sample_state):
    """保存运行记录应创建JSON文件和索引"""
    hm = HistoryManager(history_dir=history_dir)
    exec_id = hm.save_run(sample_state)

    assert exec_id == "abc12345"
    # 单次记录文件
    record_path = os.path.join(history_dir, "abc12345.json")
    assert os.path.exists(record_path)
    # 索引文件
    assert os.path.exists(hm.index_path)


@pytest.mark.unit
def test_save_run_record_contains_key_fields(history_dir, sample_state):
    """保存的记录应包含关键字段"""
    hm = HistoryManager(history_dir=history_dir)
    hm.save_run(sample_state)

    with open(os.path.join(history_dir, "abc12345.json"), "r", encoding="utf-8") as f:
        record = json.load(f)

    assert record["execution_id"] == "abc12345"
    assert record["analysis_date"] == "2025-01-15"
    assert record["transactions_count"] == 2
    assert record["rule_hit_count"] == 5
    assert record["report_count"] == 2
    assert record["duration_seconds"] == 12.34
    assert record["risk_distribution"] == {"high": 1, "medium": 1}
    assert record["primary_accounts"] == ["A002", "A005"]
    assert record["rule_details"] == {"分拆转账": 3, "大额交易": 2}
    # 应有交易数据哈希
    assert record["transactions_hash"] != ""


@pytest.mark.unit
def test_save_run_same_id_overwrites(history_dir, sample_state):
    """相同execution_id应覆盖旧记录"""
    hm = HistoryManager(history_dir=history_dir)
    hm.save_run(sample_state)
    # 修改后再次保存
    sample_state["rule_hit_count"] = 999
    hm.save_run(sample_state)

    runs = hm.list_runs()
    assert len(runs) == 1  # 不应重复
    assert runs[0]["rule_hit_count"] == 999


@pytest.mark.unit
def test_save_run_without_execution_id_generates_one(history_dir):
    """没有execution_id时应自动生成"""
    hm = HistoryManager(history_dir=history_dir)
    state = {"transactions": [], "rule_hit_count": 0, "report_count": 0}
    exec_id = hm.save_run(state)
    assert exec_id != ""
    assert len(exec_id) == 8  # uuid前8位


# ============================================================
# 列表查询测试
# ============================================================
@pytest.mark.unit
def test_list_runs_empty(history_dir):
    """空历史应返回空列表"""
    hm = HistoryManager(history_dir=history_dir)
    assert hm.list_runs() == []


@pytest.mark.unit
def test_list_returns_runs_in_desc_order(history_dir, multi_states):
    """列表应按时间倒序"""
    hm = HistoryManager(history_dir=history_dir)
    for s in multi_states:
        hm.save_run(s)
        time.sleep(0.01)  # 确保时间戳不同

    runs = hm.list_runs()
    assert len(runs) == 3
    # 最新的应该在前面
    assert runs[0]["execution_id"] == "run0002"


@pytest.mark.unit
def test_list_runs_respects_limit(history_dir, multi_states):
    """limit应限制返回数"""
    hm = HistoryManager(history_dir=history_dir)
    for s in multi_states:
        hm.save_run(s)
        time.sleep(0.01)

    runs = hm.list_runs(limit=2)
    assert len(runs) == 2


# ============================================================
# 获取单次记录测试
# ============================================================
@pytest.mark.unit
def test_get_run_returns_full_record(history_dir, sample_state):
    """get_run应返回完整记录"""
    hm = HistoryManager(history_dir=history_dir)
    hm.save_run(sample_state)

    record = hm.get_run("abc12345")
    assert record is not None
    assert record["execution_id"] == "abc12345"
    assert record["rule_details"] == sample_state["rule_details"]


@pytest.mark.unit
def test_get_run_nonexistent_returns_none(history_dir):
    """不存在的ID应返回None"""
    hm = HistoryManager(history_dir=history_dir)
    assert hm.get_run("nonexistent") is None


# ============================================================
# 搜索测试
# ============================================================
@pytest.mark.unit
def test_search_by_min_report_count(history_dir, multi_states):
    """按最少报告数搜索"""
    hm = HistoryManager(history_dir=history_dir)
    for s in multi_states:
        hm.save_run(s)

    # 全部报告数都是1，过滤>=2应该返回空
    results = hm.search_runs(min_report_count=2)
    assert len(results) == 0
    # >=1应该返回全部
    results = hm.search_runs(min_report_count=1)
    assert len(results) == 3


@pytest.mark.unit
def test_search_by_account(history_dir, multi_states):
    """按账户搜索"""
    hm = HistoryManager(history_dir=history_dir)
    for s in multi_states:
        hm.save_run(s)

    # 搜索 A001（在run0001中）
    results = hm.search_runs(account="A001")
    assert len(results) == 1
    assert results[0]["execution_id"] == "run0001"


@pytest.mark.unit
def test_search_by_date_range(history_dir, multi_states):
    """按日期范围搜索"""
    hm = HistoryManager(history_dir=history_dir)
    for s in multi_states:
        hm.save_run(s)

    # 不限制日期应返回全部
    results = hm.search_runs()
    assert len(results) == 3


# ============================================================
# 删除测试
# ============================================================
@pytest.mark.unit
def test_delete_run_removes_record_and_index(history_dir, sample_state):
    """删除应同时移除记录文件和索引项"""
    hm = HistoryManager(history_dir=history_dir)
    hm.save_run(sample_state)

    assert os.path.exists(os.path.join(history_dir, "abc12345.json"))
    assert hm.delete_run("abc12345") is True
    assert not os.path.exists(os.path.join(history_dir, "abc12345.json"))
    assert hm.get_run("abc12345") is None
    assert hm.list_runs() == []


@pytest.mark.unit
def test_delete_nonexistent_returns_false(history_dir):
    """删除不存在的ID应返回False"""
    hm = HistoryManager(history_dir=history_dir)
    assert hm.delete_run("nonexistent") is False


@pytest.mark.unit
def test_clear_all_removes_everything(history_dir, multi_states):
    """清空应删除所有记录"""
    hm = HistoryManager(history_dir=history_dir)
    for s in multi_states:
        hm.save_run(s)

    deleted = hm.clear_all()
    assert deleted >= 4  # 3记录 + 1索引
    assert hm.list_runs() == []


# ============================================================
# 统计测试
# ============================================================
@pytest.mark.unit
def test_stats_empty(history_dir):
    """空历史的统计"""
    hm = HistoryManager(history_dir=history_dir)
    stats = hm.stats()
    assert stats["total_runs"] == 0
    assert stats["total_reports"] == 0


@pytest.mark.unit
def test_stats_with_records(history_dir, multi_states):
    """有记录的统计"""
    hm = HistoryManager(history_dir=history_dir)
    for s in multi_states:
        hm.save_run(s)

    stats = hm.stats()
    assert stats["total_runs"] == 3
    assert stats["total_reports"] == 3  # 每次运行1份报告
    assert stats["total_transactions"] == 3  # 每次运行1笔交易
    assert stats["avg_duration"] > 0


# ============================================================
# 数据完整性测试（戒律 M1/P1）
# ============================================================
@pytest.mark.unit
def test_no_fabricated_data_in_record(history_dir, sample_state):
    """记录中不应有编造数据（戒律 M1）"""
    hm = HistoryManager(history_dir=history_dir)
    hm.save_run(sample_state)

    record = hm.get_run("abc12345")
    record_str = json.dumps(record, ensure_ascii=False)
    # 不应包含编造标记
    assert "编造" not in record_str
    assert "假数据" not in record_str
    # 应包含真实数据
    assert "abc12345" in record_str
    assert "A002" in record_str
    assert "2025-01-15" in record_str


@pytest.mark.unit
def test_interrupted_run_also_saved(history_dir):
    """中断的运行也应保存（戒律 P1: 不遗漏）"""
    hm = HistoryManager(history_dir=history_dir)
    state = {
        "execution_id": "interrupted_run",
        "transactions": [],
        "rule_hit_count": 0,
        "report_count": 0,
        "interrupted": True,
        "error": "用户中断",
    }
    hm.save_run(state)

    record = hm.get_run("interrupted_run")
    assert record is not None
    assert record["interrupted"] is True
    assert record["error"] == "用户中断"


@pytest.mark.unit
def test_failed_run_with_node_error_saved(history_dir):
    """有节点错误的运行也应保存"""
    hm = HistoryManager(history_dir=history_dir)
    state = {
        "execution_id": "failed_run",
        "transactions": [],
        "rule_hit_count": 0,
        "report_count": 0,
        "_node_error": {
            "node": "图分析",
            "error_type": "RuntimeError",
            "error_msg": "GNN模型加载失败",
        },
    }
    hm.save_run(state)

    record = hm.get_run("failed_run")
    assert record is not None
    assert len(record["node_errors"]) == 1
    assert record["node_errors"][0]["node"] == "图分析"
