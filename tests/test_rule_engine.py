"""
规则引擎单元测试

覆盖4条反洗钱规则的核心检测逻辑:
1. 分拆转账 (Smurfing)
2. 快进快出 (Fast-In-Fast-Out)
3. 对敲交易 (Round-Trip)
4. 大额交易 (Large Amount)
"""
import pytest
from agents.rule_engine import (
    _detect_smurfing,
    _detect_fast_in_fast_out,
    _detect_round_trip,
    _detect_large_amount,
    _merge_suspicious,
)
from graph.state import Transaction


def _make_txn(
    tid: str,
    from_acc: str,
    to_acc: str,
    amount: float,
    timestamp: str,
    txn_type: str = "transfer",
) -> Transaction:
    """构造测试交易"""
    return {
        "transaction_id": tid,
        "from_account": from_acc,
        "to_account": to_acc,
        "amount": amount,
        "timestamp": timestamp,
        "transaction_type": txn_type,
        "remark": "",
    }


# ============================================================
# 规则 1: 分拆转账
# ============================================================
def test_smurfing_detected():
    """同一收款账户1小时内收到5笔4-5万的转账应被检测"""
    txns = [
        _make_txn(f"T{i}", f"PAYER_{i}", "RECV_A", 45000.0, f"2026-07-01T10:{i:02d}:00")
        for i in range(5)
    ]
    result = _detect_smurfing(txns)
    assert len(result) == 5
    assert all(r["rule_hits"] == ["分拆转账"] for r in result)


def test_smurfing_not_triggered_below_threshold():
    """金额不在4-5万区间不触发"""
    txns = [
        _make_txn(f"T{i}", f"PAYER_{i}", "RECV_A", 30000.0, f"2026-07-01T10:{i:02d}:00")
        for i in range(5)
    ]
    result = _detect_smurfing(txns)
    assert len(result) == 0


# ============================================================
# 规则 2: 快进快出
# ============================================================
def test_fast_in_fast_out_detected():
    """入账后10分钟内转出95%以上应被检测"""
    txns = [
        _make_txn("IN_1", "PAYER_A", "ACC_X", 100000.0, "2026-07-01T10:00:00"),
        _make_txn("OUT_1", "ACC_X", "PAYER_B", 96000.0, "2026-07-01T10:05:00"),
    ]
    result = _detect_fast_in_fast_out(txns)
    # 入账交易 + 关联出账都会被标记
    assert len(result) >= 1
    assert any(r["rule_hits"] == ["快进快出"] for r in result)


def test_fast_in_fast_out_not_triggered_low_ratio():
    """转出比例不足95%不触发"""
    txns = [
        _make_txn("IN_1", "PAYER_A", "ACC_X", 100000.0, "2026-07-01T10:00:00"),
        _make_txn("OUT_1", "ACC_X", "PAYER_B", 50000.0, "2026-07-01T10:05:00"),
    ]
    result = _detect_fast_in_fast_out(txns)
    assert len(result) == 0


# ============================================================
# 规则 3: 对敲交易
# ============================================================
def test_round_trip_detected():
    """两个账户7天内互相转账金额接近应被检测"""
    txns = [
        _make_txn("RT_1", "ACC_A", "ACC_B", 50000.0, "2026-07-01T10:00:00"),
        _make_txn("RT_2", "ACC_B", "ACC_A", 48000.0, "2026-07-03T10:00:00"),
    ]
    result = _detect_round_trip(txns)
    assert len(result) == 2
    assert all(r["rule_hits"] == ["对敲交易"] for r in result)


def test_round_trip_not_triggered_amount_diff_too_large():
    """金额差异超过20%不触发"""
    txns = [
        _make_txn("RT_1", "ACC_A", "ACC_B", 50000.0, "2026-07-01T10:00:00"),
        _make_txn("RT_2", "ACC_B", "ACC_A", 20000.0, "2026-07-03T10:00:00"),
    ]
    result = _detect_round_trip(txns)
    assert len(result) == 0


# ============================================================
# 规则 4: 大额交易
# ============================================================
def test_large_amount_detected():
    """单笔≥10万应被检测"""
    txns = [
        _make_txn("BIG_1", "ACC_A", "ACC_B", 150000.0, "2026-07-01T10:00:00"),
        _make_txn("BIG_2", "ACC_A", "ACC_B", 100000.0, "2026-07-01T11:00:00"),
    ]
    result = _detect_large_amount(txns)
    assert len(result) == 2


def test_large_amount_not_triggered_below_threshold():
    """单笔<10万不触发"""
    txns = [
        _make_txn("SMALL_1", "ACC_A", "ACC_B", 99999.0, "2026-07-01T10:00:00"),
    ]
    result = _detect_large_amount(txns)
    assert len(result) == 0


# ============================================================
# 合并去重
# ============================================================
def test_merge_suspicious_dedup():
    """同一笔交易命中多规则应合并去重"""
    txn = _make_txn("DUP_1", "ACC_A", "ACC_B", 120000.0, "2026-07-01T10:00:00")
    s1 = {
        "transaction": txn, "rule_hits": ["大额交易"], "risk_score": 0.4,
        "evidence": ["大额"], "graph_evidence": None, "llm_analysis": None,
        "llm_confidence": None, "is_false_positive": None, "community_id": None,
    }
    s2 = {
        "transaction": txn, "rule_hits": ["对敲交易"], "risk_score": 0.65,
        "evidence": ["对敲"], "graph_evidence": None, "llm_analysis": None,
        "llm_confidence": None, "is_false_positive": None, "community_id": None,
    }
    merged = _merge_suspicious([[s1], [s2]])
    assert len(merged) == 1
    assert len(merged[0]["rule_hits"]) == 2
    assert merged[0]["risk_score"] == 0.65  # 取最高


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
