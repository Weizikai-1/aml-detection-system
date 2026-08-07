"""
AML 反洗钱多智能体检测系统 — Streamlit 界面
启动: streamlit run app.py
"""
import os
import sys
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st

st.set_page_config(
    page_title="AML 反洗钱检测系统",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---- 样式 ----
st.markdown("""
<style>
    .stApp { max-width: 1400px; }
    .agent-card {
        border-left: 4px solid #4CAF50;
        padding: 10px 15px;
        margin: 8px 0;
        background: #f5f5f5;
        border-radius: 4px;
    }
    .high-risk { border-left-color: #f44336; background: #fff5f5; }
    .medium-risk { border-left-color: #ff9800; background: #fff8f0; }
    .low-risk { border-left-color: #2196F3; background: #f0f8ff; }
    .metric-value { font-size: 2em; font-weight: bold; }
    .step-label { font-size: 0.8em; color: #888; }
</style>
""", unsafe_allow_html=True)

# ---- 标题 ----
col1, col2 = st.columns([3, 1])
with col1:
    st.title("🔍 AML 反洗钱多智能体检测系统")
    st.caption("基于 LangGraph 的 6-Agent 协同工作流 | 20条检测规则 | Kaggle PaySim 数据")
with col2:
    st.image("https://img.shields.io/badge/LangGraph-6_Agents-blue", width=200)

# ---- 侧边栏 ----
with st.sidebar:
    st.header("⚙️ 配置")
    n_samples = st.slider("样本数量", 100, 10000, 2000, 500)
    demo_mode = st.checkbox("🧪 Demo 模式 (注入高风险样本)", value=True,
                            help="注入制裁名单/洗钱关键词/跨境/分拆转账等高风险交易，"
                                 "触发 LLM 深审全链路")
    run_btn = st.button("🚀 启动检测", type="primary", use_container_width=True)

    st.divider()
    st.header("📋 系统信息")
    st.caption(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        from data_loader import get_source_label
        st.caption(f"数据: {get_source_label()}")
    except Exception:
        pass

    try:
        from llm.deepseek_client import DeepSeekClient
        llm_client = DeepSeekClient()
        st.caption(f"LLM: {'DeepSeek ✅' if llm_client.is_available() else '未配置 ⚠'}")
    except Exception:
        st.caption("LLM: 未配置")

    try:
        from gnn_model import is_available
        st.caption(f"GNN: {'可用 ✅ (GAT/SAGE/GCN)' if is_available() else '未安装 ⚠'}")
    except Exception:
        st.caption("GNN: 未安装")


def run_pipeline():
    """运行完整检测流水线 — 真实进度通过 LangGraph streaming"""
    from graph.workflow import build_workflow, run_sequential

    wf = build_workflow()
    state = {"n_samples": n_samples, "demo_mode": demo_mode, "errors": []}

    progress = st.progress(0, "启动 6-Agent 工作流...")
    status = st.empty()

    agent_names = [
        "data_preprocess", "rule_engine", "graph_analyst",
        "merge_analysis", "llm_review", "report_gen", "compliance",
    ]
    agent_labels = {
        "data_preprocess": "数据预处理",
        "rule_engine": "规则引擎",
        "graph_analyst": "GNN 图分析",
        "merge_analysis": "综合分析",
        "llm_review": "LLM 深审",
        "report_gen": "报告生成",
        "compliance": "合规审核",
    }
    step_map = {name: i / len(agent_names) for i, name in enumerate(agent_names)}

    try:
        if wf is not None:
            # 使用 LangGraph stream 获取真实节点执行进度
            for chunk in wf.stream(state):
                for node_name in chunk:
                    label = agent_labels.get(node_name, node_name)
                    pct = step_map.get(node_name, 0.5)
                    progress.progress(pct, f"[{node_name}] {label}")
                    status.info(f"⏳ {label} 执行中...")
            # 重新获取完整 state（stream 返回的是增量）
            final = wf.invoke(state)
        else:
            final = run_sequential(state)
            progress.progress(1.0, "串行回退模式完成")
    except Exception as e:
        # stream 不支持时回退到单次 invoke
        if wf is not None:
            final = wf.invoke(state)
        else:
            final = run_sequential(state)

    progress.progress(1.0, "✅ 检测完成")
    status.success("✅ 6 Agent 协同检测完成")

    return final


# ---- 主区域 ----
if run_btn:
    with st.spinner("多智能体检测进行中..."):
        final = run_pipeline()

    st.divider()

    # 1. 指标卡片
    ds = final.get("data_summary", {})
    rr = final.get("rule_report", {})
    rs = rr.get("summary", {})
    gn = final.get("gnn_report", {})
    comp = final.get("compliance", {})

    st.subheader("📊 检测概览")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("总交易", f"{ds.get('total', 0):,}", delta=None)
    c2.metric("规则命中", rs.get("total_hits", 0),
              delta=f"高:{rs.get('high_risk', 0)} 中:{rs.get('medium_risk', 0)}")
    c3.metric("LLM 深审", len(final.get("llm_reviews", [])),
              delta="已触发" if final.get("llm_reviews") else "未触发")
    c4.metric("GNN F1", f"{gn.get('node_f1', 0):.3f}" if gn else "N/A",
              delta=f"{final.get('gnn_enabled', False) and '✅' or '⚪'}")
    c5.metric("合规", f"{comp.get('score', 0)}/100" if comp else "N/A",
              delta="✅" if comp.get("passed") else "❌")

    # 2. Agent 执行追踪
    st.divider()
    st.subheader("🔗 Agent 执行流水线")

    agents_data = [
        ("数据预处理", True, f"加载 {ds.get('total', 0):,} 条交易, 欺诈率 {ds.get('fraud_rate', 'N/A')}"),
        ("规则引擎", True,
         f"命中 {rs.get('total_hits', 0)} 笔 "
         f"(高:{rs.get('high_risk', 0)}/中:{rs.get('medium_risk', 0)}/低:{rs.get('low_risk', 0)})"),
        ("GNN 图分析", final.get("gnn_enabled", False),
         f"F1={gn.get('node_f1', 0):.4f}" if gn else "PyTorch 未安装"),
        ("LLM 深审", len(final.get("llm_reviews", [])) > 0,
         f"审核 {len(final.get('llm_reviews', []))} 笔高风险交易"),
        ("报告生成", bool(final.get("str_report")),
         f"STR 报告 {len(final.get('str_report', ''))} 字符"),
        ("合规审核", comp.get("passed", False),
         f"{comp.get('status', 'N/A')}"),
    ]

    for name, ok, detail in agents_data:
        risk_class = ""
        if "高风险" in detail:
            risk_class = "high-risk"
        elif "LLM" in name and ok:
            risk_class = "high-risk"
        elif "规则引擎" in name:
            risk_class = "medium-risk" if rs.get("high_risk", 0) > 0 else "low-risk"

        color = "🟢" if ok else "⚪"
        st.markdown(
            f'<div class="agent-card {risk_class}">'
            f'<b>{color} {name}</b> <span class="step-label">{detail}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # 3. 规则命中分布 (Top 10)
    if rs.get("by_rule"):
        st.divider()
        st.subheader("🎯 规则命中分布")
        import pandas as pd
        rule_df = pd.DataFrame(
            sorted(rs["by_rule"].items(), key=lambda x: -x[1])[:10],
            columns=["规则", "命中数"]
        )
        st.bar_chart(rule_df.set_index("规则"))

    # 4. LLM 深审结果
    llm_results = final.get("llm_reviews", [])
    if llm_results:
        st.divider()
        st.subheader("🤖 LLM 深度审核结果")
        for i, r in enumerate(llm_results, 1):
            a = r.get("llm_analysis", {})
            with st.expander(
                f"{i}. [{a.get('suspicion_level', '?').upper()}] "
                f"{r.get('rule', '?')} (风险分: {r.get('risk_score', 0)})",
                expanded=(i <= 3),
            ):
                st.markdown(f"**嫌疑等级**: `{a.get('suspicion_level', 'N/A')}`")
                st.markdown(f"**洗钱类型**: {a.get('typology', 'N/A')}")
                st.markdown(f"**分析**: {a.get('reasoning', 'N/A')}")
                st.markdown(f"**建议**: {a.get('recommendation', 'N/A')}")

    # 5. STR 报告
    report = final.get("str_report", "")
    if report:
        st.divider()
        st.subheader("📄 可疑交易报告 (STR)")
        with st.expander("查看完整 STR 报告", expanded=True):
            st.markdown(report)

    # 6. 合规评分详情
    if comp:
        st.divider()
        st.subheader("📋 合规审核详情")
        cols = st.columns(2)
        with cols[0]:
            st.metric("评分", f"{comp.get('score', 0)}/100")
            st.caption(f"状态: {comp.get('status', 'N/A')}")
        with cols[1]:
            warnings = comp.get("warnings", [])
            if warnings:
                st.warning("\n".join(f"- {w}" for w in warnings))
            issues = comp.get("issues", [])
            if issues:
                st.error("\n".join(f"- {i}" for i in issues[:5]))

    # 7. Messages 总线追踪
    messages = final.get("messages", [])
    if messages:
        st.divider()
        st.subheader("📨 Agent 通信总线 (messages)")
        msgs_df = []
        for m in messages:
            msgs_df.append({
                "Agent": m.get("agent", "?"),
                "状态": m.get("status", "?"),
                "摘要": m.get("summary", "")[:60],
            })
        st.dataframe(msgs_df, use_container_width=True, hide_index=True)

    # 技术栈
    st.divider()
    st.caption(
        "LangGraph StateGraph + DeepSeek LLM (retry/timeout/fallback) + "
        "GAT/GCN/GraphSAGE 图神经网络 + "
        "20条 YAML 驱动检测规则 + 反思记忆 + 央行格式合规审核 | "
        "数据: Kaggle PaySim 636万笔真实交易"
    )

else:
    st.info("👈 点击左侧 **启动检测** 开始多智能体反洗钱分析")
    st.markdown("""
    ### 系统能力

    | 模块 | 技术 | 说明 |
    |------|------|------|
    | **工作流引擎** | LangGraph StateGraph | 6 Agent 并行协同 + 条件路由 |
    | **规则引擎** | 20条 YAML 驱动规则 | 分拆转账/快进快出/对敲/大额/制裁名单/跨境/循环转账等 |
    | **图分析** | GAT/GCN/GraphSAGE | 资金流向图谱 + 节点分类 |
    | **LLM 审核** | DeepSeek API | 高风险交易语义分析 + 反思记忆 + 重试/超时/fallback |
    | **合规** | 央行格式 + 内容校验 | 9项结构检查 + 证据链 + 风险合理性 + 百分制评分 |
    | **API 服务** | FastAPI | `POST /detect` + `GET /health` + `GET /report/{id}` |

    ### 执行流程

    ```
    数据预处理 → 规则引擎 ──┐
               → GNN 图分析 ─┤→ 综合分析 → 高风险?→LLM深审→报告→合规
    ```
    """)
