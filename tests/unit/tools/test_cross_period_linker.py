"""
案件跨期串联分析测试（B1-3）

测试覆盖:
1. 账户级串联检测
2. 团伙级串联检测
3. 模式级串联检测（含相似度阈值）
4. 时间窗口验证（戒律 P2: 时间太远不串联）
5. 自身记录排除
6. 无历史记录处理
7. 空数据不误报
8. 风险加成应用
9. 证据链完整性（M4 戒律）
10. 报告更新验证
"""
import os
import sys
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.cross_period_linker import CrossPeriodLinker, cross_period_linker, CROSS_PERIOD_CONFIG


def _make_txn(txn_id, from_acc, to_acc, amount, timestamp):
    return {
        "transaction_id": txn_id,
        "from_account": from_acc,
        "to_account": to_acc,
        "amount": amount,
        "timestamp": timestamp,
    }


def _make_history_record(
    execution_id, timestamp, primary_accounts, rule_details=None, str_reports=None
):
    """构造历史记录"""
    record = {
        "execution_id": execution_id,
        "timestamp": timestamp,
        "primary_accounts": primary_accounts,
        "rule_details": rule_details or {},
        "str_reports": str_reports or [],
    }
    return record


def _make_current_state(
    execution_id, analysis_date, rule_hits=None, rule_details=None, reports=None
):
    """构造当前批次状态"""
    return {
        "execution_id": execution_id,
        "analysis_date": analysis_date,
        "rule_hits": rule_hits or [],
        "rule_details": rule_details or {},
        "final_reports": reports or [],
        "str_reports": reports or [],
    }


class TestAccountLink:
    """账户级串联检测"""

    def test_account_overlap_detected(self):
        """当前批次可疑账户与历史案件账户重叠应串联"""
        current = _make_current_state(
            "CURRENT_1", "2026-01-15",
            rule_hits=[{
                "transaction": _make_txn("T1", "ACC_SUSPECT", "B", 50000, "2026-01-15T10:00:00"),
                "rule_hits": ["大额交易"],
                "risk_score": 70,
                "evidence": ["大额"],
            }],
        )
        history = [_make_history_record(
            "HIST_1", "2026-01-01", primary_accounts=["ACC_SUSPECT"]
        )]
        links = cross_period_linker.link_current_to_history(current, history)
        account_links = [l for l in links if l["link_type"] == "account"]
        assert len(account_links) == 1
        assert account_links[0]["history_execution_id"] == "HIST_1"
        assert "ACC_SUSPECT" in account_links[0]["overlap_entities"]
        assert account_links[0]["risk_score_bonus"] == 20

    def test_no_account_overlap_not_linked(self):
        """账户无重叠不应串联"""
        current = _make_current_state(
            "CURRENT_2", "2026-01-15",
            rule_hits=[{
                "transaction": _make_txn("T1", "ACC_NEW", "B", 50000, "2026-01-15T10:00:00"),
                "rule_hits": ["大额交易"],
                "risk_score": 70,
                "evidence": ["大额"],
            }],
        )
        history = [_make_history_record(
            "HIST_2", "2026-01-01", primary_accounts=["ACC_OTHER"]
        )]
        links = cross_period_linker.link_current_to_history(current, history)
        account_links = [l for l in links if l["link_type"] == "account"]
        assert len(account_links) == 0


class TestCommunityLink:
    """团伙级串联检测"""

    def test_community_overlap_detected(self):
        """团伙成员重叠≥2应串联"""
        current = _make_current_state(
            "CURRENT_3", "2026-01-15",
        )
        current["graph_data"] = {
            "communities": [
                ["M1", "M2", "M3", "M4"],  # 当前团伙
            ]
        }
        history = [_make_history_record(
            "HIST_3", "2026-01-01",
            primary_accounts=[],
            str_reports=[{
                "primary_account": "M1",
                "related_accounts": ["M2", "M5", "M6"],
            }],
        )]
        links = cross_period_linker.link_current_to_history(current, history)
        community_links = [l for l in links if l["link_type"] == "community"]
        # M1, M2 重叠（2个），满足 min_community_overlap=2
        assert len(community_links) == 1
        assert "M1" in community_links[0]["overlap_entities"]
        assert "M2" in community_links[0]["overlap_entities"]

    def test_insufficient_overlap_not_linked(self):
        """团伙成员重叠<2不应串联"""
        current = _make_current_state(
            "CURRENT_4", "2026-01-15",
        )
        current["graph_data"] = {
            "communities": [
                ["M1", "M2", "M3"],  # 当前团伙
            ]
        }
        history = [_make_history_record(
            "HIST_4", "2026-01-01",
            primary_accounts=[],
            str_reports=[{
                "primary_account": "M1",  # 只1个重叠
                "related_accounts": ["M5", "M6"],
            }],
        )]
        links = cross_period_linker.link_current_to_history(current, history)
        community_links = [l for l in links if l["link_type"] == "community"]
        assert len(community_links) == 0


class TestPatternLink:
    """模式级串联检测"""

    def test_pattern_similarity_detected(self):
        """规则命中模式相似度≥70%且时间临近应串联"""
        current = _make_current_state(
            "CURRENT_5", "2026-01-15",
            rule_details={"分拆转账": 5, "快进快出": 3, "大额交易": 2, "对敲交易": 1},
        )
        history = [_make_history_record(
            "HIST_5", "2026-01-10",  # 5天内，时间临近
            primary_accounts=[],
            rule_details={"分拆转账": 4, "快进快出": 2, "大额交易": 3, "对敲交易": 1, "备注关键词": 1},
        )]
        links = cross_period_linker.link_current_to_history(current, history)
        pattern_links = [l for l in links if l["link_type"] == "pattern"]
        # 共同规则4个，并集5个，相似度80% ≥ 70%
        assert len(pattern_links) == 1
        assert pattern_links[0]["similarity"] >= 0.7

    def test_low_similarity_not_linked(self):
        """模式相似度<70%不应串联"""
        current = _make_current_state(
            "CURRENT_6", "2026-01-15",
            rule_details={"分拆转账": 5, "快进快出": 3},
        )
        history = [_make_history_record(
            "HIST_6", "2026-01-10",
            primary_accounts=[],
            rule_details={"大额交易": 2, "对敲交易": 1, "备注关键词": 1, "空壳公司": 1},
        )]
        links = cross_period_linker.link_current_to_history(current, history)
        pattern_links = [l for l in links if l["link_type"] == "pattern"]
        # 共同规则0个，相似度0%
        assert len(pattern_links) == 0

    def test_pattern_far_time_not_linked(self):
        """模式相似但时间太远不应串联（戒律 P2）"""
        current = _make_current_state(
            "CURRENT_7", "2026-06-15",
            rule_details={"分拆转账": 5, "快进快出": 3, "大额交易": 2},
        )
        history = [_make_history_record(
            "HIST_7", "2026-01-01",  # 5个月前，超过30天窗口
            primary_accounts=[],
            rule_details={"分拆转账": 4, "快进快出": 2, "大额交易": 3},
        )]
        links = cross_period_linker.link_current_to_history(current, history)
        pattern_links = [l for l in links if l["link_type"] == "pattern"]
        # 时间太远，不串联
        assert len(pattern_links) == 0


class TestSelfExclusion:
    """自身记录排除"""

    def test_self_record_excluded(self):
        """相同 execution_id 不应串联到自身"""
        current = _make_current_state(
            "SAME_ID", "2026-01-15",
            rule_hits=[{
                "transaction": _make_txn("T1", "ACC_X", "B", 50000, "2026-01-15T10:00:00"),
                "rule_hits": ["大额交易"],
                "risk_score": 70,
                "evidence": ["大额"],
            }],
        )
        history = [_make_history_record(
            "SAME_ID", "2026-01-01", primary_accounts=["ACC_X"]
        )]
        links = cross_period_linker.link_current_to_history(current, history)
        assert len(links) == 0


class TestEmptyData:
    """空数据不误报（戒律 P2）"""

    def test_no_history_no_links(self):
        """无历史记录应返回空"""
        current = _make_current_state("CURRENT_8", "2026-01-15")
        links = cross_period_linker.link_current_to_history(current, [])
        assert len(links) == 0

    def test_no_current_accounts_no_account_links(self):
        """当前批次无账户不应有账户级串联"""
        current = _make_current_state("CURRENT_9", "2026-01-15")
        history = [_make_history_record(
            "HIST_9", "2026-01-01", primary_accounts=["ACC_HIST"]
        )]
        links = cross_period_linker.link_current_to_history(current, history)
        account_links = [l for l in links if l["link_type"] == "account"]
        assert len(account_links) == 0

    def test_disabled_returns_empty(self):
        """配置禁用应返回空"""
        linker = CrossPeriodLinker(config={"enabled": False})
        current = _make_current_state(
            "CURRENT_10", "2026-01-15",
            rule_hits=[{
                "transaction": _make_txn("T1", "ACC_Y", "B", 50000, "2026-01-15T10:00:00"),
                "rule_hits": ["大额交易"],
                "risk_score": 70,
                "evidence": ["大额"],
            }],
        )
        history = [_make_history_record(
            "HIST_10", "2026-01-01", primary_accounts=["ACC_Y"]
        )]
        links = linker.link_current_to_history(current, history)
        assert len(links) == 0


class TestEvidenceChain:
    """证据链完整性（戒律 M4）"""

    def test_link_contains_history_id(self):
        """关联链接应包含历史案件ID"""
        current = _make_current_state(
            "CURRENT_11", "2026-01-15",
            rule_hits=[{
                "transaction": _make_txn("T1", "ACC_Z", "B", 50000, "2026-01-15T10:00:00"),
                "rule_hits": ["大额交易"],
                "risk_score": 70,
                "evidence": ["大额"],
            }],
        )
        history = [_make_history_record(
            "HIST_11", "2026-01-01", primary_accounts=["ACC_Z"]
        )]
        links = cross_period_linker.link_current_to_history(current, history)
        assert len(links) > 0
        for link in links:
            assert link["history_execution_id"] == "HIST_11"
            assert link["history_timestamp"] == "2026-01-01"
            assert "reason" in link
            assert "risk_score_bonus" in link

    def test_link_contains_overlap_entities(self):
        """关联链接应包含重叠实体列表"""
        current = _make_current_state(
            "CURRENT_12", "2026-01-15",
            rule_hits=[{
                "transaction": _make_txn("T1", "ACC_OVERLAP", "B", 50000, "2026-01-15T10:00:00"),
                "rule_hits": ["大额交易"],
                "risk_score": 70,
                "evidence": ["大额"],
            }],
        )
        history = [_make_history_record(
            "HIST_12", "2026-01-01", primary_accounts=["ACC_OVERLAP"]
        )]
        links = cross_period_linker.link_current_to_history(current, history)
        account_links = [l for l in links if l["link_type"] == "account"]
        assert len(account_links) == 1
        assert "ACC_OVERLAP" in account_links[0]["overlap_entities"]
        assert "ACC_OVERLAP" in account_links[0]["current_entities"]
        assert "ACC_OVERLAP" in account_links[0]["history_entities"]


class TestApplyLinksToReports:
    """报告更新验证"""

    def test_apply_links_updates_evidence_chain(self):
        """应用关联应更新报告证据链"""
        reports = [{
            "report_id": "RPT_1",
            "primary_account": "ACC_LINKED",
            "related_accounts": [],
            "evidence_chain": ["原始证据"],
            "analysis_summary": "原始摘要",
        }]
        links = [{
            "link_type": "account",
            "history_execution_id": "HIST_13",
            "history_timestamp": "2026-01-01",
            "current_entities": ["ACC_LINKED"],
            "history_entities": ["ACC_LINKED"],
            "overlap_entities": ["ACC_LINKED"],
            "reason": "账户级串联: 重叠账户 ACC_LINKED",
            "risk_score_bonus": 20,
        }]
        updated = cross_period_linker.apply_links_to_reports(reports, links)
        assert len(updated) == 1
        # 证据链应包含跨期关联
        assert any("跨期关联" in e for e in updated[0]["evidence_chain"])
        # 分析摘要应提及跨期关联
        assert "跨期关联" in updated[0]["analysis_summary"]
        assert "1个历史案件" in updated[0]["analysis_summary"]

    def test_apply_links_no_match_no_change(self):
        """关联链接与报告账户无关应不修改报告"""
        reports = [{
            "report_id": "RPT_2",
            "primary_account": "ACC_UNRELATED",
            "related_accounts": [],
            "evidence_chain": ["原始证据"],
            "analysis_summary": "原始摘要",
        }]
        links = [{
            "link_type": "account",
            "history_execution_id": "HIST_14",
            "history_timestamp": "2026-01-01",
            "current_entities": ["ACC_OTHER"],
            "history_entities": ["ACC_OTHER"],
            "overlap_entities": ["ACC_OTHER"],
            "reason": "账户级串联",
            "risk_score_bonus": 20,
        }]
        updated = cross_period_linker.apply_links_to_reports(reports, links)
        # 报告应该未被修改
        assert updated[0]["evidence_chain"] == ["原始证据"]
        assert updated[0]["analysis_summary"] == "原始摘要"

    def test_apply_empty_links_no_change(self):
        """空关联链接不应修改报告"""
        reports = [{
            "report_id": "RPT_3",
            "primary_account": "ACC_X",
            "evidence_chain": ["原始证据"],
            "analysis_summary": "原始摘要",
        }]
        updated = cross_period_linker.apply_links_to_reports(reports, [])
        assert updated[0]["evidence_chain"] == ["原始证据"]

    def test_bonus_capped(self):
        """风险加成应钳制（单份报告最多40分）"""
        reports = [{
            "report_id": "RPT_4",
            "primary_account": "ACC_CAP",
            "related_accounts": [],
            "evidence_chain": [],
            "analysis_summary": "",
        }]
        # 构造3条关联链接，每条+20，总计60，应钳制到40
        links = [
            {
                "link_type": "account",
                "history_execution_id": f"HIST_{i}",
                "history_timestamp": "2026-01-01",
                "current_entities": ["ACC_CAP"],
                "history_entities": ["ACC_CAP"],
                "overlap_entities": ["ACC_CAP"],
                "reason": f"关联{i}",
                "risk_score_bonus": 20,
            }
            for i in range(3)
        ]
        updated = cross_period_linker.apply_links_to_reports(reports, links)
        assert "加成风险分40分" in updated[0]["analysis_summary"]  # 钳制到40


class TestRiskScoreRange:
    """风险加成范围验证（戒律 M3）"""

    def test_bonus_is_positive(self):
        """风险加成应为正数"""
        current = _make_current_state(
            "CURRENT_15", "2026-01-15",
            rule_hits=[{
                "transaction": _make_txn("T1", "ACC_BONUS", "B", 50000, "2026-01-15T10:00:00"),
                "rule_hits": ["大额交易"],
                "risk_score": 70,
                "evidence": ["大额"],
            }],
        )
        history = [_make_history_record(
            "HIST_15", "2026-01-01", primary_accounts=["ACC_BONUS"]
        )]
        links = cross_period_linker.link_current_to_history(current, history)
        for link in links:
            assert link["risk_score_bonus"] > 0
            assert link["risk_score_bonus"] <= 100
