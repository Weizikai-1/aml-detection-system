"""
跨境交易检测测试（B0-2）

测试覆盖:
1. 频繁跨境汇款检测
2. 跨境分拆检测
3. 大额换汇检测
4. 高风险地区跨境交易检测
5. 正常跨境贸易不误报
6. 无跨境字段交易不误报
7. 证据链完整性
8. 风险分范围验证
"""
import os
import sys
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.rule_engine import _detect_cross_border


def _make_txn(txn_id, from_acc, to_acc, amount, timestamp, **kwargs):
    """构造测试交易"""
    txn = {
        "transaction_id": txn_id,
        "from_account": from_acc,
        "to_account": to_acc,
        "amount": amount,
        "timestamp": timestamp,
    }
    txn.update(kwargs)
    return txn


class TestFrequentCrossBorder:
    """频繁跨境汇款检测"""

    def test_frequent_cross_border_detected(self):
        """7天内5笔以上跨境交易被检测到"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = [
            _make_txn(
                f"TXN_CB_{i:03d}",
                f"ACC_FREQ",
                f"ACC_FOREIGN_{i}",
                60000,
                (base_time + timedelta(hours=i * 12)).isoformat(),
                currency="USD",
                counterparty_country="US",
            )
            for i in range(6)
        ]
        results = _detect_cross_border(transactions)
        assert len(results) >= 5
        assert any("跨境频繁交易" in r["rule_hits"][0] for r in results)
        for r in results:
            assert r["risk_score"] >= 65

    def test_below_threshold_not_flagged(self):
        """少于5笔跨境交易不触发频繁检测"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = [
            _make_txn(
                f"TXN_CB_LOW_{i}",
                "ACC_LOW_FREQ",
                f"ACC_FOREIGN_{i}",
                60000,
                (base_time + timedelta(days=i)).isoformat(),
                currency="USD",
            )
            for i in range(3)
        ]
        results = _detect_cross_border(transactions)
        frequent_hits = [r for r in results if "跨境频繁交易" in r["rule_hits"][0]]
        assert len(frequent_hits) == 0


class TestCrossBorderSplit:
    """跨境分拆检测"""

    def test_cross_border_split_detected(self):
        """大额资金拆分为多笔均匀跨境转账被检测到"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        # 总额250000，拆分为5笔各50000，变异系数接近0
        transactions = [
            _make_txn(
                f"TXN_SPLIT_{i:03d}",
                "ACC_SPLIT",
                f"ACC_FOREIGN_{i}",
                50000,
                (base_time + timedelta(hours=i)).isoformat(),
                currency="USD",
                counterparty_country="US",
            )
            for i in range(5)
        ]
        results = _detect_cross_border(transactions)
        split_hits = [r for r in results if "跨境分拆" in r["rule_hits"][0]]
        assert len(split_hits) >= 1
        for r in split_hits:
            assert r["risk_score"] >= 80

    def test_uneven_amounts_not_split(self):
        """金额差异大的跨境交易不判定为分拆"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        amounts = [10000, 200000, 5000, 100000, 3000]  # 变异系数大
        transactions = [
            _make_txn(
                f"TXN_UNEVEN_{i}",
                "ACC_UNEVEN",
                f"ACC_FOREIGN_{i}",
                amounts[i],
                (base_time + timedelta(hours=i)).isoformat(),
                currency="USD",
            )
            for i in range(5)
        ]
        results = _detect_cross_border(transactions)
        split_hits = [r for r in results if "跨境分拆" in r["rule_hits"][0]]
        assert len(split_hits) == 0


class TestLargeFX:
    """大额换汇检测"""

    def test_large_fx_detected(self):
        """大额换汇交易被检测到"""
        transactions = [
            _make_txn(
                "TXN_FX_001",
                "ACC_FX",
                "ACC_FX_TARGET",
                100000,
                "2026-01-01T10:00:00",
                transaction_type="fx",
            ),
        ]
        results = _detect_cross_border(transactions)
        fx_hits = [r for r in results if "换汇" in r["rule_hits"][0]]
        assert len(fx_hits) >= 1
        assert fx_hits[0]["risk_score"] >= 65

    def test_small_fx_not_flagged(self):
        """小额换汇不触发检测"""
        transactions = [
            _make_txn(
                "TXN_FX_SMALL",
                "ACC_FX_SMALL",
                "ACC_FX_TARGET",
                30000,
                "2026-01-01T10:00:00",
                transaction_type="fx",
            ),
        ]
        results = _detect_cross_border(transactions)
        fx_hits = [r for r in results if "换汇" in r["rule_hits"][0]]
        assert len(fx_hits) == 0


class TestHighRiskRegion:
    """高风险地区跨境交易检测"""

    def test_high_risk_region_detected(self):
        """与避税天堂地区交易被检测到"""
        transactions = [
            _make_txn(
                "TXN_HR_001",
                "ACC_HR",
                "ACC_KY",
                100000,
                "2026-01-01T10:00:00",
                counterparty_country="KY",  # 开曼群岛
            ),
        ]
        results = _detect_cross_border(transactions)
        hr_hits = [r for r in results if "高风险地区" in r["rule_hits"][0]]
        assert len(hr_hits) >= 1
        assert hr_hits[0]["risk_score"] >= 80

    def test_normal_country_not_high_risk(self):
        """正常国家不触发高风险地区检测"""
        transactions = [
            _make_txn(
                "TXN_NORMAL_COUNTRY",
                "ACC_NC",
                "ACC_US",
                100000,
                "2026-01-01T10:00:00",
                counterparty_country="US",
            ),
        ]
        results = _detect_cross_border(transactions)
        hr_hits = [r for r in results if "高风险地区" in r["rule_hits"][0]]
        assert len(hr_hits) == 0


class TestNoFalsePositive:
    """不误报测试（戒律 P2）"""

    def test_domestic_transactions_not_flagged(self):
        """纯国内交易不触发跨境检测"""
        transactions = [
            _make_txn(
                "TXN_DOM_001",
                "ACC_CN_A",
                "ACC_CN_B",
                200000,
                "2026-01-01T10:00:00",
                remark="货款",
            ),
            _make_txn(
                "TXN_DOM_002",
                "ACC_CN_C",
                "ACC_CN_D",
                500000,
                "2026-01-01T11:00:00",
                remark="采购",
            ),
        ]
        results = _detect_cross_border(transactions)
        assert len(results) == 0

    def test_missing_fields_not_crash(self):
        """缺失字段不崩溃"""
        transactions = [
            {"transaction_id": "TXN_EMPTY", "amount": 100000},
        ]
        results = _detect_cross_border(transactions)
        assert len(results) == 0


class TestEvidenceChain:
    """证据链完整性（戒律 M4）"""

    def test_evidence_contains_account_id(self):
        """证据中包含账户ID"""
        transactions = [
            _make_txn(
                "TXN_EVID_001",
                "ACC_EVID_TEST",
                "ACC_KY",
                100000,
                "2026-01-01T10:00:00",
                counterparty_country="KY",
            ),
        ]
        results = _detect_cross_border(transactions)
        assert len(results) >= 1
        assert "ACC_EVID_TEST" in results[0]["evidence"][0]

    def test_evidence_contains_amount(self):
        """证据中包含金额信息"""
        transactions = [
            _make_txn(
                "TXN_EVID_AMT",
                "ACC_EVID_AMT",
                "ACC_VG",
                150000,
                "2026-01-01T10:00:00",
                counterparty_country="VG",
            ),
        ]
        results = _detect_cross_border(transactions)
        assert len(results) >= 1
        assert "150000" in results[0]["evidence"][0] or "150,000" in results[0]["evidence"][0]

    def test_evidence_contains_country(self):
        """证据中包含国家信息"""
        transactions = [
            _make_txn(
                "TXN_EVID_CTRY",
                "ACC_EVID_C",
                "ACC_PA",
                200000,
                "2026-01-01T10:00:00",
                counterparty_country="PA",
            ),
        ]
        results = _detect_cross_border(transactions)
        assert len(results) >= 1
        assert "PA" in results[0]["evidence"][0]


class TestRiskScoreRange:
    """风险分范围验证（戒律 M3）"""

    def test_all_scores_in_valid_range(self):
        """所有风险分在0-100范围内"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = [
            _make_txn(
                f"TXN_RANGE_{i}",
                "ACC_RANGE",
                f"ACC_HK_{i}",
                80000,
                (base_time + timedelta(hours=i)).isoformat(),
                counterparty_country="HK",
            )
            for i in range(6)
        ]
        results = _detect_cross_border(transactions)
        for r in results:
            assert 0 <= r["risk_score"] <= 100
