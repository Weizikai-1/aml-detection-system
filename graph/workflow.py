"""
LangGraph 工作流编排 — 6 Agent 并行协同
"""
import logging
from datetime import datetime
from graph.state import AMLState

log = logging.getLogger("aml.workflow")

_LG_AVAILABLE = False
try:
    from langgraph.graph import StateGraph, START, END
    _LG_AVAILABLE = True
except ImportError:
    pass


def build_workflow():
    if not _LG_AVAILABLE:
        log.warning("LangGraph 未安装，使用串行回退")
        return None
    from agents.data_preprocess import run as preprocess
    from agents.rule_engine_agent import run as rule_engine
    from agents.graph_analyst import run as graph_analyst
    from agents.llm_reviewer import run as llm_review
    from agents.report_generator import run as report_gen
    from agents.compliance import run as compliance
    wf = StateGraph(AMLState)
    wf.add_node("data_preprocess", preprocess)
    wf.add_node("rule_engine", rule_engine)
    wf.add_node("graph_analyst", graph_analyst)
    wf.add_node("merge_analysis", _merge_analysis)
    wf.add_node("llm_review", llm_review)
    wf.add_node("report_gen", report_gen)
    wf.add_node("compliance", compliance)
    wf.add_edge(START, "data_preprocess")
    wf.add_edge("data_preprocess", "rule_engine")
    wf.add_edge("data_preprocess", "graph_analyst")
    wf.add_edge("rule_engine", "merge_analysis")
    wf.add_edge("graph_analyst", "merge_analysis")
    wf.add_conditional_edges("merge_analysis", _route_by_risk,
        {"llm_review": "llm_review", "report_gen": "report_gen"})
    wf.add_edge("llm_review", "report_gen")
    wf.add_edge("report_gen", "compliance")
    wf.add_edge("compliance", END)
    log.info("LangGraph 并行工作流已构建")
    return wf.compile()


def _merge_analysis(state: AMLState) -> dict:
    """同步汇聚节点 — 仅作为 fan-in 同步点，不复制数据。"""
    rr = state.get("rule_report", {})
    gnn = state.get("gnn_enabled", False)
    summary = rr.get("summary", {})
    gnn_label = "启用" if gnn else "未启用"
    return {
        "current_step": "综合分析",
        "messages": [{
            "agent": "merge_analysis",
            "timestamp": datetime.now().isoformat(),
            "summary": "规则命中 {} 笔, 高风险 {}, GNN={}".format(
                summary.get("total_hits", 0), summary.get("high_risk", 0), gnn_label),
            "status": "ok",
        }],
    }


def _route_by_risk(state: AMLState) -> str:
    """条件路由：rule_report.high_risk 非空 -> LLM 深审"""
    rr = state.get("rule_report", {})
    high_risk = rr.get("high_risk", [])
    if high_risk:
        n = len(high_risk)
        rules = set()
        for h in high_risk:
            rules.update(h.get("rules", []))
        log.info(f"路由: {n} 笔高风险 -> LLM 深审 (规则: {rules})")
        return "llm_review"
    summary = rr.get("summary", {})
    total = summary.get("total_hits", 0)
    label = "低/中风险" if total > 0 else "无命中"
    log.info(f"路由: {label} -> 直接报告")
    return "report_gen"


def run_sequential(state: dict) -> dict:
    """串行回退 — LangGraph 不可用时的纯 Python 替代"""
    from agents.data_preprocess import run as preprocess
    from agents.rule_engine_agent import run as rule_engine
    from agents.graph_analyst import run as graph_analyst
    from agents.llm_reviewer import run as llm_review
    from agents.report_generator import run as report_gen
    from agents.compliance import run as compliance
    log.info("=== 串行回退模式 ===")
    state["messages"] = []
    for agent in [preprocess, rule_engine, graph_analyst, _merge_analysis]:
        upd = agent(state)
        msgs = upd.pop("messages", [])
        state.update(upd)
        state["messages"].extend(msgs)
    rr = state.get("rule_report", {})
    if rr.get("high_risk"):
        upd = llm_review(state)
        msgs = upd.pop("messages", [])
        state.update(upd)
        state["messages"].extend(msgs)
    for agent in [report_gen, compliance]:
        upd = agent(state)
        msgs = upd.pop("messages", [])
        state.update(upd)
        state["messages"].extend(msgs)
    return state
