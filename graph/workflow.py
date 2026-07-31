"""
LangGraph 工作流编排 — 6 Agent 并行协同
类比 TradingAgents 的 Analyst Team 并行模式

Agent 流水线 (并行版):
    数据预处理
        ↓
    ┌───────────────┬───────────────┐
    ▼               ▼               ▼
  规则引擎        GNN图分析       [预留扩展]
    │               │
    └───────┬───────┘
            ▼
      综合分析 (merge)
            │
    ┌───────▼────────┐
    │ 高风险 ≥70?     │
    ├───────┬────────┤
    │ Yes   │ No     │
    ▼       ▼        │
  LLM深审  报告生成──┘
    │       │
    └───┬───┘
        ▼
    合规审核 → END
"""
import logging
from graph.state import AMLState

log = logging.getLogger("aml.workflow")

_LG_AVAILABLE = False
try:
    from langgraph.graph import StateGraph, START, END
    _LG_AVAILABLE = True
except ImportError:
    pass


def build_workflow():
    """构建 LangGraph 并行工作流"""
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

    # 注册节点
    wf.add_node("data_preprocess", preprocess)
    wf.add_node("rule_engine", rule_engine)
    wf.add_node("graph_analyst", graph_analyst)
    wf.add_node("merge_analysis", _merge_analysis)
    wf.add_node("llm_review", llm_review)
    wf.add_node("report_gen", report_gen)
    wf.add_node("compliance", compliance)

    # 阶段1: 预处理
    wf.add_edge(START, "data_preprocess")

    # 阶段2: 并行分析 (同 super-step)
    wf.add_edge("data_preprocess", "rule_engine")
    wf.add_edge("data_preprocess", "graph_analyst")

    # 阶段3: 汇总 (等待两个并行节点都完成)
    wf.add_edge("rule_engine", "merge_analysis")
    wf.add_edge("graph_analyst", "merge_analysis")

    # 阶段4: 路由 → LLM 深审 或 直接报告
    wf.add_conditional_edges(
        "merge_analysis",
        _route_by_risk,
        {"llm_review": "llm_review", "report_gen": "report_gen"},
    )
    wf.add_edge("llm_review", "report_gen")

    # 阶段5: 报告 + 合规
    wf.add_edge("report_gen", "compliance")
    wf.add_edge("compliance", END)

    compiled = wf.compile()
    log.info("LangGraph 并行工作流已构建")
    return compiled


def _merge_analysis(state: AMLState) -> dict:
    """综合分析节点 — 规则 + GNN 结果汇总"""
    rs = state.get("rule_summary", {})
    gn = state.get("graph_report", {})

    has_gnn = state.get("gnn_enabled", False)
    total_risk = rs.get("high_risk", 0) + rs.get("medium_risk", 0)

    return {
        "current_step": "综合分析",
        "data_summary": state.get("data_summary", {}),
        "rule_summary": rs,
        "graph_report": gn,
        "high_risk_txns": state.get("high_risk_txns", []),
        "error_count": state.get("error_count", 0),
    }


def _route_by_risk(state: AMLState) -> str:
    """条件路由：有高风险(≥70分)交易 → LLM 深审"""
    high_risk = state.get("high_risk_txns", [])
    if high_risk:
        n = len(high_risk)
        all_rules = set()
        for h in high_risk:
            all_rules.update(h.get("rules", []))
        log.info(f"路由: {n} 笔高风险 → LLM 深审 (规则: {all_rules})")
        return "llm_review"
    rs = state.get("rule_summary", {})
    if rs.get("total_hits", 0) > 0:
        log.info(f"路由: 低/中风险 {rs.get('total_hits')} 笔 → 直接报告")
    else:
        log.info("路由: 无命中 → 直接报告")
    return "report_gen"


def run_sequential(state: dict) -> dict:
    """串行回退 — LangGraph 不可用时"""
    from agents.data_preprocess import run as preprocess
    from agents.rule_engine_agent import run as rule_engine
    from agents.graph_analyst import run as graph_analyst
    from agents.llm_reviewer import run as llm_review
    from agents.report_generator import run as report_gen
    from agents.compliance import run as compliance

    log.info("=== 串行回退模式 ===")
    state.update(preprocess(state))
    state.update(rule_engine(state))
    state.update(graph_analyst(state))
    state.update(_merge_analysis(state))

    if state.get("high_risk_txns"):
        state.update(llm_review(state))
    state.update(report_gen(state))
    state.update(compliance(state))
    return state
