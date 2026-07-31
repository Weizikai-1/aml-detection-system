"""Agent 工作流 + Demo 注入器测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from graph.workflow import run_sequential, _merge_analysis, _route_by_risk


def _state(n=100, demo=False):
    return {"n_samples": n, "errors": [], "demo_mode": demo}


class TestWorkflow:
    def test_empty_state_runs(self):
        """空状态也应正常返回"""
        state = run_sequential(_state(100))
        assert isinstance(state, dict)
        assert "str_report" in state
        assert "compliance" in state

    def test_demo_mode_injects_high_risk(self):
        """Demo 模式应注入高风险交易并触发更多规则"""
        state = run_sequential(_state(100, demo=True))
        rr = state.get("rule_report", {})
        rs = rr.get("summary", {})
        by_rule = rs.get("by_rule", {})
        assert len(by_rule) > 1, f"仅触发 {len(by_rule)} 条规则"
        assert rs.get("high_risk", 0) > 0, "Demo 模式应有高风险命中"

    def test_str_report_structure(self):
        """STR 报告应包含必要章节"""
        state = run_sequential(_state(50, demo=True))
        report = state.get("str_report", "")
        assert "可疑交易报告" in report
        assert "数据概览" in report
        assert "规则引擎" in report
        assert "建议措施" in report

    def test_compliance_passes(self):
        """合规审核应对有效报告通过"""
        state = run_sequential(_state(50, demo=True))
        assert state.get("compliance", {}).get("passed")

    def test_report_not_empty(self):
        """生成的报告不应为空"""
        state = run_sequential(_state(50))
        report = state.get("str_report", "")
        assert len(report) > 100, f"报告太短: {len(report)} 字符"

    def test_messages_bus_exists(self):
        """State 中应有 messages 总线记录各 Agent 产出"""
        state = run_sequential(_state(50, demo=True))
        messages = state.get("messages", [])
        assert len(messages) >= 3, f"messages 总线条目不足: {len(messages)}"
        agents = {m.get("agent") for m in messages}
        assert "data_preprocess" in agents
        assert "rule_engine" in agents

    def test_rule_report_structure(self):
        """rule_report 应包含 hits, summary, high_risk 三个子结构"""
        state = run_sequential(_state(50, demo=True))
        rr = state.get("rule_report", {})
        assert "hits" in rr
        assert "summary" in rr
        assert "high_risk" in rr


class TestDemoInjector:
    def test_injects_transactions(self):
        from agents.demo_injector import inject_demo_txns
        txns = [{"step": 1, "type": "TRANSFER", "amount": 100, "nameOrig": "A", "nameDest": "B"}]
        result = inject_demo_txns(txns, seed=42)
        assert len(result) > len(txns), "应增加了交易"
        demo_txns = [t for t in result if t.get("_demo")]
        assert len(demo_txns) > 3, f"Demo 交易过少: {len(demo_txns)}"

    def test_contains_sanction(self):
        from agents.demo_injector import inject_demo_txns
        result = inject_demo_txns([], seed=42)
        has_sanction = any("SDN001" in str(t.get("nameOrig", "")) for t in result)
        assert has_sanction, "应包含制裁名单交易"

    def test_contains_smurfing(self):
        from agents.demo_injector import inject_demo_txns
        result = inject_demo_txns([], seed=42)
        by_dest = {}
        for t in result:
            if t.get("_demo"):
                d = t.get("nameDest", "")
                by_dest[d] = by_dest.get(d, 0) + 1
        assert any(v >= 5 for v in by_dest.values()), "应有≥5笔同收款方"


class TestMergeRoute:
    def test_low_risk_routes_to_report(self):
        state = {"rule_report": {"summary": {"high_risk": 0, "total_hits": 10},
                                  "high_risk": []}}
        assert _route_by_risk(state) == "report_gen"

    def test_high_risk_routes_to_llm(self):
        state = {"rule_report": {"summary": {"high_risk": 1},
                                  "high_risk": [{"rules": ["sanction_list"], "risk_score": 95}]}}
        assert _route_by_risk(state) == "llm_review"

    def test_merge_is_sync_point(self):
        """merge_analysis 不应复制数据，只做同步 + 写 messages"""
        state = {"rule_report": {"summary": {"total_hits": 5, "high_risk": 2}},
                 "gnn_enabled": True}
        merged = _merge_analysis(state)
        # merge_analysis 不再复制 data_summary / high_risk_txns 等字段
        assert "data_summary" not in merged
        assert "high_risk_txns" not in merged
        assert "messages" in merged
        assert merged["current_step"] == "综合分析"
