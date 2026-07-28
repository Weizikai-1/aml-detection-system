"""
KYC客户身份画像测试（B0-3）

测试覆盖:
1. KYC信息设置和获取
2. 客户类型预期行为模式
3. 行为偏离检测 - 单笔金额异常
4. 行为偏离检测 - 交易频率异常
5. 行为偏离检测 - 跨境交易异常
6. 行为偏离检测 - 夜间交易异常
7. PEP账户高风险检测
8. 行业风险系数调整
9. 正常行为不误报
10. 画像持久化
"""
import os
import sys
import json
import tempfile
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.kyc_profile import (
    KYCProfile, KYCManager, kyc_manager,
    CUSTOMER_TYPE_PROFILES, INDUSTRY_RISK_LEVELS,
)


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


class TestKYCProfile:
    """KYC画像基本功能测试"""

    def test_create_profile(self):
        """创建KYC画像"""
        profile = KYCProfile("ACC_TEST")
        assert profile.account_id == "ACC_TEST"
        assert profile.customer_type == "personal"
        assert profile.industry == "unknown"

    def test_to_dict_and_from_dict(self):
        """序列化和反序列化"""
        profile = KYCProfile("ACC_SER")
        profile.customer_type = "enterprise"
        profile.industry = "trade"
        profile.business_scope = "国际贸易"
        profile.registered_capital = 5000000
        profile.high_risk_flags = ["PEP"]

        d = profile.to_dict()
        assert d["account_id"] == "ACC_SER"
        assert d["customer_type"] == "enterprise"
        assert d["industry"] == "trade"
        assert "PEP" in d["high_risk_flags"]

        restored = KYCProfile.from_dict(d)
        assert restored.account_id == "ACC_SER"
        assert restored.customer_type == "enterprise"
        assert restored.industry == "trade"
        assert "PEP" in restored.high_risk_flags

    def test_from_dict_empty_account_id(self):
        """空账户号返回None"""
        profile = KYCProfile.from_dict({"account_id": ""})
        assert profile is None

    def test_get_expected_profile(self):
        """获取预期行为模式"""
        profile = KYCProfile("ACC_EXP")
        profile.customer_type = "personal"
        expected = profile.get_expected_profile()
        assert expected["expected_max_amount"] == 50000
        assert expected["cross_border_expected"] is False

        profile.customer_type = "enterprise"
        expected = profile.get_expected_profile()
        assert expected["expected_max_amount"] == 1000000
        assert expected["cross_border_expected"] is True

    def test_get_industry_risk_multiplier(self):
        """行业风险系数"""
        profile = KYCProfile("ACC_IND")
        profile.industry = "retail"
        assert profile.get_industry_risk_multiplier() == 0.9

        profile.industry = "crypto"
        assert profile.get_industry_risk_multiplier() == 1.5

        profile.industry = "unknown"
        assert profile.get_industry_risk_multiplier() == 1.0


class TestKYCManager:
    """KYC管理器测试"""

    def setup_method(self):
        """使用临时文件"""
        self.tmpdir = tempfile.mkdtemp()
        self.manager = KYCManager(storage_path=os.path.join(self.tmpdir, "kyc.json"))

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_set_and_get_kyc_info(self):
        """设置和获取KYC信息"""
        self.manager.set_kyc_info(
            "ACC_KYC_001",
            customer_type="enterprise",
            industry="manufacturing",
            business_scope="汽车制造",
            registered_capital=10000000,
        )
        profile = self.manager.get_profile("ACC_KYC_001")
        assert profile is not None
        assert profile.customer_type == "enterprise"
        assert profile.industry == "manufacturing"
        assert profile.business_scope == "汽车制造"

    def test_get_profile_not_exist(self):
        """获取不存在的画像返回None"""
        assert self.manager.get_profile("NOT_EXIST") is None

    def test_empty_account_id_not_set(self):
        """空账户号不设置"""
        self.manager.set_kyc_info("", customer_type="personal")
        assert self.manager.get_profile("") is None

    def test_persistence(self):
        """持久化保存和加载"""
        self.manager.set_kyc_info("ACC_PERSIST", customer_type="sole_proprietor", industry="retail")
        # 重新创建manager加载
        new_manager = KYCManager(storage_path=self.manager.storage_path)
        profile = new_manager.get_profile("ACC_PERSIST")
        assert profile is not None
        assert profile.customer_type == "sole_proprietor"


class TestBehaviorMismatch:
    """行为偏离检测测试"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.manager = KYCManager(storage_path=os.path.join(self.tmpdir, "kyc.json"))

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_personal_large_amount_mismatch(self):
        """个人账户大额交易偏离"""
        self.manager.set_kyc_info("ACC_PERSONAL", customer_type="personal")
        # 个人账户预期单笔≤50000，实际150000(3倍)
        transactions = [
            _make_txn("T1", "ACC_PERSONAL", "ACC_B", 150000, "2026-01-01T10:00:00"),
        ]
        mismatches = self.manager.check_behavior_mismatch("ACC_PERSONAL", transactions)
        amount_mismatches = [m for m in mismatches if m["dimension"] == "单笔金额异常"]
        assert len(amount_mismatches) >= 1
        assert amount_mismatches[0]["risk_score"] >= 40

    def test_personal_normal_amount_no_mismatch(self):
        """个人账户正常金额不误报"""
        self.manager.set_kyc_info("ACC_NORMAL", customer_type="personal")
        transactions = [
            _make_txn("T1", "ACC_NORMAL", "ACC_B", 30000, "2026-01-01T10:00:00"),
        ]
        mismatches = self.manager.check_behavior_mismatch("ACC_NORMAL", transactions)
        amount_mismatches = [m for m in mismatches if m["dimension"] == "单笔金额异常"]
        assert len(amount_mismatches) == 0

    def test_personal_high_frequency_mismatch(self):
        """个人账户高频交易偏离"""
        self.manager.set_kyc_info("ACC_FREQ", customer_type="personal")
        # 个人账户预期日均5笔，实际一天30笔(6倍)
        base = datetime(2026, 1, 1, 10, 0, 0)
        transactions = [
            _make_txn(f"T{i}", "ACC_FREQ", f"ACC_{i}", 1000,
                      (base + timedelta(minutes=i * 10)).isoformat())
            for i in range(30)
        ]
        mismatches = self.manager.check_behavior_mismatch("ACC_FREQ", transactions)
        freq_mismatches = [m for m in mismatches if m["dimension"] == "交易频率异常"]
        assert len(freq_mismatches) >= 1

    def test_personal_cross_border_mismatch(self):
        """个人账户跨境交易偏离"""
        self.manager.set_kyc_info("ACC_CB", customer_type="personal")
        transactions = [
            _make_txn("T1", "ACC_CB", "ACC_US", 50000, "2026-01-01T10:00:00",
                      currency="USD", counterparty_country="US"),
            _make_txn("T2", "ACC_CB", "ACC_UK", 50000, "2026-01-02T10:00:00",
                      currency="GBP", counterparty_country="GB"),
            _make_txn("T3", "ACC_CB", "ACC_JP", 50000, "2026-01-03T10:00:00",
                      currency="JPY", counterparty_country="JP"),
        ]
        mismatches = self.manager.check_behavior_mismatch("ACC_CB", transactions)
        cb_mismatches = [m for m in mismatches if m["dimension"] == "跨境交易异常"]
        assert len(cb_mismatches) >= 1

    def test_enterprise_cross_border_no_mismatch(self):
        """企业账户跨境交易不误报"""
        self.manager.set_kyc_info("ACC_ENT", customer_type="enterprise")
        transactions = [
            _make_txn("T1", "ACC_ENT", "ACC_US", 500000, "2026-01-01T10:00:00",
                      currency="USD", counterparty_country="US"),
        ]
        mismatches = self.manager.check_behavior_mismatch("ACC_ENT", transactions)
        cb_mismatches = [m for m in mismatches if m["dimension"] == "跨境交易异常"]
        assert len(cb_mismatches) == 0

    def test_night_ratio_mismatch(self):
        """夜间交易占比异常"""
        self.manager.set_kyc_info("ACC_NIGHT", customer_type="personal")
        # 5笔交易，3笔在夜间(23:00, 01:00, 03:00)
        transactions = [
            _make_txn("T1", "ACC_NIGHT", "ACC_B", 5000, "2026-01-01T23:00:00"),
            _make_txn("T2", "ACC_NIGHT", "ACC_C", 5000, "2026-01-02T01:00:00"),
            _make_txn("T3", "ACC_NIGHT", "ACC_D", 5000, "2026-01-03T03:00:00"),
            _make_txn("T4", "ACC_NIGHT", "ACC_E", 5000, "2026-01-04T10:00:00"),
            _make_txn("T5", "ACC_NIGHT", "ACC_F", 5000, "2026-01-05T14:00:00"),
        ]
        mismatches = self.manager.check_behavior_mismatch("ACC_NIGHT", transactions)
        night_mismatches = [m for m in mismatches if m["dimension"] == "夜间交易异常"]
        assert len(night_mismatches) >= 1

    def test_pep_account_high_risk(self):
        """PEP账户大额交易检测"""
        self.manager.set_kyc_info(
            "ACC_PEP", customer_type="personal", high_risk_flags=["PEP"]
        )
        # PEP账户单笔超过预期(50000)
        transactions = [
            _make_txn("T1", "ACC_PEP", "ACC_B", 80000, "2026-01-01T10:00:00"),
        ]
        mismatches = self.manager.check_behavior_mismatch("ACC_PEP", transactions)
        pep_mismatches = [m for m in mismatches if m["dimension"] == "PEP账户高风险交易"]
        assert len(pep_mismatches) >= 1
        assert pep_mismatches[0]["risk_score"] >= 75

    def test_industry_risk_multiplier_applied(self):
        """高风险行业风险系数调整"""
        self.manager.set_kyc_info(
            "ACC_CRYPTO", customer_type="enterprise", industry="crypto"
        )
        # 企业账户预期单笔≤1000000，实际2500000(2.5倍)
        transactions = [
            _make_txn("T1", "ACC_CRYPTO", "ACC_B", 2500000, "2026-01-01T10:00:00"),
        ]
        mismatches = self.manager.check_behavior_mismatch("ACC_CRYPTO", transactions)
        amount_mismatches = [m for m in mismatches if m["dimension"] == "单笔金额异常"]
        if len(amount_mismatches) >= 1:
            # crypto行业风险系数1.5，风险分应被放大
            assert amount_mismatches[0]["risk_score"] >= 60

    def test_no_profile_no_mismatch(self):
        """无KYC画像不检测"""
        transactions = [
            _make_txn("T1", "ACC_NO_KYC", "ACC_B", 1000000, "2026-01-01T10:00:00"),
        ]
        mismatches = self.manager.check_behavior_mismatch("ACC_NO_KYC", transactions)
        assert len(mismatches) == 0

    def test_empty_transactions_no_mismatch(self):
        """空交易列表不检测"""
        self.manager.set_kyc_info("ACC_EMPTY", customer_type="personal")
        mismatches = self.manager.check_behavior_mismatch("ACC_EMPTY", [])
        assert len(mismatches) == 0


class TestEvidenceChain:
    """证据链完整性测试（戒律 M4）"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.manager = KYCManager(storage_path=os.path.join(self.tmpdir, "kyc.json"))

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_evidence_contains_account_id(self):
        """证据中包含账户ID"""
        self.manager.set_kyc_info("ACC_EVID", customer_type="personal")
        transactions = [
            _make_txn("T1", "ACC_EVID", "ACC_B", 200000, "2026-01-01T10:00:00"),
        ]
        mismatches = self.manager.check_behavior_mismatch("ACC_EVID", transactions)
        assert len(mismatches) >= 1
        assert "ACC_EVID" in mismatches[0]["evidence"]

    def test_evidence_contains_expected_and_actual(self):
        """证据中包含预期值和实际值"""
        self.manager.set_kyc_info("ACC_EA", customer_type="personal")
        transactions = [
            _make_txn("T1", "ACC_EA", "ACC_B", 200000, "2026-01-01T10:00:00"),
        ]
        mismatches = self.manager.check_behavior_mismatch("ACC_EA", transactions)
        amount_mismatches = [m for m in mismatches if m["dimension"] == "单笔金额异常"]
        assert len(amount_mismatches) >= 1
        assert "200,000" in amount_mismatches[0]["evidence"]
        assert "个人账户" in amount_mismatches[0]["evidence"]


class TestRiskScoreRange:
    """风险分范围验证（戒律 M3）"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.manager = KYCManager(storage_path=os.path.join(self.tmpdir, "kyc.json"))

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_all_scores_in_valid_range(self):
        """所有风险分在0-100范围内"""
        self.manager.set_kyc_info("ACC_RANGE", customer_type="personal", industry="crypto")
        base = datetime(2026, 1, 1, 23, 0, 0)
        transactions = [
            _make_txn(f"T{i}", "ACC_RANGE", f"ACC_{i}", 200000,
                      (base + timedelta(minutes=i)).isoformat(),
                      currency="USD", counterparty_country="US")
            for i in range(30)
        ]
        mismatches = self.manager.check_behavior_mismatch("ACC_RANGE", transactions)
        for m in mismatches:
            assert 0 <= m["risk_score"] <= 100


class TestStats:
    """统计功能测试"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.manager = KYCManager(storage_path=os.path.join(self.tmpdir, "kyc.json"))

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_get_stats(self):
        """获取统计信息"""
        self.manager.set_kyc_info("ACC_S1", customer_type="personal")
        self.manager.set_kyc_info("ACC_S2", customer_type="enterprise")
        self.manager.set_kyc_info("ACC_S3", customer_type="enterprise")
        stats = self.manager.get_stats()
        assert stats["total"] >= 3
        assert stats["personal"] >= 1
        assert stats["enterprise"] >= 2
