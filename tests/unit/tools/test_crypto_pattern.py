"""
虚拟货币交易检测测试（B1-1）

测试覆盖:
1. 场外OTC模式检测（多对一汇聚→一对多分发）
2. 混币器特征检测（多笔小额进账+单笔大额出账）
3. 法币-虚拟货币兑换检测（关键词+高频中额）
4. 已知平台关联检测（备注关键词）
5. 正常交易不误报（P2 戒律）
6. 自转账不参与检测（P2 戒律）
7. 证据链完整性（M4 戒律）
8. 风险分范围验证（M3 戒律）
"""
import os
import sys
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.rule_engine import _detect_crypto_pattern


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


class TestOTCPattern:
    """场外OTC模式检测"""

    def test_otc_hub_pattern_detected(self):
        """3+笔汇聚 + 3+笔分发 + 资金流转比例≥50% 应被检测"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = []
        # 3个付款方汇聚到中心账户 HUB
        for i in range(3):
            transactions.append(_make_txn(
                f"OTC_IN_{i}",
                f"PAYER_{i}",
                "HUB",
                20000,
                (base_time + timedelta(hours=i)).isoformat(),
            ))
        # 中心账户分发到3个收款方（出账总额≥入账50%）
        for i in range(3):
            transactions.append(_make_txn(
                f"OTC_OUT_{i}",
                "HUB",
                f"PAYEE_{i}",
                18000,
                (base_time + timedelta(hours=5 + i)).isoformat(),
            ))
        results = _detect_crypto_pattern(transactions)
        # 应该标记OTC模式
        otc_hits = [r for r in results if "虚拟货币OTC" in r["rule_hits"][0]]
        assert len(otc_hits) >= 6  # 3入+3出
        for r in otc_hits:
            assert r["risk_score"] == 80
            assert "OTC模式" in r["evidence"][0]

    def test_otc_low_turnover_ratio_not_detected(self):
        """出账/入账比例<50% 不应被标记为OTC（戒律 P2: 不误报）"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = []
        # 3笔大额入账
        for i in range(3):
            transactions.append(_make_txn(
                f"OTC_IN_LOW_{i}",
                f"PAYER_LOW_{i}",
                "HUB_LOW",
                100000,
                (base_time + timedelta(hours=i)).isoformat(),
            ))
        # 出账金额很小（<50%），不算OTC模式
        for i in range(3):
            transactions.append(_make_txn(
                f"OTC_OUT_LOW_{i}",
                "HUB_LOW",
                f"PAYEE_LOW_{i}",
                1000,  # 总出账3000，入账300000，比例1%
                (base_time + timedelta(hours=5 + i)).isoformat(),
            ))
        results = _detect_crypto_pattern(transactions)
        otc_hits = [r for r in results if "虚拟货币OTC" in r["rule_hits"][0]]
        assert len(otc_hits) == 0

    def test_otc_insufficient_in_not_detected(self):
        """入账笔数不足不应触发OTC"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = [
            _make_txn("OTC_MIN_IN_1", "P1", "HUB", 20000, base_time.isoformat()),
            _make_txn("OTC_MIN_IN_2", "P2", "HUB", 20000, (base_time + timedelta(hours=1)).isoformat()),
            _make_txn("OTC_MIN_OUT_1", "HUB", "Q1", 18000, (base_time + timedelta(hours=3)).isoformat()),
            _make_txn("OTC_MIN_OUT_2", "HUB", "Q2", 18000, (base_time + timedelta(hours=4)).isoformat()),
            _make_txn("OTC_MIN_OUT_3", "HUB", "Q3", 18000, (base_time + timedelta(hours=5)).isoformat()),
        ]
        results = _detect_crypto_pattern(transactions)
        otc_hits = [r for r in results if "虚拟货币OTC" in r["rule_hits"][0]]
        assert len(otc_hits) == 0  # 入账只有2笔，不足3笔

    def test_otc_out_of_window_not_detected(self):
        """超出时间窗口不应触发OTC"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = []
        for i in range(3):
            transactions.append(_make_txn(
                f"OTC_W_IN_{i}",
                f"WP_{i}",
                "HUB_W",
                20000,
                base_time.isoformat(),
            ))
        # 出账在3天后（超过24小时窗口）
        for i in range(3):
            transactions.append(_make_txn(
                f"OTC_W_OUT_{i}",
                "HUB_W",
                f"WQ_{i}",
                18000,
                (base_time + timedelta(days=3, hours=i)).isoformat(),
            ))
        results = _detect_crypto_pattern(transactions)
        otc_hits = [r for r in results if "虚拟货币OTC" in r["rule_hits"][0]]
        assert len(otc_hits) == 0


class TestMixerPattern:
    """混币器特征检测"""

    def test_mixer_pattern_detected(self):
        """5+笔小额入账 + 大额出账 + 30分钟内 应被检测"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = []
        # 5笔小额入账（每笔8000元，≤10000）
        for i in range(5):
            transactions.append(_make_txn(
                f"MIX_IN_{i}",
                f"MIX_PAYER_{i}",
                "MIXER",
                8000,
                (base_time + timedelta(minutes=i * 3)).isoformat(),
            ))
        # 1笔大额出账（≥50000）
        transactions.append(_make_txn(
            "MIX_OUT",
            "MIXER",
            "MIX_PAYEE",
            60000,
            (base_time + timedelta(minutes=20)).isoformat(),
        ))
        results = _detect_crypto_pattern(transactions)
        mixer_hits = [r for r in results if "虚拟货币混币器" in r["rule_hits"][0]]
        assert len(mixer_hits) >= 6
        for r in mixer_hits:
            assert r["risk_score"] == 85
            assert "混币器" in r["evidence"][0]

    def test_mixer_large_in_not_detected(self):
        """入账单笔超过阈值不应触发混币器"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = []
        # 入账都是大额（>10000），不符合混币器特征
        for i in range(5):
            transactions.append(_make_txn(
                f"MIX_BIG_IN_{i}",
                f"BP_{i}",
                "BIG_MIXER",
                50000,
                (base_time + timedelta(minutes=i * 3)).isoformat(),
            ))
        transactions.append(_make_txn(
            "MIX_BIG_OUT",
            "BIG_MIXER",
            "BQ",
            200000,
            (base_time + timedelta(minutes=20)).isoformat(),
        ))
        results = _detect_crypto_pattern(transactions)
        mixer_hits = [r for r in results if "虚拟货币混币器" in r["rule_hits"][0]]
        assert len(mixer_hits) == 0

    def test_mixer_insufficient_in_count_not_detected(self):
        """入账笔数不足不应触发混币器"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = []
        for i in range(4):  # 4笔 < 5
            transactions.append(_make_txn(
                f"MIX_FEW_IN_{i}",
                f"FP_{i}",
                "FEW_MIXER",
                8000,
                (base_time + timedelta(minutes=i * 3)).isoformat(),
            ))
        transactions.append(_make_txn(
            "MIX_FEW_OUT",
            "FEW_MIXER",
            "FQ",
            60000,
            (base_time + timedelta(minutes=20)).isoformat(),
        ))
        results = _detect_crypto_pattern(transactions)
        mixer_hits = [r for r in results if "虚拟货币混币器" in r["rule_hits"][0]]
        assert len(mixer_hits) == 0

    def test_mixer_no_big_out_not_detected(self):
        """没有大额出账不应触发混币器"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = []
        for i in range(5):
            transactions.append(_make_txn(
                f"MIX_NOOUT_IN_{i}",
                f"NP_{i}",
                "NOOUT_MIXER",
                8000,
                (base_time + timedelta(minutes=i * 3)).isoformat(),
            ))
        # 出账都小于阈值
        transactions.append(_make_txn(
            "MIX_NOOUT_OUT",
            "NOOUT_MIXER",
            "NQ",
            30000,  # < 50000
            (base_time + timedelta(minutes=20)).isoformat(),
        ))
        results = _detect_crypto_pattern(transactions)
        mixer_hits = [r for r in results if "虚拟货币混币器" in r["rule_hits"][0]]
        assert len(mixer_hits) == 0


class TestFxExchange:
    """法币-虚拟货币兑换检测"""

    def test_fx_high_frequency_detected(self):
        """24小时内3+笔含兑换关键词 + 金额≥5000 应被检测"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = [
            _make_txn("FX_1", "TRADER", "ACC1", 10000,
                      base_time.isoformat(), remark="换U"),
            _make_txn("FX_2", "ACC1", "TRADER", 12000,
                      (base_time + timedelta(hours=2)).isoformat(), remark="出USDT"),
            _make_txn("FX_3", "TRADER", "ACC2", 8000,
                      (base_time + timedelta(hours=5)).isoformat(), remark="收u"),
        ]
        results = _detect_crypto_pattern(transactions)
        fx_hits = [r for r in results if "虚拟货币兑换" in r["rule_hits"][0]]
        assert len(fx_hits) >= 3
        for r in fx_hits:
            assert r["risk_score"] == 70

    def test_fx_low_frequency_not_detected(self):
        """只有1-2笔含关键词交易不应触发（戒律 P2）"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = [
            _make_txn("FX_LOW_1", "TRADER2", "ACC", 10000,
                      base_time.isoformat(), remark="换U"),
            _make_txn("FX_LOW_2", "ACC", "TRADER2", 12000,
                      (base_time + timedelta(hours=2)).isoformat(), remark="出USDT"),
        ]
        results = _detect_crypto_pattern(transactions)
        fx_hits = [r for r in results if "虚拟货币兑换" in r["rule_hits"][0]]
        assert len(fx_hits) == 0

    def test_fx_small_amount_not_detected(self):
        """金额低于阈值不应触发（避免小额测试误报）"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = [
            _make_txn("FX_SMALL_1", "TRADER3", "ACC", 1000,  # < 5000
                      base_time.isoformat(), remark="换U"),
            _make_txn("FX_SMALL_2", "ACC", "TRADER3", 2000,
                      (base_time + timedelta(hours=1)).isoformat(), remark="换U"),
            _make_txn("FX_SMALL_3", "TRADER3", "ACC", 3000,
                      (base_time + timedelta(hours=2)).isoformat(), remark="换U"),
        ]
        results = _detect_crypto_pattern(transactions)
        fx_hits = [r for r in results if "虚拟货币兑换" in r["rule_hits"][0]]
        assert len(fx_hits) == 0

    def test_fx_keyword_case_insensitive(self):
        """关键词匹配不区分大小写"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = [
            _make_txn("FX_CI_1", "TRADER_CI", "ACC", 10000,
                      base_time.isoformat(), remark="换u"),
            _make_txn("FX_CI_2", "ACC", "TRADER_CI", 12000,
                      (base_time + timedelta(hours=1)).isoformat(), remark="收U"),
            _make_txn("FX_CI_3", "TRADER_CI", "ACC", 8000,
                      (base_time + timedelta(hours=2)).isoformat(), remark="出Usdt"),
        ]
        results = _detect_crypto_pattern(transactions)
        fx_hits = [r for r in results if "虚拟货币兑换" in r["rule_hits"][0]]
        assert len(fx_hits) >= 3


class TestPlatformKeyword:
    """已知平台关联检测"""

    def test_platform_binance_detected(self):
        """备注含币安关键词 + 金额≥5000 应被检测"""
        txn = _make_txn(
            "PLAT_1", "ACC1", "ACC2", 10000,
            "2026-01-01T10:00:00",
            remark="binance充值",
        )
        results = _detect_crypto_pattern([txn])
        plat_hits = [r for r in results if "虚拟货币平台关联" in r["rule_hits"][0]]
        assert len(plat_hits) == 1
        assert plat_hits[0]["risk_score"] == 75
        assert "binance" in plat_hits[0]["evidence"][0]

    def test_platform_chinese_keyword_detected(self):
        """中文平台关键词命中"""
        txn = _make_txn(
            "PLAT_2", "ACC1", "ACC2", 10000,
            "2026-01-01T10:00:00",
            remark="提现到抹茶",
        )
        results = _detect_crypto_pattern([txn])
        plat_hits = [r for r in results if "虚拟货币平台关联" in r["rule_hits"][0]]
        assert len(plat_hits) == 1

    def test_platform_small_amount_not_detected(self):
        """平台关键词命中但金额不足不应触发"""
        txn = _make_txn(
            "PLAT_3", "ACC1", "ACC2", 1000,  # < 5000
            "2026-01-01T10:00:00",
            remark="binance测试",
        )
        results = _detect_crypto_pattern([txn])
        plat_hits = [r for r in results if "虚拟货币平台关联" in r["rule_hits"][0]]
        assert len(plat_hits) == 0

    def test_platform_no_keyword_not_detected(self):
        """无平台关键词不应触发"""
        txn = _make_txn(
            "PLAT_4", "ACC1", "ACC2", 10000,
            "2026-01-01T10:00:00",
            remark="正常转账",
        )
        results = _detect_crypto_pattern([txn])
        plat_hits = [r for r in results if "虚拟货币平台关联" in r["rule_hits"][0]]
        assert len(plat_hits) == 0


class TestNoFalsePositive:
    """正常交易不应误报（戒律 P2）"""

    def test_normal_transfers_not_detected(self):
        """正常工资/转账不应触发任何虚拟货币规则"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = [
            _make_txn("NORMAL_1", "EMPLOYER", "EMPLOYEE", 15000,
                      base_time.isoformat(), remark="工资"),
            _make_txn("NORMAL_2", "EMPLOYEE", "LANDLORD", 5000,
                      (base_time + timedelta(days=1)).isoformat(), remark="房租"),
            _make_txn("NORMAL_3", "BUYER", "SELLER", 30000,
                      (base_time + timedelta(days=2)).isoformat(), remark="货款"),
        ]
        results = _detect_crypto_pattern(transactions)
        assert len(results) == 0

    def test_self_transfer_not_detected(self):
        """自转账不参与虚拟货币检测（戒律 P2）"""
        txn = _make_txn(
            "SELF_1", "ACC1", "ACC1", 100000,
            "2026-01-01T10:00:00",
            remark="换U binance",
        )
        results = _detect_crypto_pattern([txn])
        assert len(results) == 0

    def test_missing_account_not_detected(self):
        """缺失账户字段的交易不应触发（戒律 M1: 不编造）"""
        txn = {
            "transaction_id": "NO_ACC",
            "amount": 100000,
            "timestamp": "2026-01-01T10:00:00",
            "remark": "换U binance",
        }
        results = _detect_crypto_pattern([txn])
        # 应该跳过或降级处理
        # 备注+金额匹配的话可能触发平台关联，但没有from/to账户不应触发
        plat_hits = [r for r in results if "虚拟货币平台关联" in r["rule_hits"][0]]
        assert len(plat_hits) == 0


class TestEvidenceChain:
    """证据链完整性（戒律 M4）"""

    def test_otc_evidence_contains_amounts(self):
        """OTC证据应包含入账/出账金额"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = []
        for i in range(3):
            transactions.append(_make_txn(
                f"EVID_OTC_IN_{i}",
                f"EP_{i}",
                "EVID_HUB",
                20000,
                (base_time + timedelta(hours=i)).isoformat(),
            ))
        for i in range(3):
            transactions.append(_make_txn(
                f"EVID_OTC_OUT_{i}",
                "EVID_HUB",
                f"EQ_{i}",
                18000,
                (base_time + timedelta(hours=5 + i)).isoformat(),
            ))
        results = _detect_crypto_pattern(transactions)
        otc_hits = [r for r in results if "虚拟货币OTC" in r["rule_hits"][0]]
        assert len(otc_hits) > 0
        evidence = otc_hits[0]["evidence"][0]
        # 证据应包含具体金额
        assert "60,000" in evidence  # 入账总额3*20000
        assert "54,000" in evidence  # 出账总额3*18000
        assert "EVID_HUB" in evidence  # 账户名

    def test_mixer_evidence_contains_thresholds(self):
        """混币器证据应包含金额阈值"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = []
        for i in range(5):
            transactions.append(_make_txn(
                f"EVID_MIX_IN_{i}",
                f"EMP_{i}",
                "EVID_MIXER",
                8000,
                (base_time + timedelta(minutes=i * 3)).isoformat(),
            ))
        transactions.append(_make_txn(
            "EVID_MIX_OUT",
            "EVID_MIXER",
            "EMQ",
            60000,
            (base_time + timedelta(minutes=20)).isoformat(),
        ))
        results = _detect_crypto_pattern(transactions)
        mixer_hits = [r for r in results if "虚拟货币混币器" in r["rule_hits"][0]]
        assert len(mixer_hits) > 0
        evidence = mixer_hits[0]["evidence"][0]
        # 证据应包含阈值信息
        assert "10,000" in evidence  # 入账单笔最大金额
        assert "50,000" in evidence  # 出账最小金额


class TestRiskScoreRange:
    """风险分范围验证（戒律 M3: 0-100）"""

    def test_all_risk_scores_in_range(self):
        """所有规则命中的风险分应在0-100范围内"""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        transactions = []
        # OTC模式
        for i in range(3):
            transactions.append(_make_txn(
                f"RANGE_OTC_IN_{i}", f"RP_{i}", "RANGE_HUB",
                20000, (base_time + timedelta(hours=i)).isoformat(),
            ))
        for i in range(3):
            transactions.append(_make_txn(
                f"RANGE_OTC_OUT_{i}", "RANGE_HUB", f"RQ_{i}",
                18000, (base_time + timedelta(hours=5 + i)).isoformat(),
            ))
        # 平台关联
        transactions.append(_make_txn(
            "RANGE_PLAT", "A", "B", 10000,
            base_time.isoformat(), remark="binance",
        ))

        results = _detect_crypto_pattern(transactions)
        for r in results:
            assert 0 <= r["risk_score"] <= 100
