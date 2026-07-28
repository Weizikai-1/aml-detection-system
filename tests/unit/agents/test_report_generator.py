"""
报告生成 Agent 单元测试

测试覆盖:
1. 分组逻辑(_group_by_account)——仅按收款方分组
2. 风险等级评估(_assess_risk_level)
3. 证据链生成(_generate_evidence_chain)
4. 可疑模式总结(_summarize_patterns)
5. STR报告构造(_build_str_report)
6. Agent节点函数(端到端)
"""
import re
import pytest
from agents.report_generator import (
    _group_by_account,
    _assess_risk_level,
    _generate_evidence_chain,
    _summarize_patterns,
    _build_str_report,
    cluster_into_cases,
    create_report_generator_agent,
)
from graph.state import AMLState, SuspiciousTransaction


def _make_txn(tid="T1", frm="ACC_A", to="ACC_B", amount=50000.0):
    return {
        "transaction_id": tid,
        "from_account": frm,
        "to_account": to,
        "amount": amount,
        "timestamp": "2026-07-01T10:00:00",
        "transaction_type": "transfer",
        "remark": "",
    }


def _make_suspicious(
    risk_score=70, rule_hits=None, evidence=None, txn=None,
):
    return {
        "transaction": txn or _make_txn(),
        "rule_hits": rule_hits if rule_hits is not None else ["大额交易"],
        "risk_score": risk_score,
        "evidence": evidence if evidence is not None else ["单笔金额超10万"],
        "graph_evidence": None,
        "llm_analysis": None,
        "llm_confidence": None,
        "is_false_positive": None,
        "community_id": None,
    }


# ============================================================
# 1. 分组逻辑
# ============================================================
@pytest.mark.unit
class TestGroupByAccount:
    def test_group_by_receiver(self):
        """仅按收款方分组"""
        s1 = _make_suspicious(txn=_make_txn("T1", "PAYER_A", "RECV_X"))
        s2 = _make_suspicious(txn=_make_txn("T2", "PAYER_B", "RECV_X"))
        s3 = _make_suspicious(txn=_make_txn("T3", "PAYER_C", "RECV_Y"))

        groups = _group_by_account([s1, s2, s3])

        assert "RECV_X" in groups
        assert "RECV_Y" in groups
        assert len(groups["RECV_X"]) == 2
        assert len(groups["RECV_Y"]) == 1

    def test_no_payer_grouping(self):
        """Regression: 付款方不再单独成组(修复报告膨胀bug)"""
        # 修复前: 付款方也被加入分组，导致同一笔交易出现在两个组里，报告数量翻倍
        # 修复后: 仅按收款方分组
        s = _make_suspicious(txn=_make_txn("T1", "PAYER_A", "RECV_X"))

        groups = _group_by_account([s])

        assert "RECV_X" in groups
        assert "PAYER_A" not in groups
        assert len(groups) == 1


# ============================================================
# 2. 风险等级评估
# ============================================================
@pytest.mark.unit
class TestAssessRiskLevel:
    def test_critical_max_score(self):
        """max≥85 → critical"""
        items = [_make_suspicious(risk_score=90)]
        assert _assess_risk_level(items) == "critical"

    def test_critical_avg_and_count(self):
        """avg≥70 且 count≥5 → critical"""
        items = [_make_suspicious(risk_score=70) for _ in range(5)]
        assert _assess_risk_level(items) == "critical"

    def test_high_level(self):
        """max≥70 → high"""
        items = [_make_suspicious(risk_score=75)]
        assert _assess_risk_level(items) == "high"

    def test_medium_level(self):
        """max≥50 → medium"""
        items = [_make_suspicious(risk_score=55)]
        assert _assess_risk_level(items) == "medium"

    def test_low_level(self):
        """其他 → low"""
        items = [_make_suspicious(risk_score=40)]
        assert _assess_risk_level(items) == "low"

    def test_empty_list(self):
        """空列表 → low"""
        assert _assess_risk_level([]) == "low"


# ============================================================
# 3. 证据链生成
# ============================================================
@pytest.mark.unit
class TestGenerateEvidenceChain:
    def test_dedup_evidence(self):
        """重复证据去重"""
        items = [
            _make_suspicious(evidence=["证据A", "证据B"]),
            _make_suspicious(evidence=["证据A", "证据C"]),
        ]
        chain = _generate_evidence_chain(items)

        assert len(chain) == 3
        assert set(chain) == {"证据A", "证据B", "证据C"}

    def test_sorted_output(self):
        """证据排序输出"""
        items = [
            _make_suspicious(evidence=["证据C"]),
            _make_suspicious(evidence=["证据A", "证据B"]),
        ]
        chain = _generate_evidence_chain(items)

        assert chain == ["证据A", "证据B", "证据C"]


# ============================================================
# 4. 可疑模式总结
# ============================================================
@pytest.mark.unit
class TestSummarizePatterns:
    def test_pattern_counts_sorted(self):
        """按出现次数倒序"""
        items = [
            _make_suspicious(rule_hits=["大额交易", "对敲交易"]),
            _make_suspicious(rule_hits=["大额交易"]),
            _make_suspicious(rule_hits=["大额交易", "快进快出"]),
        ]
        patterns = _summarize_patterns(items)

        # 大额交易(3笔) 排第一
        assert patterns[0].startswith("大额交易")
        assert "(3笔)" in patterns[0]

    def test_empty_patterns(self):
        """无规则命中 → 空列表"""
        items = [_make_suspicious(rule_hits=[])]
        assert _summarize_patterns(items) == []


# ============================================================
# 5. STR报告构造
# ============================================================
@pytest.mark.unit
class TestBuildSTRReport:
    def test_report_id_format(self):
        """报告ID格式: STR-YYYYMMDD-8位HEX大写"""
        items = [_make_suspicious(txn=_make_txn("T1", "A", "TARGET_ACC"))]
        report = _build_str_report("TARGET_ACC", items)

        # STR-20260727-AB12CD34
        pattern = r"^STR-\d{8}-[0-9A-F]{8}$"
        assert re.match(pattern, report["report_id"]), \
            f"报告ID格式不符: {report['report_id']}"

    def test_related_excludes_primary(self):
        """关联账户不含主账户"""
        items = [
            _make_suspicious(txn=_make_txn("T1", "PAYER_A", "TARGET_ACC")),
            _make_suspicious(txn=_make_txn("T2", "PAYER_B", "TARGET_ACC")),
        ]
        report = _build_str_report("TARGET_ACC", items)

        assert "TARGET_ACC" not in report["related_accounts"]
        assert "PAYER_A" in report["related_accounts"]
        assert "PAYER_B" in report["related_accounts"]

    def test_total_amount(self):
        """总金额累加正确"""
        items = [
            _make_suspicious(txn=_make_txn("T1", "A", "B", 50000.0)),
            _make_suspicious(txn=_make_txn("T2", "C", "B", 30000.0)),
        ]
        report = _build_str_report("B", items)

        assert report["total_suspicious_amount"] == 80000.0

    def test_self_transfer_no_related(self):
        """自转账(from==to)关联账户为空"""
        items = [_make_suspicious(txn=_make_txn("T1", "ACC_X", "ACC_X"))]
        report = _build_str_report("ACC_X", items)

        # 主账户被discard，关联账户应为空列表
        assert report["related_accounts"] == []

    def test_risk_level_propagated(self):
        """风险等级写入报告"""
        items = [_make_suspicious(risk_score=90)]
        report = _build_str_report("B", items)

        assert report["risk_level"] == "critical"
        assert report["customer_profile"]["risk_rating"] == "critical"


# ============================================================
# 5.5 案件聚类
# ============================================================
class TestClusterIntoCases:
    def test_empty_input(self):
        """空输入返回空列表"""
        cases = cluster_into_cases([])
        assert len(cases) == 0

    def test_single_case(self):
        """同一账户同一规则聚成一个案件"""
        txns = [
            _make_suspicious(
                rule_hits=["分拆转账"],
                txn=_make_txn("T1", "TARGET", "RECV_1", 45000),
                risk_score=70,
            ),
            _make_suspicious(
                rule_hits=["分拆转账"],
                txn=_make_txn("T2", "TARGET", "RECV_2", 46000),
                risk_score=70,
            ),
        ]
        cases = cluster_into_cases(txns)
        assert len(cases) == 1
        assert cases[0]["primary_account"] == "TARGET"
        assert cases[0]["rule_type"] == "分拆转账"
        assert cases[0]["txn_count"] == 2
        assert cases[0]["total_amount"] == 91000.0
        assert cases[0]["max_risk_score"] == 70

    def test_multiple_rules_same_account(self):
        """同一账户不同规则分成不同案件"""
        txns = [
            _make_suspicious(
                rule_hits=["分拆转账"],
                txn=_make_txn("T1", "SRC1", "TARGET", 45000),
                risk_score=70,
            ),
            _make_suspicious(
                rule_hits=["大额交易"],
                txn=_make_txn("T2", "SRC2", "TARGET", 200000),
                risk_score=40,
            ),
        ]
        cases = cluster_into_cases(txns)
        assert len(cases) == 2

    def test_different_accounts(self):
        """不同账户分成不同案件"""
        txns = [
            _make_suspicious(
                rule_hits=["大额交易"],
                txn=_make_txn("T1", "A1", "B1", 200000),
                risk_score=40,
            ),
            _make_suspicious(
                rule_hits=["大额交易"],
                txn=_make_txn("T2", "A2", "B2", 200000),
                risk_score=40,
            ),
        ]
        cases = cluster_into_cases(txns)
        assert len(cases) == 2

    def test_sorted_by_risk(self):
        """案件按风险分降序排列（戒律 P3）"""
        txns = [
            _make_suspicious(
                rule_hits=["大额交易"],
                txn=_make_txn("T1", "A1", "LOW", 200000),
                risk_score=40,
            ),
            _make_suspicious(
                rule_hits=["分拆转账"],
                txn=_make_txn("T2", "A2", "HIGH", 45000),
                risk_score=70,
            ),
        ]
        cases = cluster_into_cases(txns)
        assert cases[0]["max_risk_score"] >= cases[1]["max_risk_score"]

    def test_evidence_summary(self):
        """案件包含证据汇总（戒律 P3：有证据）"""
        txns = [
            _make_suspicious(
                rule_hits=["分拆转账"],
                txn=_make_txn("T1", "SRC1", "TARGET", 45000),
                evidence=["1小时内收到5笔转账", "金额接近5万阈值"],
                risk_score=70,
            ),
        ]
        cases = cluster_into_cases(txns)
        assert len(cases[0]["evidence_summary"]) > 0

    def test_case_id_format(self):
        """案件ID格式正确"""
        txns = [_make_suspicious()]
        cases = cluster_into_cases(txns)
        assert cases[0]["case_id"].startswith("CASE-")

    def test_no_transaction_left_behind(self):
        """所有可疑交易都被分配到案件（戒律 P1：不遗漏）"""
        txns = [
            _make_suspicious(
                rule_hits=["分拆转账"],
                txn=_make_txn(f"T{i}", f"SRC{i}", "TARGET", 45000),
            )
            for i in range(5)
        ]
        cases = cluster_into_cases(txns)
        total_in_cases = sum(c["txn_count"] for c in cases)
        assert total_in_cases == 5


# ============================================================
# 6. Agent节点函数
# ============================================================
@pytest.mark.unit
class TestReportGeneratorAgent:
    def test_empty_input(self):
        """无确认交易 → 0报告"""
        agent = create_report_generator_agent()
        state: AMLState = {"llm_confirmed": []}
        result = agent(state)

        assert result["str_reports"] == []
        assert result["report_count"] == 0

    def test_full_flow_multiple_reports(self):
        """多账户分组生成多份报告"""
        s1 = _make_suspicious(txn=_make_txn("T1", "A", "RECV_1"))
        s2 = _make_suspicious(txn=_make_txn("T2", "B", "RECV_2"))
        s3 = _make_suspicious(txn=_make_txn("T3", "C", "RECV_1"))

        agent = create_report_generator_agent()
        state: AMLState = {"llm_confirmed": [s1, s2, s3]}
        result = agent(state)

        assert result["report_count"] == 2
        primary_accounts = [r["primary_account"] for r in result["str_reports"]]
        assert "RECV_1" in primary_accounts
        assert "RECV_2" in primary_accounts

    def test_reports_sorted_by_risk(self):
        """报告按 critical→low 排序"""
        # RECV_HIGH: 高风险
        high_risk_items = [_make_suspicious(risk_score=90, txn=_make_txn("T1", "A", "RECV_HIGH"))]
        # RECV_LOW: 低风险
        low_risk_items = [_make_suspicious(risk_score=40, txn=_make_txn("T2", "B", "RECV_LOW"))]

        agent = create_report_generator_agent()
        state: AMLState = {"llm_confirmed": low_risk_items + high_risk_items}
        result = agent(state)

        # critical 应排在 low 之前
        assert result["str_reports"][0]["risk_level"] == "critical"
        assert result["str_reports"][1]["risk_level"] == "low"

    def test_gnn_discovered_uses_real_account(self):
        """Regression: GNN 发现的交易主账户应是真实账户而非伪造账户"""
        # 修复前: to_account="GNN_FLAGGED"，所有 GNN 交易被归到一份伪造账户报告
        # 修复后: to_account=真实可疑账户，每笔 GNN 交易生成独立报告
        gnn_suspicious = {
            "transaction": {
                "transaction_id": "GNN_ACC_X",
                "from_account": "GNN_MODEL",
                "to_account": "ACC_X",  # 真实可疑账户
                "amount": 50000.0,
                "timestamp": "2026-07-01T10:00:00",
                "transaction_type": "gnn_discovered",
                "remark": "GNN节点分类发现",
            },
            "rule_hits": ["GNN节点分类"],
            "risk_score": 85,
            "evidence": ["GNN模型预测高风险"],
            "graph_evidence": "GNN模型预测高风险",
            "llm_analysis": None,
            "llm_confidence": None,
            "is_false_positive": None,
            "community_id": None,
        }

        agent = create_report_generator_agent()
        state: AMLState = {"llm_confirmed": [gnn_suspicious]}
        result = agent(state)

        assert result["report_count"] == 1
        assert result["str_reports"][0]["primary_account"] == "ACC_X"
        assert "GNN_MODEL" in result["str_reports"][0]["related_accounts"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
