"""
端到端不变量检查器单元测试
"""
import pytest
from tools.invariant_checker import check_invariants

pytestmark = pytest.mark.unit


def _make_suspicious(tid="T1", risk_score=70, evidence=None, rule_hits=None):
    return {
        "transaction": {"transaction_id": tid},
        "rule_hits": rule_hits if rule_hits is not None else ["大额交易"],
        "risk_score": risk_score,
        "evidence": evidence if evidence is not None else ["大额交易证据"],
    }


class TestInvariantChecker:

    def test_empty_state_passes(self):
        """空状态应通过检查"""
        result = check_invariants({})
        assert result["passed"] is True
        assert result["violation_count"] == 0

    def test_valid_risk_scores_pass(self):
        """有效风险评分通过检查（降序排列）"""
        state = {
            "rule_hits": [
                _make_suspicious("T1", 100),
                _make_suspicious("T2", 80),
                _make_suspicious("T3", 50),
            ]
        }
        result = check_invariants(state)
        assert result["passed"] is True

    def test_out_of_range_score_violation(self):
        """超出范围的风险评分违反M3"""
        state = {
            "rule_hits": [_make_suspicious("T1", 150)]
        }
        result = check_invariants(state)
        assert result["passed"] is False
        assert any(v["invariant"] == "M3_risk_score_range" for v in result["violations"])

    def test_negative_score_violation(self):
        """负数风险评分违反M3"""
        state = {
            "rule_hits": [_make_suspicious("T1", -5)]
        }
        result = check_invariants(state)
        assert result["passed"] is False
        assert any(v["invariant"] == "M3_risk_score_range" for v in result["violations"])

    def test_missing_evidence_violation(self):
        """评分>=60但证据链为空违反M2"""
        state = {
            "rule_hits": [_make_suspicious("T1", 70, evidence=[])]
        }
        result = check_invariants(state)
        assert result["passed"] is False
        assert any(v["invariant"] == "M2_evidence_nonempty" for v in result["violations"])

    def test_low_score_no_evidence_ok(self):
        """评分<60时证据链为空不违反M2"""
        state = {
            "rule_hits": [_make_suspicious("T1", 40, evidence=[])]
        }
        result = check_invariants(state)
        assert result["passed"] is True

    def test_high_risk_not_lost(self):
        """高风险交易在LLM审核结果中存在，不违反P1"""
        state = {
            "rule_hits": [_make_suspicious("T1", 75)],
            "llm_reviewed": [_make_suspicious("T1", 75)],
            "llm_confirmed": [_make_suspicious("T1", 75)],
            "str_reports": [{"report_id": "STR-001"}],
        }
        result = check_invariants(state)
        assert result["passed"] is True

    def test_high_risk_lost_violation(self):
        """高风险交易不在LLM审核结果中，违反P1"""
        state = {
            "rule_hits": [_make_suspicious("T1", 75)],
            "llm_reviewed": [_make_suspicious("T2", 50)],  # T1不在其中
            "llm_confirmed": [_make_suspicious("T2", 50)],
        }
        result = check_invariants(state)
        assert result["passed"] is False
        assert any(v["invariant"] == "P1_high_risk_not_lost" for v in result["violations"])

    def test_report_consistency_ok(self):
        """有确认可疑交易且有报告，通过检查"""
        state = {
            "llm_confirmed": [_make_suspicious("T1", 70)],
            "str_reports": [{"report_id": "STR-001"}],
        }
        result = check_invariants(state)
        assert result["passed"] is True

    def test_report_consistency_violation(self):
        """有确认可疑交易但无报告，违反一致性"""
        state = {
            "llm_confirmed": [_make_suspicious("T1", 70)],
            "str_reports": [],
        }
        result = check_invariants(state)
        assert result["passed"] is False
        assert any(v["invariant"] == "report_consistency" for v in result["violations"])

    def test_llm_reviewed_score_checked(self):
        """LLM审核后的交易评分也被检查"""
        state = {
            "rule_hits": [],
            "llm_reviewed": [_make_suspicious("T1", 120)],
        }
        result = check_invariants(state)
        assert result["passed"] is False
        assert any(v["invariant"] == "M3_risk_score_range" for v in result["violations"])

    def test_multiple_violations(self):
        """多个违反同时检测"""
        state = {
            "rule_hits": [
                _make_suspicious("T1", 150),  # M3违反
                _make_suspicious("T2", 70, evidence=[]),  # M2违反
            ],
        }
        result = check_invariants(state)
        assert result["passed"] is False
        assert result["violation_count"] >= 2
