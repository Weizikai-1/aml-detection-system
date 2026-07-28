"""
制裁名单/黑名单检测测试

测试覆盖:
1. OFAC SDN名单匹配（名称精确匹配、包含匹配）
2. 央行关注名单匹配
3. 自定义黑名单添加/移除/匹配
4. 制裁国家交易匹配
5. 虚拟货币地址匹配
6. 批量交易检测
7. 无命中场景（不误报）
8. 证据链完整性
9. 风险分范围验证
"""
import os
import sys
import json
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.sanction_checker import SanctionChecker, sanction_checker


class TestSanctionChecker:
    """制裁名单检测测试"""

    def setup_method(self):
        """每个测试方法前创建独立的checker实例"""
        # 使用临时文件避免测试间状态污染
        self.tmpdir = tempfile.mkdtemp()
        self.checker = SanctionChecker()
        self.checker._storage_path = os.path.join(self.tmpdir, "custom_blacklist.json")
        self.checker._custom_blacklist = {}

    def teardown_method(self):
        """清理临时文件"""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestOFACMatching(TestSanctionChecker):
    """OFAC制裁名单匹配测试"""

    def test_ofac_exact_name_match(self):
        """精确名称匹配命中OFAC SDN"""
        result = self.checker.check_account(
            account_id="ACC001",
            account_name="Al-Zawahiri Network",
        )
        assert result is not None
        assert result["match_type"] == "ofac_sdn"
        assert result["risk_score"] == 95
        assert "OFAC SDN" in result["evidence"]
        assert "Al-Zawahiri Network" in result["evidence"]

    def test_ofac_case_insensitive_match(self):
        """大小写不敏感匹配"""
        result = self.checker.check_account(
            account_id="ACC002",
            account_name="al-zawahiri network",
        )
        assert result is not None
        assert result["match_type"] == "ofac_sdn"

    def test_ofac_partial_name_match(self):
        """部分名称匹配（实体名是账户名的子串）"""
        result = self.checker.check_account(
            account_id="ACC003",
            account_name="Al-Zawahiri Network Branch Beijing",
        )
        assert result is not None
        assert result["match_type"] == "ofac_sdn"

    def test_ofac_no_match_normal_name(self):
        """正常名称不命中"""
        result = self.checker.check_account(
            account_id="ACC004",
            account_name="Normal Trading Company",
        )
        assert result is None

    def test_ofac_north_korea_entity(self):
        """朝鲜制裁实体匹配"""
        result = self.checker.check_account(
            account_id="ACC005",
            account_name="North Korea Missile Procurement",
        )
        assert result is not None
        assert result["match_type"] == "ofac_sdn"
        assert result["risk_score"] == 95


class TestPBOCMatching(TestSanctionChecker):
    """央行关注名单匹配测试"""

    def test_pboc_exact_match(self):
        """央行名单精确匹配"""
        result = self.checker.check_account(
            account_id="ACC010",
            account_name="地下钱庄网络A",
        )
        assert result is not None
        assert result["match_type"] == "pboc_watchlist"
        assert result["risk_score"] == 90

    def test_pboc_partial_match(self):
        """央行名单部分匹配"""
        result = self.checker.check_account(
            account_id="ACC011",
            account_name="地下钱庄网络A的关联公司",
        )
        assert result is not None
        assert result["match_type"] == "pboc_watchlist"


class TestCustomBlacklist(TestSanctionChecker):
    """自定义黑名单测试"""

    def test_add_and_check_blacklist(self):
        """添加到黑名单并检测命中"""
        self.checker.add_to_blacklist("SUSPICIOUS_ACC", "涉嫌洗钱")
        result = self.checker.check_account(
            account_id="SUSPICIOUS_ACC",
            account_name="Test",
        )
        assert result is not None
        assert result["match_type"] == "custom_blacklist"
        assert result["risk_score"] == 95
        assert "涉嫌洗钱" in result["evidence"]

    def test_remove_from_blacklist(self):
        """从黑名单移除后不再命中"""
        self.checker.add_to_blacklist("TEMP_ACC", "临时标记")
        assert self.checker.check_account("TEMP_ACC", "Temp") is not None

        self.checker.remove_from_blacklist("TEMP_ACC")
        assert self.checker.check_account("TEMP_ACC", "Temp") is None

    def test_blacklist_persistence(self):
        """黑名单持久化保存和加载"""
        self.checker.add_to_blacklist("PERSIST_ACC", "持久化测试")
        # 重新创建checker，应该能加载已保存的黑名单
        new_checker = SanctionChecker()
        new_checker._storage_path = self.checker._storage_path
        new_checker._load_custom_blacklist()
        result = new_checker.check_account("PERSIST_ACC", "Test")
        assert result is not None
        assert result["match_type"] == "custom_blacklist"

    def test_empty_account_id_not_added(self):
        """空账户号不被添加到黑名单"""
        self.checker.add_to_blacklist("", "空账户")
        assert "" not in self.checker._custom_blacklist


class TestSanctionedCountry(TestSanctionChecker):
    """制裁国家匹配测试"""

    def test_north_korea_country_match(self):
        """朝鲜制裁国家匹配"""
        result = self.checker.check_account(
            account_id="ACC020",
            account_name="Normal Name",
            country="KP",
        )
        assert result is not None
        assert result["match_type"] == "sanctioned_country"
        assert result["risk_score"] == 90
        assert "KP" in result["evidence"]

    def test_iran_country_match(self):
        """伊朗制裁国家匹配"""
        result = self.checker.check_account(
            account_id="ACC021",
            account_name="Normal Name",
            country="IR",
        )
        assert result is not None
        assert result["match_type"] == "sanctioned_country"

    def test_normal_country_no_match(self):
        """正常国家不命中"""
        result = self.checker.check_account(
            account_id="ACC022",
            account_name="Normal Name",
            country="CN",
        )
        assert result is None or result["match_type"] != "sanctioned_country"

    def test_country_case_insensitive(self):
        """国家代码大小写不敏感"""
        result = self.checker.check_account(
            account_id="ACC023",
            account_name="Normal Name",
            country="kp",
        )
        assert result is not None
        assert result["match_type"] == "sanctioned_country"


class TestCryptoAddress(TestSanctionChecker):
    """虚拟货币地址匹配测试"""

    def test_btc_address_match(self):
        """比特币地址匹配"""
        result = self.checker.check_crypto_address(
            "bc1q5shngj24323nsrmxv99st02quy896kwtxvh9e2"
        )
        assert result is not None
        assert result["match_type"] == "crypto_sanction"
        assert result["risk_score"] == 95

    def test_eth_address_match(self):
        """以太坊地址匹配"""
        result = self.checker.check_crypto_address(
            "0x1234567890abcdef1234567890abcdef12345678"
        )
        assert result is not None
        assert result["match_type"] == "crypto_sanction"

    def test_normal_crypto_address_no_match(self):
        """正常虚拟货币地址不命中"""
        result = self.checker.check_crypto_address(
            "bc1qabcdefghijklmnopqrstuvwxyz1234567890"
        )
        assert result is None

    def test_empty_address_no_match(self):
        """空地址不命中"""
        assert self.checker.check_crypto_address("") is None
        assert self.checker.check_crypto_address(None) is None


class TestTransactionBatchCheck(TestSanctionChecker):
    """批量交易检测测试"""

    def test_batch_check_normal_transactions(self):
        """正常交易批量检测无命中"""
        transactions = [
            {
                "transaction_id": "TXN001",
                "from_account": "ACC_NORMAL_1",
                "to_account": "ACC_NORMAL_2",
                "amount": 50000,
                "remark": "货款",
            },
            {
                "transaction_id": "TXN002",
                "from_account": "ACC_NORMAL_3",
                "to_account": "ACC_NORMAL_4",
                "amount": 100000,
                "remark": "采购款",
            },
        ]
        hits = self.checker.check_transactions(transactions)
        assert len(hits) == 0

    def test_batch_check_ofac_hit(self):
        """批量检测中OFAC命中"""
        transactions = [
            {
                "transaction_id": "TXN003",
                "from_account": "ACC_NORMAL_5",
                "to_account": "Al-Zawahiri Network",
                "amount": 500000,
                "remark": "货款",
            },
        ]
        hits = self.checker.check_transactions(transactions)
        assert len(hits) == 1
        assert hits[0]["match_type"] == "ofac_sdn"
        assert hits[0]["matched_field"] == "to_account"
        assert hits[0]["risk_score"] == 95

    def test_batch_check_custom_blacklist_hit(self):
        """批量检测中自定义黑名单命中"""
        self.checker.add_to_blacklist("BLACKLISTED_ACC", "高风险")
        transactions = [
            {
                "transaction_id": "TXN004",
                "from_account": "BLACKLISTED_ACC",
                "to_account": "ACC_NORMAL_6",
                "amount": 300000,
                "remark": "转账",
            },
        ]
        hits = self.checker.check_transactions(transactions)
        assert len(hits) == 1
        assert hits[0]["match_type"] == "custom_blacklist"
        assert hits[0]["matched_field"] == "from_account"

    def test_batch_check_crypto_in_remark(self):
        """备注中包含制裁虚拟货币地址"""
        transactions = [
            {
                "transaction_id": "TXN005",
                "from_account": "ACC_A",
                "to_account": "ACC_B",
                "amount": 200000,
                "remark": "转账到 bc1q5shngj24323nsrmxv99st02quy896kwtxvh9e2",
            },
        ]
        hits = self.checker.check_transactions(transactions)
        assert len(hits) >= 1
        crypto_hit = [h for h in hits if h["match_type"] == "crypto_sanction"]
        assert len(crypto_hit) >= 1
        assert crypto_hit[0]["matched_field"] == "remark"

    def test_batch_check_country_in_transaction(self):
        """交易中包含制裁国家信息"""
        transactions = [
            {
                "transaction_id": "TXN006",
                "from_account": "ACC_C",
                "to_account": "ACC_D",
                "amount": 1000000,
                "remark": "跨境汇款",
                "counterparty_country": "KP",
            },
        ]
        hits = self.checker.check_transactions(transactions)
        country_hits = [h for h in hits if h["match_type"] == "sanctioned_country"]
        assert len(country_hits) >= 1
        assert country_hits[0]["matched_field"] == "counterparty_country"

    def test_batch_check_multiple_hits(self):
        """同一笔交易多个维度命中"""
        self.checker.add_to_blacklist("Al-Zawahiri Network", "黑名单+OFAC")
        transactions = [
            {
                "transaction_id": "TXN007",
                "from_account": "Al-Zawahiri Network",
                "to_account": "ACC_NORMAL",
                "amount": 500000,
                "remark": "转账",
            },
        ]
        hits = self.checker.check_transactions(transactions)
        # from_account既是自定义黑名单又匹配OFAC，应至少命中一次
        assert len(hits) >= 1
        # 风险分应≥90
        for hit in hits:
            assert hit["risk_score"] >= 90


class TestEvidenceChain(TestSanctionChecker):
    """证据链完整性测试（戒律 M4）"""

    def test_evidence_contains_account_id(self):
        """证据中包含账户ID"""
        self.checker.add_to_blacklist("ACC_EVIDENCE", "测试证据链")
        result = self.checker.check_account("ACC_EVIDENCE", "Test")
        assert "ACC_EVIDENCE" in result["evidence"]

    def test_evidence_contains_list_source(self):
        """证据中包含名单来源"""
        result = self.checker.check_account(
            account_id="ACC_OFAC",
            account_name="ISIS Financial Cell",
        )
        assert "OFAC" in result["evidence"]
        assert "ISIS Financial Cell" in result["evidence"]

    def test_evidence_contains_entity_id(self):
        """证据中包含实体ID"""
        result = self.checker.check_account(
            account_id="ACC_ENT",
            account_name="Sinaloa Cartel Front Company",
        )
        assert "OFAC-SDNT-001" in result["evidence"]


class TestRiskScoreRange(TestSanctionChecker):
    """风险分范围验证（戒律 M3）"""

    def test_all_risk_scores_in_valid_range(self):
        """所有命中风险分在0-100范围内"""
        test_cases = [
            ("Al-Zawahiri Network", "ACC_R1"),  # OFAC
            ("地下钱庄网络A", "ACC_R2"),          # 央行
            ("Normal", "ACC_R3", "KP"),          # 制裁国家
        ]

        for case in test_cases:
            name = case[0]
            acc_id = case[1]
            country = case[2] if len(case) > 2 else ""

            result = self.checker.check_account(acc_id, name, country)
            if result:
                assert 0 <= result["risk_score"] <= 100

    def test_custom_blacklist_add_to_blacklist_with_reason(self):
        """自定义黑名单带理由"""
        self.checker.add_to_blacklist("ACC_REASON", "涉嫌电信诈骗洗钱")
        result = self.checker.check_account("ACC_REASON", "Test")
        assert "涉嫌电信诈骗洗钱" in result["evidence"]


class TestStats(TestSanctionChecker):
    """统计功能测试"""

    def test_get_stats(self):
        """获取统计信息"""
        stats = self.checker.get_stats()
        assert "ofac_sdn_count" in stats
        assert "pboc_watchlist_count" in stats
        assert "custom_blacklist_count" in stats
        assert "sanctioned_countries_count" in stats
        assert "crypto_address_count" in stats
        assert stats["ofac_sdn_count"] > 0
        assert stats["sanctioned_countries_count"] > 0

    def test_stats_after_adding_blacklist(self):
        """添加黑名单后统计更新"""
        initial_count = self.checker.get_stats()["custom_blacklist_count"]
        self.checker.add_to_blacklist("STATS_TEST_ACC", "统计测试")
        new_count = self.checker.get_stats()["custom_blacklist_count"]
        assert new_count == initial_count + 1


class TestIntegrationWithRuleEngine:
    """与规则引擎集成测试"""

    def test_sanction_detection_in_rule_engine(self):
        """制裁名单检测在规则引擎中正常工作"""
        from agents.rule_engine import _detect_sanction_list

        transactions = [
            {
                "transaction_id": "TXN_SAN_001",
                "from_account": "Al-Zawahiri Network",
                "to_account": "ACC_NORMAL",
                "amount": 500000,
                "timestamp": "2026-01-01T10:00:00",
                "remark": "转账",
            },
        ]
        results = _detect_sanction_list(transactions)
        assert len(results) >= 1
        assert results[0]["risk_score"] >= 90
        assert "制裁名单" in results[0]["rule_hits"][0]

    def test_sanction_no_false_positive(self):
        """正常交易不触发制裁名单检测"""
        from agents.rule_engine import _detect_sanction_list

        transactions = [
            {
                "transaction_id": "TXN_NORMAL_001",
                "from_account": "ACC_NORMAL_A",
                "to_account": "ACC_NORMAL_B",
                "amount": 50000,
                "timestamp": "2026-01-01T10:00:00",
                "remark": "工资",
            },
        ]
        results = _detect_sanction_list(transactions)
        assert len(results) == 0
