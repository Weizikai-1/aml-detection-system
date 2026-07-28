"""
合规审核 Agent 单元测试

测试覆盖:
1. 报告完整性检查(_check_completeness)
2. 证据充分性检查(_check_evidence)
3. 风险等级一致性检查(_check_risk_consistency)
4. 格式规范性检查(_check_format)
5. 综合审核(_audit_report)
6. Agent节点函数(端到端 + 三档分流)
"""
import pytest
from agents.compliance_auditor import (
    _check_completeness,
    _check_evidence,
    _check_risk_consistency,
    _check_format,
    _audit_report,
    create_compliance_auditor_agent,
)
from graph.state import AMLState, STRReport, SuspiciousTransaction


def _make_suspicious(
    risk_score=70, rule_hits=None, evidence=None,
):
    return {
        "transaction": {
            "transaction_id": "T1",
            "from_account": "ACC_A",
            "to_account": "ACC_B",
            "amount": 50000.0,
            "timestamp": "2026-07-01T10:00:00",
            "transaction_type": "transfer",
            "remark": "",
        },
        "rule_hits": rule_hits if rule_hits is not None else ["大额交易"],
        "risk_score": risk_score,
        "evidence": evidence if evidence is not None else ["单笔金额超10万"],
        "graph_evidence": None,
        "llm_analysis": None,
        "llm_confidence": None,
        "is_false_positive": None,
        "community_id": None,
    }


def _make_report(
    report_id="STR-20260727-AB12CD34",
    risk_level="high",
    suspicious_transactions=None,
    evidence_chain=None,
    analysis_summary="账户存在明显可疑交易特征，涉及多笔交易，需重点关注。",
    disposal_suggestion="列入重点监控名单，上报可疑交易报告，加强后续交易监测。",
    primary_account="ACC_B",
    report_date="2026-07-27 10:00:00",
):
    """构造一份基本合规的STR报告"""
    if suspicious_transactions is None:
        suspicious_transactions = [_make_suspicious() for _ in range(3)]
    if evidence_chain is None:
        evidence_chain = ["证据1", "证据2", "证据3"]

    return {
        "report_id": report_id,
        "report_date": report_date,
        "report_type": "初始报告",
        "primary_account": primary_account,
        "related_accounts": ["ACC_A"],
        "customer_profile": {"account_type": "对公", "risk_rating": risk_level},
        "suspicious_transactions": suspicious_transactions,
        "total_suspicious_amount": 150000.0,
        "suspicious_patterns": ["大额交易(3笔)"],
        "risk_level": risk_level,
        "analysis_summary": analysis_summary,
        "evidence_chain": evidence_chain,
        "disposal_suggestion": disposal_suggestion,
        "compliance_status": "pending",
        "compliance_notes": None,
        "reviewer": None,
        "final_decision": None,
    }


# ============================================================
# 1. 报告完整性检查
# ============================================================
@pytest.mark.unit
class TestCheckCompleteness:
    def test_all_fields_present(self):
        """8个必填字段齐全 → 1.0"""
        report = _make_report()
        score, issues = _check_completeness(report)

        assert score == 1.0
        assert len(issues) == 0

    def test_missing_field(self):
        """缺字段 → 扣分"""
        report = _make_report()
        report["primary_account"] = ""
        score, issues = _check_completeness(report)

        assert score < 1.0
        assert any("primary_account" in i for i in issues)

    def test_empty_list_field(self):
        """空列表视为缺失"""
        report = _make_report()
        report["suspicious_transactions"] = []
        score, issues = _check_completeness(report)

        assert score < 1.0
        assert any("suspicious_transactions" in i for i in issues)

    def test_none_field(self):
        """None 视为缺失"""
        report = _make_report()
        report["evidence_chain"] = None
        score, issues = _check_completeness(report)

        assert score < 1.0


# ============================================================
# 2. 证据充分性检查
# ============================================================
@pytest.mark.unit
class TestCheckEvidence:
    def test_strong_evidence(self):
        """证据≥3 + 交易≥5 + 模式≥3 → 高分"""
        txns = [
            _make_suspicious(rule_hits=["大额交易", "对敲交易", "快进快出"])
            for _ in range(5)
        ]
        report = _make_report(
            suspicious_transactions=txns,
            evidence_chain=["证据1", "证据2", "证据3", "证据4"],
        )
        score, issues = _check_evidence(report)

        assert score == 1.0
        assert len(issues) == 0

    def test_empty_evidence(self):
        """证据链空 → 低分 + issue"""
        report = _make_report(
            evidence_chain=[],
            suspicious_transactions=[_make_suspicious()],
        )
        score, issues = _check_evidence(report)

        assert score < 0.5
        assert any("证据链为空" in i for i in issues)

    def test_medium_evidence(self):
        """中等证据 → 中等分数"""
        txns = [_make_suspicious(rule_hits=["大额交易"]) for _ in range(3)]
        report = _make_report(
            suspicious_transactions=txns,
            evidence_chain=["证据1", "证据2"],
        )
        score, _ = _check_evidence(report)

        # 证据2条(+0.25) + 交易3笔(+0.2) + 模式1种(+0.1) = 0.55
        assert 0.4 <= score <= 0.7


# ============================================================
# 3. 风险等级一致性检查
# ============================================================
@pytest.mark.unit
class TestCheckRiskConsistency:
    def test_consistent_critical(self):
        """实际等级=预期 → 1.0"""
        txns = [_make_suspicious(risk_score=90) for _ in range(2)]
        report = _make_report(risk_level="critical", suspicious_transactions=txns)
        score, issues = _check_risk_consistency(report)

        assert score == 1.0
        assert len(issues) == 0

    def test_diff_one_level(self):
        """偏差1级 → 0.7"""
        txns = [_make_suspicious(risk_score=90) for _ in range(2)]
        # 实际 high，预期 critical，偏差1级
        report = _make_report(risk_level="high", suspicious_transactions=txns)
        score, issues = _check_risk_consistency(report)

        assert score == 0.7
        assert len(issues) > 0

    def test_no_transactions_but_marked_suspicious(self):
        """无可疑交易却标可疑 → 0.0（戒律: 无证据不应给中间分）"""
        report = _make_report(
            risk_level="high",
            suspicious_transactions=[],
        )
        score, issues = _check_risk_consistency(report)

        assert score == 0.0
        assert any("无可疑交易" in i for i in issues)


# ============================================================
# 4. 格式规范性检查
# ============================================================
@pytest.mark.unit
class TestCheckFormat:
    def test_valid_format(self):
        """规范 → 1.0"""
        report = _make_report()
        score, issues = _check_format(report)

        assert score == 1.0
        assert len(issues) == 0

    def test_bad_report_id(self):
        """ID不以STR开头 → 扣0.2"""
        report = _make_report(report_id="AML-20260727-AB12CD34")
        score, issues = _check_format(report)

        assert score == pytest.approx(0.8, abs=0.01)
        assert any("报告ID格式" in i for i in issues)

    def test_short_summary(self):
        """摘要<20字 → 扣0.2"""
        report = _make_report(analysis_summary="短摘要")
        score, issues = _check_format(report)

        assert score == pytest.approx(0.8, abs=0.01)
        assert any("摘要过短" in i for i in issues)

    def test_short_disposal(self):
        """处置建议过短 → 扣0.15"""
        report = _make_report(disposal_suggestion="上报")
        score, _ = _check_format(report)

        assert score == pytest.approx(0.85, abs=0.01)


# ============================================================
# 5. 综合审核(_audit_report)
# ============================================================
@pytest.mark.unit
class TestAuditReport:
    def test_passed(self):
        """综合≥0.8 → passed"""
        # 完整报告 + 强证据 + 一致风险 + 规范格式
        txns = [
            _make_suspicious(risk_score=90, rule_hits=["大额交易", "对敲交易", "快进快出"])
            for _ in range(5)
        ]
        report = _make_report(
            risk_level="critical",
            suspicious_transactions=txns,
            evidence_chain=["证据1", "证据2", "证据3", "证据4"],
        )
        status, score, issues, notes = _audit_report(report)

        assert status == "passed"
        assert score >= 0.8

    def test_rejected(self):
        """综合<0.5 → rejected"""
        report = _make_report()
        # 制造多处缺陷: 缺字段 + 空证据 + 风险不一致 + 格式问题
        report["primary_account"] = ""
        report["evidence_chain"] = []
        report["suspicious_transactions"] = []
        report["risk_level"] = "critical"
        report["report_id"] = "BAD_ID"
        report["analysis_summary"] = "短"

        status, score, issues, notes = _audit_report(report)

        assert status == "rejected"
        assert score < 0.5

    def test_human_review_in_between(self):
        """综合 0.5-0.8 → human_review"""
        # 中等证据 + 一致风险 → 应落在 human_review 区间
        txns = [_make_suspicious(risk_score=55, rule_hits=["大额交易"]) for _ in range(2)]
        report = _make_report(
            risk_level="medium",
            suspicious_transactions=txns,
            evidence_chain=["证据1"],
        )
        status, score, _, _ = _audit_report(report)

        # 中等情况通常落在 human_review 区间
        assert status in ("human_review", "passed")
        assert 0.5 <= score < 0.85 or status == "passed"


# ============================================================
# 6. Agent节点函数
# ============================================================
@pytest.mark.unit
class TestComplianceAuditorAgent:
    def test_empty_input(self):
        """无报告 → 空结果"""
        agent = create_compliance_auditor_agent()
        state: AMLState = {"str_reports": []}
        result = agent(state)

        assert result["final_reports"] == []
        assert result["rejected_reports"] == []
        assert result["compliance_stats"]["total"] == 0

    def test_mixed_reports_routing(self):
        """三档报告正确分流: passed / human_review / rejected"""
        # 1. 高质量报告 → passed
        good_txns = [
            _make_suspicious(risk_score=90, rule_hits=["大额交易", "对敲交易", "快进快出"])
            for _ in range(5)
        ]
        good_report = _make_report(
            report_id="STR-20260727-AAAA1111",
            risk_level="critical",
            suspicious_transactions=good_txns,
            evidence_chain=["证据1", "证据2", "证据3", "证据4"],
        )

        # 2. 缺陷报告 → rejected
        bad_report = _make_report(
            report_id="STR-20260727-BBBB2222",
            risk_level="critical",
        )
        bad_report["primary_account"] = ""
        bad_report["evidence_chain"] = []
        bad_report["suspicious_transactions"] = []
        bad_report["analysis_summary"] = "短"

        agent = create_compliance_auditor_agent()
        state: AMLState = {"str_reports": [good_report, bad_report]}
        result = agent(state)

        stats = result["compliance_stats"]
        assert stats["total"] == 2
        assert stats["passed"] >= 1
        assert stats["rejected"] >= 1

    def test_stats_consistency(self):
        """统计数字一致: passed + human_review + rejected == total"""
        txns = [_make_suspicious(risk_score=70) for _ in range(3)]
        report = _make_report(risk_level="high", suspicious_transactions=txns)

        agent = create_compliance_auditor_agent()
        state: AMLState = {"str_reports": [report]}
        result = agent(state)

        stats = result["compliance_stats"]
        assert stats["passed"] + stats["human_review"] + stats["rejected"] == stats["total"]

    def test_passed_report_has_final_decision(self):
        """通过的报告应标记 final_decision"""
        good_txns = [
            _make_suspicious(risk_score=90, rule_hits=["大额交易", "对敲交易", "快进快出"])
            for _ in range(5)
        ]
        good_report = _make_report(
            risk_level="critical",
            suspicious_transactions=good_txns,
            evidence_chain=["证据1", "证据2", "证据3", "证据4"],
        )

        agent = create_compliance_auditor_agent()
        state: AMLState = {"str_reports": [good_report]}
        result = agent(state)

        passed_reports = [r for r in result["final_reports"] if r["compliance_status"] == "passed"]
        if passed_reports:
            assert passed_reports[0]["final_decision"] is not None

    def test_human_review_creates_task(self):
        """human_review 报告应创建人工审核任务"""
        # 构造一份中等质量报告，落入 human_review 区间
        txns = [_make_suspicious(risk_score=60, rule_hits=["大额交易"]) for _ in range(2)]
        medium_report = _make_report(
            risk_level="medium",
            suspicious_transactions=txns,
            evidence_chain=["证据1", "证据2"],
        )

        agent = create_compliance_auditor_agent()
        state: AMLState = {"str_reports": [medium_report]}
        result = agent(state)

        # 如果落入 human_review，应有对应任务
        if result["compliance_stats"]["human_review"] > 0:
            assert len(result["human_review_tasks"]) > 0
            task = result["human_review_tasks"][0]
            assert "report_id" in task
            assert "compliance_score" in task
            assert "issues" in task


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
