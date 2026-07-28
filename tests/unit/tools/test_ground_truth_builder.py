"""
真值集构建工具测试

覆盖:
- GroundTruthRecord / GroundTruthDataset 数据操作
- 自动标注逻辑
- 人工审核接口
- 与数据生成器集成
"""
import json
import os
import tempfile

import pytest

from tools.ground_truth_builder import (
    GroundTruthRecord,
    GroundTruthDataset,
    auto_label_transactions,
    review_transaction,
    export_for_review,
    build_ground_truth_from_generator,
)
from config import GROUND_TRUTH_DIR


# ============================================================
# GroundTruthRecord
# ============================================================
def test_record_roundtrip():
    """记录序列化与反序列化"""
    r = GroundTruthRecord(
        transaction_id="TXN001",
        is_suspicious=True,
        suspicious_reasons=["分拆转账"],
        labels=["confirmed"],
        review_status="reviewed",
        reviewer="admin",
        notes="test",
    )
    d = r.to_dict()
    r2 = GroundTruthRecord.from_dict(d)
    assert r2.transaction_id == "TXN001"
    assert r2.is_suspicious is True
    assert r2.suspicious_reasons == ["分拆转账"]
    assert r2.review_status == "reviewed"


def test_record_none_suspicious():
    """is_suspicious 为 None 时表示待定"""
    r = GroundTruthRecord(transaction_id="TXN002", is_suspicious=None)
    assert r.is_suspicious is None
    d = r.to_dict()
    assert d["is_suspicious"] is None


# ============================================================
# GroundTruthDataset
# ============================================================
def test_dataset_stats():
    """数据集统计计算正确"""
    ds = GroundTruthDataset(name="test", description="test ds")
    ds.add_record(GroundTruthRecord("T1", is_suspicious=True))
    ds.add_record(GroundTruthRecord("T2", is_suspicious=False))
    ds.add_record(GroundTruthRecord("T3", is_suspicious=None))
    ds.add_record(GroundTruthRecord("T4", is_suspicious=True, review_status="reviewed"))

    stats = ds._compute_stats()
    assert stats["total_records"] == 4
    assert stats["suspicious_count"] == 2
    assert stats["normal_count"] == 1
    assert stats["pending_count"] == 1
    assert stats["reviewed_count"] == 1
    assert stats["suspicious_ratio"] == 0.5


def test_dataset_save_load():
    """数据集保存与加载"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "gt.json")
        ds = GroundTruthDataset(name="save_test", description="roundtrip")
        ds.add_record(GroundTruthRecord("T1", is_suspicious=True, suspicious_reasons=["大额交易"]))
        ds.save(path)

        ds2 = GroundTruthDataset.load(path)
        assert ds2.name == "save_test"
        assert "T1" in ds2.records
        assert ds2.records["T1"].is_suspicious is True


# ============================================================
# 自动标注
# ============================================================
def test_auto_label_basic():
    """自动标注基于 is_suspicious 字段"""
    transactions = [
        {"transaction_id": "N1", "is_suspicious": False, "suspicious_reason": ""},
        {"transaction_id": "S1", "is_suspicious": True, "suspicious_reason": "大额交易"},
    ]
    ds = auto_label_transactions(transactions, conservative=False)
    assert ds.records["N1"].is_suspicious is False
    assert ds.records["S1"].is_suspicious is True
    assert ds.records["S1"].suspicious_reasons == ["大额交易"]


def test_auto_label_conservative():
    """保守模式下 None 标记为 pending"""
    transactions = [
        {"transaction_id": "U1", "is_suspicious": None},
    ]
    ds = auto_label_transactions(transactions, conservative=True)
    assert ds.records["U1"].is_suspicious is None
    assert ds.records["U1"].review_status == "pending"


def test_auto_label_missing_tid_skipped():
    """缺少 transaction_id 的交易被跳过"""
    transactions = [
        {"amount": 100},
        {"transaction_id": "T1", "is_suspicious": False},
    ]
    ds = auto_label_transactions(transactions)
    assert len(ds.records) == 1


# ============================================================
# 人工审核
# ============================================================
def test_review_transaction():
    """人工审核修改记录状态"""
    ds = GroundTruthDataset()
    ds.add_record(GroundTruthRecord("T1", is_suspicious=None, review_status="pending"))

    ok = review_transaction(ds, "T1", is_suspicious=True, reviewer="alice", notes="确认可疑")
    assert ok is True
    assert ds.records["T1"].is_suspicious is True
    assert ds.records["T1"].review_status == "reviewed"
    assert ds.records["T1"].reviewer == "alice"


def test_review_transaction_not_found():
    """审核不存在的交易返回 False"""
    ds = GroundTruthDataset()
    ok = review_transaction(ds, "NONEXIST", is_suspicious=True)
    assert ok is False


# ============================================================
# 导出审核
# ============================================================
def test_export_for_review():
    """导出待定记录为 CSV"""
    with tempfile.TemporaryDirectory() as tmpdir:
        ds = GroundTruthDataset()
        ds.add_record(GroundTruthRecord("T1", is_suspicious=None, suspicious_reasons=["?"]))
        ds.add_record(GroundTruthRecord("T2", is_suspicious=False))

        path = os.path.join(tmpdir, "review.csv")
        export_for_review(ds, path)
        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()
        assert len(lines) == 2  # header + 1 pending row


# ============================================================
# 与数据生成器集成
# ============================================================
def test_build_from_generator():
    """从生成器构建真值集"""
    ds, txns = build_ground_truth_from_generator(
        normal_count=10,
        suspicious_modes=["smurfing"],
        save=False,
        name="test_gen",
    )
    assert ds.name == "test_gen"
    assert len(ds.records) == len(txns)
    assert ds.stats["total_records"] == len(txns)
    # 正常交易 + 8笔分拆转账可疑（large_amount 已改为非可疑，改用 smurfing 验证可疑路径）
    assert ds.stats["normal_count"] == 10
    assert ds.stats["suspicious_count"] == 8


# ============================================================
# 边界测试
# ============================================================
def test_empty_dataset_stats():
    """空数据集统计不报错"""
    ds = GroundTruthDataset()
    stats = ds._compute_stats()
    assert stats["total_records"] == 0
    assert stats["suspicious_ratio"] == 0.0
