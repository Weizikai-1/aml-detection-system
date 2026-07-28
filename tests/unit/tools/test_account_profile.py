"""
账户风险画像单元测试
"""
import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.account_profile import AccountRiskProfile, AccountProfileManager


class TestAccountRiskProfile:
    def test_new_profile_defaults(self):
        """新创建的画像有正确的默认值"""
        p = AccountRiskProfile("A001")
        assert p.account_id == "A001"
        assert p.total_suspicious_hits == 0
        assert p.highest_risk_score == 0
        assert p.risk_trend == "stable"

    def test_risk_multiplier_clean(self):
        """历史清白且交易多的账户，风险系数降低"""
        p = AccountRiskProfile("A001")
        p.total_transactions = 50
        p.total_suspicious_hits = 0
        assert p.get_risk_multiplier() == 0.9

    def test_risk_multiplier_clean_few_txns(self):
        """交易笔数少的账户不放宽（基线不可靠）"""
        p = AccountRiskProfile("A001")
        p.total_transactions = 5
        p.total_suspicious_hits = 0
        assert p.get_risk_multiplier() == 1.0

    def test_risk_multiplier_normal(self):
        """1-2次可疑的账户系数正常"""
        p = AccountRiskProfile("A001")
        p.total_suspicious_hits = 1
        assert p.get_risk_multiplier() == 1.0
        p.total_suspicious_hits = 2
        assert p.get_risk_multiplier() == 1.0

    def test_risk_multiplier_recidivist(self):
        """3-5次可疑账户加成"""
        p = AccountRiskProfile("A001")
        p.total_suspicious_hits = 3
        assert p.get_risk_multiplier() == 1.15
        p.total_suspicious_hits = 5
        assert p.get_risk_multiplier() == 1.15

    def test_risk_multiplier_high(self):
        """5次以上可疑账户高加成"""
        p = AccountRiskProfile("A001")
        p.total_suspicious_hits = 6
        assert p.get_risk_multiplier() == 1.3

    def test_is_high_risk_recidivist(self):
        """高风险累犯判定"""
        p = AccountRiskProfile("A001")
        p.total_suspicious_hits = 3
        p.highest_risk_score = 75
        assert p.is_high_risk_recidivist() is True

    def test_is_not_high_risk_low_score(self):
        """次数够但分数不够，不算高风险累犯"""
        p = AccountRiskProfile("A001")
        p.total_suspicious_hits = 3
        p.highest_risk_score = 60
        assert p.is_high_risk_recidivist() is False

    def test_to_dict_and_back(self):
        """序列化和反序列化一致"""
        p = AccountRiskProfile("A001")
        p.total_suspicious_hits = 3
        p.highest_risk_score = 75
        p.suspicious_patterns = {"分拆转账": 2, "快进快出": 1}

        data = p.to_dict()
        p2 = AccountRiskProfile.from_dict(data)

        assert p2.account_id == "A001"
        assert p2.total_suspicious_hits == 3
        assert p2.highest_risk_score == 75
        assert p2.suspicious_patterns["分拆转账"] == 2


class TestAccountProfileManager:
    def _make_tmp_path(self) -> str:
        f = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        f.close()
        return f.name

    def test_get_new_profile(self):
        """获取不存在的账户会创建新画像"""
        mgr = AccountProfileManager("")
        p = mgr.get_profile("NEW_ACC")
        assert p.account_id == "NEW_ACC"
        assert p.total_suspicious_hits == 0

    def test_save_and_load(self):
        """保存后再加载数据一致"""
        path = self._make_tmp_path()
        try:
            mgr = AccountProfileManager(path)
            p = mgr.get_profile("A001")
            p.total_suspicious_hits = 5
            p.highest_risk_score = 80
            mgr.save()

            mgr2 = AccountProfileManager(path)
            p2 = mgr2.get_profile("A001")
            assert p2.total_suspicious_hits == 5
            assert p2.highest_risk_score == 80
        finally:
            os.unlink(path)

    def test_update_from_suspicious(self):
        """从可疑交易列表更新画像"""
        mgr = AccountProfileManager("")
        suspicious = [
            {
                "transaction": {"from_account": "A1", "to_account": "A2", "amount": 50000},
                "rule_hits": ["分拆转账"],
                "risk_score": 70,
            },
            {
                "transaction": {"from_account": "A1", "to_account": "A3", "amount": 45000},
                "rule_hits": ["分拆转账"],
                "risk_score": 70,
            },
        ]
        mgr.update_from_suspicious(suspicious)

        # A1 出现在 2 笔可疑中
        p1 = mgr.get_profile("A1")
        assert p1.total_suspicious_hits == 2

        # A2 出现在 1 笔可疑中
        p2 = mgr.get_profile("A2")
        assert p2.total_suspicious_hits == 1

        # A3 出现在 1 笔可疑中
        p3 = mgr.get_profile("A3")
        assert p3.total_suspicious_hits == 1

    def test_get_high_risk_accounts(self):
        """获取高风险账户列表"""
        mgr = AccountProfileManager("")
        p1 = mgr.get_profile("HIGH_RISK")
        p1.total_suspicious_hits = 4
        p1.highest_risk_score = 75

        p2 = mgr.get_profile("LOW_RISK")
        p2.total_suspicious_hits = 1
        p2.highest_risk_score = 50

        high_risk = mgr.get_high_risk_accounts()
        assert len(high_risk) == 1
        assert high_risk[0].account_id == "HIGH_RISK"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
