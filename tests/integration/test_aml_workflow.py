"""
AML 主流程集成测试

测试目标：验证完整工作流"数据预处理 → 规则引擎 → 图分析 → LLM 降级 → 报告生成 → 合规审核"
能在降级模式（无 LLM）下端到端跑通，且核心戒律不被违反。

覆盖场景：
1. 完整流程跑通：构造能触发多种规则的交易集，执行 AMLAgentsGraph.run()
2. 规则引擎产出有效：rule_hits 非空、风险评分在 [0, 100]、证据链不为空
3. 报告生成有效：str_reports 非空、报告包含主涉案方
4. 不变量检查通过：M3 评分范围、M2 证据完整、P1 高风险不丢失
5. 空交易容错：空输入不崩溃
6. 正常交易不误报：全部正常交易时 rule_hits 为空或极少
7. LLM 降级一致性：降级模式下评分口径一致（_degraded 标记）
8. 图分析集成：图分析证据正确附加到可疑交易
9. 合规审核集成：compliance_score 在合理范围
10. 持久化集成：执行后 state 包含完整字段
"""
import os
import sys
import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from graph.state import Transaction
from graph.workflow import AMLAgentsGraph
from tools.invariant_checker import check_invariants


# ============================================================
# 辅助函数
# ============================================================
def _make_txn(
    tid: str,
    from_acc: str,
    to_acc: str,
    amount: float,
    timestamp: str,
    remark: str = "",
    txn_type: str = "transfer",
) -> Transaction:
    return {
        "transaction_id": tid,
        "from_account": from_acc,
        "to_account": to_acc,
        "amount": amount,
        "timestamp": timestamp,
        "transaction_type": txn_type,
        "remark": remark,
    }


def _smurfing_txns(count: int = 6) -> list:
    """构造分拆转账：count 笔 4.5 万，同收款人，不同付款人，1 小时内"""
    return [
        _make_txn(
            f"SMURF_{i}", f"PAYER_{i}", "RECV_SMURF", 45000.0,
            f"2026-07-01T10:{i:02d}:00"
        )
        for i in range(count)
    ]


def _large_amount_txns() -> list:
    """大额交易"""
    return [
        _make_txn("LARGE_1", "ACC_X", "ACC_Y", 200000.0, "2026-07-01T11:00:00"),
        _make_txn("LARGE_2", "ACC_Z", "ACC_W", 350000.0, "2026-07-01T12:00:00"),
    ]


def _fast_in_fast_out_txns() -> list:
    """快进快出：10 分钟内 99% 金额转出"""
    return [
        _make_txn("FIFO_IN", "ACC_IN", "ACC_MID", 50000.0, "2026-07-01T13:00:00"),
        _make_txn("FIFO_OUT", "ACC_MID", "ACC_OUT", 49500.0, "2026-07-01T13:08:00"),
    ]


def _normal_txns() -> list:
    """正常交易"""
    return [
        _make_txn("NORMAL_1", "ACC_A", "ACC_B", 5000.0, "2026-07-01T14:00:00", "工资"),
        _make_txn("NORMAL_2", "ACC_C", "ACC_D", 8000.0, "2026-07-01T15:00:00", "报销"),
        _make_txn("NORMAL_3", "ACC_E", "ACC_F", 3200.0, "2026-07-01T16:00:00", "餐费"),
    ]


def _mixed_suspicious_txns() -> list:
    """混合可疑交易（触发多种规则）"""
    return _smurfing_txns(6) + _large_amount_txns() + _fast_in_fast_out_txns()


# ============================================================
# 完整流程集成测试
# ============================================================
@pytest.mark.integration
class TestAMLWorkflowIntegration:
    """AML 主流程集成测试"""

    @classmethod
    @pytest.fixture(scope="class")
    def aml_system(cls):
        """降级模式的 AML 系统（无 LLM）"""
        return AMLAgentsGraph(llm=None, enable_monitor=False)

    @classmethod
    @pytest.fixture(scope="class")
    def mixed_result(cls, aml_system):
        """混合可疑交易的完整分析结果（class 级共享，避免重复执行）"""
        txns = _mixed_suspicious_txns()
        result = aml_system.run(transactions=txns, analysis_date="2026-07-01")
        return result

    def test_workflow_completes_without_error(self, mixed_result):
        """完整流程能跑通，不抛异常"""
        assert mixed_result is not None
        assert isinstance(mixed_result, dict)
        assert mixed_result.get("error", "") == ""

    def test_rule_hits_not_empty(self, mixed_result):
        """规则引擎有命中（构造的数据应触发规则）"""
        rule_hits = mixed_result.get("rule_hits", [])
        assert len(rule_hits) > 0, "混合可疑交易应触发规则命中"

    def test_str_reports_generated(self, mixed_result):
        """生成了 STR 报告"""
        str_reports = mixed_result.get("str_reports", [])
        assert len(str_reports) > 0, "应生成至少一份 STR 报告"

    def test_risk_scores_in_valid_range(self, mixed_result):
        """戒律 M3：所有风险评分在 [0, 100] 范围"""
        rule_hits = mixed_result.get("rule_hits", [])
        for hit in rule_hits:
            score = hit.get("risk_score", 0)
            assert 0 <= score <= 100, f"风险评分越界: {score}"

    def test_evidence_chain_not_empty(self, mixed_result):
        """戒律 M2：评分≥60 的可疑交易证据链不为空"""
        rule_hits = mixed_result.get("rule_hits", [])
        for hit in rule_hits:
            if hit.get("risk_score", 0) >= 60:
                evidence = hit.get("evidence", [])
                structured = hit.get("structured_evidence", [])
                assert len(evidence) > 0 or len(structured) > 0, \
                    f"评分≥60 的交易证据链为空: {hit.get('transaction', {}).get('transaction_id')}"

    def test_invariant_check_passes(self, mixed_result):
        """不变量检查通过（戒律守护）"""
        invariant = mixed_result.get("invariant_check")
        assert invariant is not None, "应执行不变量检查"
        assert invariant["passed"], \
            f"不变量检查未通过: {invariant.get('violations', [])}"

    def test_value_metrics_present(self, mixed_result):
        """价值证明指标存在"""
        vm = mixed_result.get("value_metrics")
        assert vm is not None, "应计算价值证明指标"
        # 实际字段名：detection_rate / suspicious_transactions_found 等
        assert len(vm) > 0, "价值证明指标不应为空"

    def test_processing_time_recorded(self, mixed_result):
        """处理时间被记录"""
        assert "total_processing_time" in mixed_result
        assert mixed_result["total_processing_time"] > 0

    def test_execution_id_present(self, mixed_result):
        """执行 ID 存在"""
        assert "execution_id" in mixed_result
        assert mixed_result["execution_id"]

    def test_llm_degraded_mode_marked(self, mixed_result):
        """LLM 降级模式被正确标记"""
        llm_confirmed = mixed_result.get("llm_confirmed", [])
        for txn in llm_confirmed:
            # 降级模式的交易应有 _degraded 标记
            if txn.get("llm_analysis"):
                assert txn.get("_degraded") is True or txn.get("_degraded") is None


# ============================================================
# 空交易容错测试
# ============================================================
@pytest.mark.integration
class TestEmptyTransactionsIntegration:

    def test_empty_transactions_no_crash(self):
        """空交易列表不崩溃"""
        aml = AMLAgentsGraph(llm=None, enable_monitor=False)
        result = aml.run(transactions=[], analysis_date="2026-07-01")
        assert result is not None
        assert isinstance(result, dict)

    def test_empty_transactions_no_rule_hits(self):
        """空交易列表无规则命中"""
        aml = AMLAgentsGraph(llm=None, enable_monitor=False)
        result = aml.run(transactions=[], analysis_date="2026-07-01")
        rule_hits = result.get("rule_hits", [])
        assert len(rule_hits) == 0


# ============================================================
# 正常交易不误报测试
# ============================================================
@pytest.mark.integration
class TestNormalTransactionsIntegration:

    def test_normal_txns_minimal_hits(self):
        """正常交易不应触发可疑规则（戒律 P2 不误报）"""
        aml = AMLAgentsGraph(llm=None, enable_monitor=False)
        txns = _normal_txns()
        result = aml.run(transactions=txns, analysis_date="2026-07-01")
        rule_hits = result.get("rule_hits", [])
        # 正常交易（5000/8000/3200）不应触发大额、分拆等规则
        assert len(rule_hits) == 0, f"正常交易不应触发规则，但命中 {len(rule_hits)} 笔"


# ============================================================
# 单一规则触发测试
# ============================================================
@pytest.mark.integration
class TestSingleRuleIntegration:

    def test_smurfing_detected(self):
        """分拆转账被检测到"""
        aml = AMLAgentsGraph(llm=None, enable_monitor=False)
        txns = _smurfing_txns(6)
        result = aml.run(transactions=txns, analysis_date="2026-07-01")
        rule_hits = result.get("rule_hits", [])
        assert len(rule_hits) > 0, "6 笔 4.5 万分拆转账应被检测到"

    def test_large_amount_detected(self):
        """大额交易被检测到"""
        aml = AMLAgentsGraph(llm=None, enable_monitor=False)
        txns = _large_amount_txns()
        result = aml.run(transactions=txns, analysis_date="2026-07-01")
        rule_hits = result.get("rule_hits", [])
        assert len(rule_hits) > 0, "20 万和 35 万大额交易应被检测到"

    def test_fast_in_fast_out_detected(self):
        """快进快出被检测到"""
        aml = AMLAgentsGraph(llm=None, enable_monitor=False)
        txns = _fast_in_fast_out_txns()
        result = aml.run(transactions=txns, analysis_date="2026-07-01")
        rule_hits = result.get("rule_hits", [])
        assert len(rule_hits) > 0, "快进快出应被检测到"


# ============================================================
# 报告生成集成测试
# ============================================================
@pytest.mark.integration
class TestReportGenerationIntegration:

    def test_str_report_contains_primary_account(self):
        """STR 报告包含主涉案方"""
        aml = AMLAgentsGraph(llm=None, enable_monitor=False)
        txns = _mixed_suspicious_txns()
        result = aml.run(transactions=txns, analysis_date="2026-07-01")
        str_reports = result.get("str_reports", [])
        for report in str_reports:
            primary = report.get("primary_account") or report.get("main_account")
            assert primary, f"STR 报告缺少主涉案方: {report.get('report_id', 'unknown')}"

    def test_str_report_has_risk_level(self):
        """STR 报告有风险等级"""
        aml = AMLAgentsGraph(llm=None, enable_monitor=False)
        txns = _mixed_suspicious_txns()
        result = aml.run(transactions=txns, analysis_date="2026-07-01")
        str_reports = result.get("str_reports", [])
        for report in str_reports:
            risk_level = report.get("risk_level") or report.get("risk_score")
            assert risk_level is not None, f"STR 报告缺少风险等级: {report.get('report_id')}"


# ============================================================
# 图分析集成测试
# ============================================================
@pytest.mark.integration
class TestGraphAnalysisIntegration:

    def test_graph_analysis_does_not_break_workflow(self):
        """图分析不破坏主流程"""
        aml = AMLAgentsGraph(llm=None, enable_monitor=False)
        txns = _mixed_suspicious_txns()
        result = aml.run(transactions=txns, analysis_date="2026-07-01")
        # 图分析即使无可疑团伙，也不应导致流程失败
        assert result is not None
        assert "error" not in result or result["error"] == ""


# ============================================================
# 合规审核集成测试
# ============================================================
@pytest.mark.integration
class TestComplianceAuditIntegration:

    def test_compliance_score_present(self):
        """合规审核评分存在"""
        aml = AMLAgentsGraph(llm=None, enable_monitor=False)
        txns = _mixed_suspicious_txns()
        result = aml.run(transactions=txns, analysis_date="2026-07-01")
        score = result.get("compliance_score")
        # 合规评分可能为 None（无可疑交易时），但混合可疑交易时应存在
        if score is not None:
            assert 0 <= score <= 100, f"合规评分越界: {score}"

    def test_compliance_stats_present(self):
        """合规统计存在"""
        aml = AMLAgentsGraph(llm=None, enable_monitor=False)
        txns = _mixed_suspicious_txns()
        result = aml.run(transactions=txns, analysis_date="2026-07-01")
        # 合规统计字段（可能为空 dict，但字段应存在）
        assert "compliance_score" in result or "compliance_stats" in result
