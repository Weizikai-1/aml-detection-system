"""合规审核 Agent 测试 — 格式检查 + 内容实质性 + 证据链 + 评分"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from agents.compliance import run as compliance_run, _check_content_substance, _check_evidence_chain, _check_risk_reasonableness
from agents.compliance import _STRUCTURE_CHECKS


# ---- 构造 State 辅助函数 ----
def _state(report: str = "", rule_report: dict = None) -> dict:
    s = {"str_report": report}
    if rule_report is not None:
        s["rule_report"] = rule_report
    return s


_VALID_REPORT = """# 可疑交易报告 (STR)

**报告时间**: 2026-08-04 15:30
**数据来源**: Kaggle PaySim

## 1. 数据概览
- 总交易: 5000
- 欺诈交易: 65
- 欺诈率: 1.30%

## 2. 规则引擎检测
- 命中总数: 96
- 高风险: 4 / 中风险: 35 / 低风险: 57
- 规则分布: {'large_amount': 75}

## 3. 图分析 (GNN)
- 节点级 F1: 0.8000
- 节点级 Precision: 1.0000

## 4. LLM 深度审核
LLM 审核已执行

## 5. 高风险交易详情
1. 风险分 95 | 规则: sanction_list
   - 付款方: SDN001_TERROR_FINANCE
   - 收款方: C1234567
   - 金额: 500000
   - 证据: 制裁名单命中

## 6. 建议措施
建议立即上报监管部门。
"""


class TestComplianceStructure:
    def test_empty_report(self):
        """空报告应判定不通过"""
        result = compliance_run(_state(""))["compliance"]
        assert not result["passed"]
        assert "报告为空" in result["issues"][0]
        assert result["score"] == 0

    def test_valid_report_passes(self):
        """完整报告应通过合规"""
        result = compliance_run(_state(_VALID_REPORT))["compliance"]
        assert result["passed"]
        assert result["score"] >= 80
        assert "合规通过" in result["status"]

    def test_report_exists_but_empty_str(self):
        """空字符串报告"""
        result = compliance_run(_state(""))["compliance"]
        assert not result["passed"]

    def test_structure_checks_all_pass(self):
        """有效报告所有结构项应通过"""
        state = _state(_VALID_REPORT)
        for name, check_fn in _STRUCTURE_CHECKS:
            assert check_fn(state), f"结构检查 '{name}' 应通过"

    def test_missing_section_detected(self):
        """缺少关键章节应被检测"""
        state = _state("可疑交易报告\n数据来源: test\n建议措施: 无")  # 缺少大部分章节
        result = compliance_run(state)["compliance"]
        assert len(result["issues"]) > 0

    def test_content_too_short(self):
        """报告过短应触发内容实质性检查"""
        short = "可疑交易报告\n报告时间: 2026\n数据来源: test"
        result = compliance_run(_state(short))["compliance"]
        # 过短报告应不通过或评分较低
        assert result["score"] < 60


class TestContentSubstance:
    def test_short_content_flagged(self):
        issues = _check_content_substance("短报告" * 3)
        assert len(issues) > 0

    def test_normal_content_ok(self):
        issues = _check_content_substance(_VALID_REPORT)
        assert len(issues) == 0

    def test_excessive_na_flagged(self):
        report = "可疑交易报告\n" + "N/A\n" * 10
        issues = _check_content_substance(report)
        assert any("N/A" in i for i in issues)

    def test_no_fraud_is_legal(self):
        """无高风险交易是合法情况，不应标记"""
        report = _VALID_REPORT.replace("高风险: 4", "高风险: 0") + "\n无高风险交易。"
        issues = _check_content_substance(report)
        # 不应因为 "无高风险交易" 而报错
        assert not any("无高风险交易" in i for i in issues)


class TestEvidenceChain:
    def test_complete_evidence_passes(self):
        issues = _check_evidence_chain(_VALID_REPORT)
        assert len(issues) == 0

    def test_missing_evidence_flagged(self):
        report = "可疑交易报告\n无具体证据支撑"
        issues = _check_evidence_chain(report)
        assert len(issues) > 0


class TestRiskReasonableness:
    def test_hits_without_high_risk_warns(self):
        rr = {"summary": {"high_risk": 0, "total_hits": 50}}
        warnings = _check_risk_reasonableness({"rule_report": rr})
        assert len(warnings) > 0
        assert "阈值" in warnings[0]

    def test_excessive_hit_rate_warns(self):
        rr = {"summary": {"high_risk": 10, "total_hits": 200}}
        warnings = _check_risk_reasonableness({"rule_report": rr})
        assert len(warnings) > 0
        assert "命中率" in warnings[0] or "过度触发" in warnings[0]

    def test_normal_case_no_warnings(self):
        rr = {"summary": {"high_risk": 5, "total_hits": 30}}
        warnings = _check_risk_reasonableness({"rule_report": rr})
        assert len(warnings) == 0


class TestScoring:
    def test_score_range(self):
        result = compliance_run(_state(_VALID_REPORT))["compliance"]
        assert 0 <= result["score"] <= 100

    def test_score_deduction_for_missing_sections(self):
        """每缺一个结构项扣 8 分"""
        report = "可疑交易报告\n建议措施: 无"  # 大量缺失
        result = compliance_run(_state(report))["compliance"]
        assert result["score"] < 90

    def test_format_check_keys(self):
        result = compliance_run(_state(_VALID_REPORT))["compliance"]
        assert "format_check" in result
        assert "content_check" in result
        assert "has_evidence" in result["content_check"]
        assert "content_length" in result["content_check"]


class TestWarnings:
    def test_warnings_in_result(self):
        rr = {"summary": {"high_risk": 0, "total_hits": 10}}
        state = {"str_report": _VALID_REPORT, "rule_report": rr}
        result = compliance_run(state)["compliance"]
        assert result["warnings"] or result["issues"]
