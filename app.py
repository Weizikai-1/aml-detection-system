"""
AML-Agent 反洗钱分析系统 - Streamlit 可视化界面

功能:
- 数据上传 (CSV/Excel/JSON)
- 检测统计仪表盘
- 可疑交易列表
- 资金流向图
- STR 可疑报告详情 + 导出
"""
import os
import sys
import json
import tempfile
from datetime import datetime

import streamlit as st
import pandas as pd

# 添加项目根目录
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DATA_DIR, REPORTS_DIR, has_llm as config_has_llm
from tools.data_generator import generate_test_data
from tools.data_importer import import_transactions
from graph.workflow import AMLAgentsGraph


# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="AML-Agent 反洗钱分析系统",
    page_icon="🔍",
    layout="wide",
)

# 初始化 session state
if "analysis_result" not in st.session_state:
    st.session_state.analysis_result = None
if "transactions" not in st.session_state:
    st.session_state.transactions = None
if "analysis_done" not in st.session_state:
    st.session_state.analysis_done = False
# 模拟数据生成参数 session state
if "sim_normal_count" not in st.session_state:
    st.session_state.sim_normal_count = 120
if "sim_suspicious_modes" not in st.session_state:
    st.session_state.sim_suspicious_modes = ["smurfing", "fast_in_fast_out", "round_trip", "large_amount"]


# ============================================================
# 侧边栏
# ============================================================
with st.sidebar:
    st.title("🔍 AML-Agent")
    st.subheader("反洗钱多智能体分析系统")
    st.markdown("---")

    # 数据源选择
    st.markdown("### 📊 数据源")
    data_source = st.radio(
        "选择数据来源",
        ["模拟数据", "上传文件"],
        horizontal=True,
    )

    uploaded_file = None
    if data_source == "上传文件":
        uploaded_file = st.file_uploader(
            "上传交易数据",
            type=["csv", "xlsx", "xls", "json"],
            help="支持 CSV、Excel、JSON 格式",
        )
    else:
        normal_count = st.slider("正常交易数量", 50, 300, st.session_state.sim_normal_count, key="sim_normal_count")
        suspicious_modes = st.multiselect(
            "可疑交易模式",
            ["smurfing", "fast_in_fast_out", "round_trip", "large_amount"],
            default=st.session_state.sim_suspicious_modes,
            key="sim_suspicious_modes",
        )

    st.markdown("---")

    # LLM 选项
    llm_available = config_has_llm()
    use_llm = st.checkbox("启用 LLM 深审", value=llm_available, disabled=not llm_available)
    if not llm_available:
        st.caption("⚠️ 未配置 LLM API Key，将使用降级模式")

    st.markdown("---")

    # 分析按钮
    analyze_btn = st.button("🚀 开始分析", type="primary", use_container_width=True)


# ============================================================
# 主内容区
# ============================================================
st.title("🔍 反洗钱交易智能分析平台")
st.caption(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


def load_transactions():
    """加载交易数据"""
    if data_source == "模拟数据":
        return generate_test_data(
            normal_count=st.session_state.sim_normal_count,
            suspicious_modes=st.session_state.sim_suspicious_modes,
        )
    elif uploaded_file is not None:
        # 保存到临时文件
        suffix = os.path.splitext(uploaded_file.name)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded_file.getvalue())
            tmp_path = tmp.name
        try:
            result = import_transactions(tmp_path, strict=False)
            return result["transactions"]
        finally:
            os.unlink(tmp_path)
    return None


def run_analysis(transactions):
    """执行分析"""
    llm = None
    if use_llm and config_has_llm():
        try:
            from tools.llm_client import get_llm
            llm = get_llm()
        except Exception:
            llm = None

    aml_system = AMLAgentsGraph(llm=llm)
    result = aml_system.run(transactions=transactions, debug=False)
    return result


# 点击分析按钮
if analyze_btn:
    with st.spinner("正在加载交易数据..."):
        transactions = load_transactions()
        if transactions is None or len(transactions) == 0:
            st.error("未加载到有效交易数据")
            st.stop()
        st.session_state.transactions = transactions

    with st.spinner(f"正在分析 {len(transactions)} 笔交易..."):
        result = run_analysis(transactions)
        st.session_state.analysis_result = result
        st.session_state.analysis_done = True

    st.success(f"分析完成！共处理 {len(transactions)} 笔交易")
    st.rerun()


# ============================================================
# 分析结果展示
# ============================================================
if not st.session_state.analysis_done or st.session_state.analysis_result is None:
    st.info("👈 请在左侧选择数据源并点击「开始分析」")

    # 展示系统架构
    st.markdown("---")
    st.subheader("📋 系统架构")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("""
        **🔧 Agent 1: 数据预处理**
        - 数据清洗去重
        - 缺失值填充
        - 金额分级
        - 账户行为基线
        """)
    with col2:
        st.markdown("""
        **⚖️ Agent 2: 规则引擎**
        - 分拆转账检测
        - 快进快出检测
        - 对敲交易检测
        - 大额交易检测
        - 基线偏离检测
        - 备注关键词检测
        """)
    with col3:
        st.markdown("""
        **🕸️ Agent 3: 图分析**
        - 资金网络建图
        - 社区发现
        - GNN 节点分类
        - 团伙识别
        """)

    col4, col5, col6 = st.columns(3)
    with col4:
        st.markdown("""
        **🧠 Agent 4: LLM 深审**
        - 语义复核
        - 证据链验证
        - 误报排除
        - 并发批量处理
        """)
    with col5:
        st.markdown("""
        **📝 Agent 5: 报告生成**
        - STR 可疑报告
        - 按账户分组
        - 证据链整合
        - 风险等级评定
        """)
    with col6:
        st.markdown("""
        **✅ Agent 6: 合规审核**
        - 完整性校验
        - 证据充分性
        - 格式合规性
        - 业务戒律检查
        """)

    st.stop()


result = st.session_state.analysis_result
transactions = st.session_state.transactions

# ============================================================
# Tabs: 检测概览 / 可疑交易 / 资金流向图 / STR报告 / 规则调参 / 历史对比 / 记忆洞察 / GNN解释 / 系统监控
# ============================================================
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
    "📊 检测概览", "📋 可疑交易", "🕸️ 资金流向图", "📑 STR 报告", "⚙️ 规则调参",
    "📈 历史对比", "🧠 记忆洞察", "🔬 GNN 解释", "🖥️ 系统监控"
])

with tab1:
    st.subheader("📊 检测统计概览")

    rule_hits = result.get("rule_hits", [])
    llm_confirmed = result.get("llm_confirmed", [])
    false_positives = result.get("false_positives", [])
    str_reports = result.get("str_reports", [])
    rule_details = result.get("rule_details", {})
    compliance_score = result.get("compliance_score", None)

    # 顶部指标卡片
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("总交易数", len(transactions))
    with col2:
        st.metric("规则引擎命中", len(rule_hits), delta=f"{len(rule_details)} 种规则")
    with col3:
        st.metric("LLM 确认可疑", len(llm_confirmed), delta=f"排除 {len(false_positives)} 笔误报", delta_color="inverse")
    with col4:
        st.metric("生成可疑报告", len(str_reports))

    # 合规评分
    if compliance_score is not None:
        st.markdown("---")
        col_left, col_right = st.columns([1, 3])
        with col_left:
            st.metric("合规审核评分", f"{compliance_score:.1f}", delta="/ 100")
        with col_right:
            if compliance_score >= 90:
                st.success("✅ 合规审核通过，报告质量优秀")
            elif compliance_score >= 70:
                st.warning("⚠️ 合规审核基本通过，建议人工复核")
            else:
                st.error("❌ 合规审核未通过，请检查报告质量")

    # 各规则命中情况
    st.markdown("---")
    st.subheader("📈 各规则命中统计")
    if rule_details:
        rule_df = pd.DataFrame({
            "规则名称": list(rule_details.keys()),
            "命中笔数": list(rule_details.values()),
        })
        st.bar_chart(rule_df.set_index("规则名称"))

    # 风险等级分布
    if str_reports:
        st.markdown("---")
        st.subheader("🎯 可疑报告风险等级分布")
        risk_levels = {}
        for rpt in str_reports:
            level = rpt.get("risk_level", "unknown")
            risk_levels[level] = risk_levels.get(level, 0) + 1
        level_df = pd.DataFrame({
            "风险等级": list(risk_levels.keys()),
            "报告数量": list(risk_levels.values()),
        })
        st.bar_chart(level_df.set_index("风险等级"))


# ============================================================
# Tab 2: 可疑交易列表
# ============================================================
with tab2:
    st.subheader("📋 可疑交易清单")

    rule_hits = result.get("rule_hits", [])

    if not rule_hits:
        st.info("未发现可疑交易")
    else:
        # 表格数据
        table_data = []
        for s in rule_hits:
            txn = s.get("transaction", {})
            table_data.append({
                "交易ID": txn.get("transaction_id", "-"),
                "付款账户": txn.get("from_account", "-"),
                "收款账户": txn.get("to_account", "-"),
                "交易金额": txn.get("amount", 0),
                "交易时间": txn.get("timestamp", "-"),
                "命中规则": "、".join(s.get("rule_hits", [])),
                "风险评分": s.get("risk_score", 0),
                "备注": txn.get("remark", "-"),
            })

        df = pd.DataFrame(table_data)
        df = df.sort_values("风险评分", ascending=False)

        # 风险等级颜色标注
        def color_risk(val):
            if val >= 85:
                return "background-color: #ffe0e0; color: #c00"
            elif val >= 70:
                return "background-color: #fff3e0; color: #e67e22"
            elif val >= 50:
                return "background-color: #fffde0; color: #f39c12"
            return ""

        st.dataframe(
            df.style.map(color_risk, subset=["风险评分"]),
            use_container_width=True,
            height=500,
        )

        # 筛选器
        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            min_score = st.slider("最低风险评分", 0, 100, 50)
        with col2:
            selected_rules = st.multiselect(
                "筛选规则",
                list(set(r for s in rule_hits for r in s.get("rule_hits", []))),
            )

        filtered = df[df["风险评分"] >= min_score]
        if selected_rules:
            filtered = filtered[filtered["命中规则"].apply(
                lambda x: any(r in x for r in selected_rules)
            )]
        st.caption(f"筛选后共 {len(filtered)} 笔交易")


# ============================================================
# Tab 3: 资金流向图
# ============================================================
with tab3:
    st.subheader("🕸️ 资金流向图")
    st.caption("展示可疑交易的资金流向网络（红色=高风险账户，黄色=中风险）")

    rule_hits = result.get("rule_hits", [])
    str_reports = result.get("str_reports", [])
    graph_data = result.get("graph_data", {})

    if not rule_hits:
        st.info("无可疑交易数据")
    else:
        # 顶部操作：生成交互式 HTML 可视化
        st.markdown("#### 📊 交互式可视化")
        st.caption(
            "基于 graph_analyst 真实输出（账户节点、资金路径、可疑社区），生成自包含 HTML 报告（戒律 M1: 真实数据）"
        )
        col_v1, col_v2 = st.columns([1, 2])
        with col_v1:
            if st.button("🌐 生成交互式资金流向图", type="primary", use_container_width=True):
                try:
                    from tools.flow_visualizer import FlowVisualizer
                    viz = FlowVisualizer()
                    exec_id = result.get("execution_id", "")
                    html_path = viz.generate_from_state(result)
                    st.success(f"✅ 可视化报告已生成")
                    with open(html_path, "r", encoding="utf-8") as f:
                        html_content = f.read()
                    st.download_button(
                        "📥 下载 HTML 报告",
                        data=html_content,
                        file_name=os.path.basename(html_path),
                        mime="text/html",
                    )
                    st.caption(f"文件路径: {html_path}")
                except Exception as e:
                    st.error(f"生成失败: {e}")
        with col_v2:
            st.info(
                "💡 提示：交互式 HTML 包含资金流向图谱、风险分布直方图、Top10 高风险账户表、可疑社区卡片，可在浏览器中缩放/平移/悬停查看详情。"
            )

        st.markdown("---")

        # 构建节点和边数据（保留 DataFrame 视图作为快速浏览）
        nodes = set()
        edges = []
        risk_scores = {}

        for s in rule_hits:
            txn = s.get("transaction", {})
            from_a = txn.get("from_account", "")
            to_a = txn.get("to_account", "")
            amount = txn.get("amount", 0)
            score = s.get("risk_score", 0)

            # 过滤自环交易（from == to），避免图可视化中出现无意义自环边
            if from_a and to_a and from_a == to_a:
                continue

            if from_a:
                nodes.add(from_a)
                risk_scores[from_a] = max(risk_scores.get(from_a, 0), score)
            if to_a:
                nodes.add(to_a)
                risk_scores[to_a] = max(risk_scores.get(to_a, 0), score)
            if from_a and to_a:
                edges.append((from_a, to_a, amount))

        # DataFrame 简要视图
        col1, col2 = st.columns([2, 1])

        with col1:
            st.markdown("#### 🔗 交易链路")
            edge_data = []
            for from_a, to_a, amount in edges:
                edge_data.append({
                    "付款账户": from_a,
                    "收款账户": to_a,
                    "交易金额": amount,
                })
            edge_df = pd.DataFrame(edge_data)
            st.dataframe(edge_df, use_container_width=True, height=400)

        with col2:
            st.markdown("#### 👤 高风险账户")
            high_risk = [
                (acc, score) for acc, score in risk_scores.items() if score >= 70
            ]
            high_risk.sort(key=lambda x: x[1], reverse=True)
            for acc, score in high_risk[:10]:
                if score >= 85:
                    st.error(f"**{acc}** — {score} 分")
                elif score >= 70:
                    st.warning(f"**{acc}** — {score} 分")
                else:
                    st.info(f"**{acc}** — {score} 分")


# ============================================================
# Tab 4: STR 报告
# ============================================================
with tab4:
    st.subheader("📑 可疑交易报告 (STR)")

    str_reports = result.get("str_reports", [])

    if not str_reports:
        st.info("未生成可疑报告")
    else:
        # 按风险排序
        str_reports_sorted = sorted(
            str_reports,
            key=lambda r: max((t.get("risk_score", 0) for t in r.get("suspicious_transactions", [])), default=0),
            reverse=True,
        )

        # 顶部：批量导出
        st.markdown("#### 📦 批量导出全部报告")
        st.caption(
            "一次性导出所有 STR 报告为 Excel + PDF，并生成 CSV 汇总表与 ZIP 打包（戒律 M1: 真实数据；P1: 不遗漏任何报告）"
        )
        batch_col1, batch_col2 = st.columns([1, 2])
        with batch_col1:
            if st.button("📦 批量导出 (Excel + PDF + ZIP)", type="primary", use_container_width=True):
                try:
                    from tools.batch_exporter import BatchExporter
                    exporter = BatchExporter()
                    batch_result = exporter.export(
                        str_reports_sorted,
                        batch_name=f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                        formats=["excel", "pdf"],
                        create_zip=True,
                        create_summary=True,
                    )
                    st.success(
                        f"✅ 批量导出完成：{batch_result['report_count']} 份报告，"
                        f"{len(batch_result['files'])} 个文件"
                    )
                    if batch_result.get("zip_path"):
                        with open(batch_result["zip_path"], "rb") as f:
                            zip_bytes = f.read()
                        st.download_button(
                            "📥 下载 ZIP 打包",
                            data=zip_bytes,
                            file_name=os.path.basename(batch_result["zip_path"]),
                            mime="application/zip",
                        )
                    if batch_result.get("summary_path"):
                        with open(batch_result["summary_path"], "r", encoding="utf-8-sig") as f:
                            csv_content = f.read()
                        st.download_button(
                            "📥 下载汇总表 CSV",
                            data=csv_content,
                            file_name="summary.csv",
                            mime="text/csv",
                        )
                    st.caption(f"批次目录: {batch_result['batch_dir']}")
                except Exception as e:
                    st.error(f"批量导出失败: {e}")
        with batch_col2:
            st.info(
                "💡 批量导出包含：每份报告的 Excel 和 PDF、汇总表（CSV）、ZIP 打包。"
                "Excel 便于数据分析，PDF 便于归档打印。"
            )

        st.markdown("---")

        # 报告选择器
        report_names = [
            f"报告 {i+1}: {r.get('primary_account', '未知')} ({r.get('risk_level', '')})"
            for i, r in enumerate(str_reports_sorted)
        ]
        selected_idx = st.selectbox("选择报告", range(len(report_names)), format_func=lambda i: report_names[i])

        report = str_reports_sorted[selected_idx]

        # 报告详情
        primary = report.get("primary_account", "-")
        risk_level = report.get("risk_level", "-")
        level_tag = {"critical": "🔴 极高风险", "high": "🟠 高风险", "medium": "🟡 中风险", "low": "🟢 低风险"}.get(risk_level, risk_level)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("主账户", primary)
        with col2:
            st.metric("风险等级", level_tag)
        with col3:
            txns = report.get("suspicious_transactions", [])
            max_score = max((t.get("risk_score", 0) for t in txns), default=0)
            st.metric("最高风险评分", f"{max_score:.0f}")

        # 可疑模式
        patterns = report.get("suspicious_patterns", [])
        if patterns:
            st.markdown("#### 🚨 可疑模式")
            st.write("、".join(patterns))

        # 证据链
        st.markdown("#### 📝 可疑交易明细")
        txn_table = []
        for s in report.get("suspicious_transactions", []):
            txn = s.get("transaction", {})
            txn_table.append({
                "交易ID": txn.get("transaction_id", "-"),
                "对手方": txn.get("to_account", "") if txn.get("from_account") == primary else txn.get("from_account", ""),
                "方向": "转出" if txn.get("from_account") == primary else "转入",
                "金额": txn.get("amount", 0),
                "时间": txn.get("timestamp", "-"),
                "命中规则": "、".join(s.get("rule_hits", [])),
                "评分": s.get("risk_score", 0),
            })
        txn_df = pd.DataFrame(txn_table)
        st.dataframe(txn_df, use_container_width=True)

        # 证据描述
        evidence = report.get("evidence_summary", "")
        if evidence:
            st.markdown("#### 🔍 证据摘要")
            st.write(evidence)

        # 导出按钮（戒律 M4: 证据链完整保留）
        st.markdown("---")
        st.markdown("#### 📥 单报告导出")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            json_str = json.dumps(report, ensure_ascii=False, indent=2, default=str)
            st.download_button(
                "📥 JSON",
                data=json_str,
                file_name=f"str_report_{primary}.json",
                mime="application/json",
            )
        with col2:
            # 生成 Markdown 报告
            md_content = f"""# 可疑交易报告 (STR)

**主账户**: {primary}
**风险等级**: {risk_level}
**报告生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 一、可疑模式
{', '.join(patterns) if patterns else '-'}

## 二、可疑交易明细

| 交易ID | 对手方 | 方向 | 金额 | 时间 | 命中规则 | 评分 |
|--------|--------|------|------|------|----------|------|
"""
            for row in txn_table:
                md_content += f"| {row['交易ID']} | {row['对手方']} | {row['方向']} | {row['金额']:,.2f} | {row['时间']} | {row['命中规则']} | {row['评分']} |\n"

            if evidence:
                md_content += f"\n## 三、证据摘要\n\n{evidence}\n"

            st.download_button(
                "📥 Markdown",
                data=md_content,
                file_name=f"str_report_{primary}.md",
                mime="text/markdown",
            )
        with col3:
            # Excel 导出（多 Sheet：报告概要 / 可疑交易明细 / 证据链 / 模式与处置）
            if st.button("📊 导出 Excel", use_container_width=True):
                try:
                    from tools.excel_exporter import ExcelExporter
                    excel_exporter = ExcelExporter()
                    xlsx_path = excel_exporter.export_report(report)
                    st.success(f"✅ Excel 已生成")
                    with open(xlsx_path, "rb") as f:
                        xlsx_bytes = f.read()
                    st.download_button(
                        "📥 下载 Excel",
                        data=xlsx_bytes,
                        file_name=os.path.basename(xlsx_path),
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                    st.caption(f"文件路径: {xlsx_path}")
                except Exception as e:
                    st.error(f"Excel 导出失败: {e}")
        with col4:
            # PDF 导出（专业格式，含中文字体、页眉页脚、风险等级着色）
            if st.button("📄 导出 PDF", use_container_width=True):
                try:
                    from tools.pdf_exporter import PdfExporter
                    pdf_exporter = PdfExporter()
                    pdf_path = pdf_exporter.export_report(report)
                    st.success(f"✅ PDF 已生成")
                    with open(pdf_path, "rb") as f:
                        pdf_bytes = f.read()
                    st.download_button(
                        "📥 下载 PDF",
                        data=pdf_bytes,
                        file_name=os.path.basename(pdf_path),
                        mime="application/pdf",
                    )
                    st.caption(f"文件路径: {pdf_path}")
                except Exception as e:
                    st.error(f"PDF 导出失败: {e}")

        # ===== 误反馈机制（戒律 P1/P2: 误报降权、漏报加权；P3: 理由必填） =====
        st.markdown("---")
        st.markdown("#### 📝 误反馈标记")
        st.caption(
            "标记系统误判（误报）或漏判（漏报），反馈会持久化并影响账户画像（戒律 P1/P2/P3）"
        )
        with st.expander("🏷️ 标记此报告的反馈", expanded=False):
            fb_col1, fb_col2 = st.columns(2)
            with fb_col1:
                fb_type = st.radio(
                    "反馈类型",
                    options=["false_positive", "false_negative", "confirmed"],
                    format_func=lambda x: {
                        "false_positive": "❌ 误报（系统误判可疑）",
                        "false_negative": "⚠️ 漏报（系统漏判可疑）",
                        "confirmed": "✅ 确认（系统判定正确）",
                    }[x],
                )
            with fb_col2:
                reviewer = st.text_input("分析师标识", value="analyst", key="fb_reviewer")

            fb_reason = st.text_area(
                "反馈理由（必填，戒律 P3）",
                value="",
                height=80,
                help="请说明为何判定为误报/漏报/确认，需要具体证据支撑",
            )

            suggested_score = None
            if fb_type in ("false_positive", "false_negative"):
                suggested_score = st.slider(
                    "建议风险分（可选）",
                    min_value=0,
                    max_value=100,
                    value=max_score,
                    help="分析师建议的风险评分",
                )

            if st.button("💾 提交反馈", type="primary"):
                if not fb_reason.strip():
                    st.error("❌ 反馈理由不能为空（戒律 P3: 必须有证据）")
                else:
                    try:
                        from tools.feedback_manager import FeedbackManager
                        fm = FeedbackManager()
                        # 为该报告的主账户记录一条反馈
                        # 关联该报告中的所有可疑交易ID
                        report_txns = report.get("suspicious_transactions", [])
                        rule_hits_list = []
                        if report_txns:
                            rule_hits_list = report_txns[0].get("rule_hits", [])
                            txn_id = report_txns[0].get("transaction", {}).get("transaction_id", "")
                        else:
                            txn_id = ""

                        fb_id = fm.record_feedback(
                            transaction_id=txn_id,
                            account=primary,
                            feedback_type=fb_type,
                            reason=fb_reason.strip(),
                            reviewer=reviewer.strip() or "unknown",
                            execution_id=result.get("execution_id", ""),
                            original_risk_score=float(max_score),
                            suggested_risk_score=float(suggested_score) if suggested_score is not None else None,
                            rule_hits=rule_hits_list,
                        )
                        st.success(f"✅ 反馈已记录 (ID: {fb_id})")
                        st.info(
                            "💡 反馈将影响该账户的未来风险评分（误报降权×0.95/次，漏报加权×1.10/次，钳制在 [0.7, 1.5]）"
                        )
                    except ValueError as ve:
                        st.error(f"❌ 反馈记录失败: {ve}")
                    except Exception as e:
                        st.error(f"❌ 反馈记录失败: {e}")

        # 显示该账户的历史反馈
        try:
            from tools.feedback_manager import FeedbackManager
            fm = FeedbackManager()
            account_history = fm.list_feedback(account=primary, limit=5)
            if account_history:
                st.markdown(f"##### 📜 账户 {primary} 的最近反馈")
                fb_history = []
                for fb in account_history:
                    fb_history.append({
                        "反馈ID": fb.get("feedback_id", ""),
                        "时间": fb.get("timestamp", ""),
                        "类型": {
                            "false_positive": "误报",
                            "false_negative": "漏报",
                            "confirmed": "确认",
                        }.get(fb.get("feedback_type", ""), fb.get("feedback_type", "")),
                        "原风险分": fb.get("original_risk_score", 0),
                        "分析师": fb.get("reviewer", ""),
                    })
                st.dataframe(pd.DataFrame(fb_history), use_container_width=True)
        except Exception:
            pass


# ============================================================
# Tab 5: 规则调参面板
# ============================================================
with tab5:
    st.subheader("⚙️ 规则调参面板")
    st.caption("调整规则引擎参数并实时对比效果，所有对比基于真实交易数据（戒律 M1）")

    from tools.rule_tuner import RuleTuner
    tuner = RuleTuner()

    # session state 初始化
    if "tuner_params" not in st.session_state:
        st.session_state.tuner_params = tuner.get_tunable_params()
    if "tuner_comparison" not in st.session_state:
        st.session_state.tuner_comparison = None
    if "tuner_errors" not in st.session_state:
        st.session_state.tuner_errors = []
    if "tuner_warnings" not in st.session_state:
        st.session_state.tuner_warnings = []
    if "tuner_info" not in st.session_state:
        st.session_state.tuner_info = ""

    # 戒律守护提示
    st.info(
        "⚖️ **戒律守护**：调参后会自动检查是否遗漏高风险交易（戒律 P1）"
        "或产生大量误报（戒律 P2）。激进调参会给出警告。"
    )

    # 参数编辑（分组展示）
    metadata = tuner.get_param_metadata()
    for group, info in metadata.items():
        with st.expander(f"📋 {info['label']}", expanded=False):
            for key, spec in info["params"].items():
                current_val = st.session_state.tuner_params[group][key]
                label = f"{spec['desc']}（{group}.{key}）"
                if spec["type"] == "int":
                    new_val = st.number_input(
                        label,
                        min_value=int(spec["min"]),
                        max_value=int(spec["max"]),
                        value=int(current_val),
                        step=1,
                        key=f"param_{group}_{key}",
                    )
                else:  # float
                    new_val = st.number_input(
                        label,
                        min_value=float(spec["min"]),
                        max_value=float(spec["max"]),
                        value=float(current_val),
                        step=(spec["max"] - spec["min"]) / 100.0,
                        format="%.4f",
                        key=f"param_{group}_{key}",
                    )
                st.session_state.tuner_params[group][key] = new_val

    # 操作按钮
    st.markdown("---")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("🔍 试用调参", type="primary", use_container_width=True):
            if not st.session_state.transactions:
                st.session_state.tuner_errors = ["请先在主界面加载数据并完成分析"]
            else:
                is_valid, errors, warnings = tuner.validate_params(
                    st.session_state.tuner_params
                )
                st.session_state.tuner_errors = errors
                st.session_state.tuner_warnings = list(warnings)
                st.session_state.tuner_info = ""
                if is_valid:
                    with st.spinner("正在基于真实交易数据对比调参效果..."):
                        try:
                            comparison = tuner.compare_effect(
                                st.session_state.transactions,
                                st.session_state.tuner_params,
                            )
                            st.session_state.tuner_comparison = comparison
                            st.session_state.tuner_warnings.extend(
                                comparison.get("warnings", [])
                            )
                            st.session_state.tuner_info = "调参效果已生成（基于真实交易数据）"
                        except ValueError as e:
                            st.session_state.tuner_errors.append(str(e))
                st.rerun()
    with col2:
        if st.button("💾 保存配置", use_container_width=True):
            is_valid, errors, _ = tuner.validate_params(st.session_state.tuner_params)
            if not is_valid:
                st.session_state.tuner_errors = list(errors)
                st.session_state.tuner_info = ""
                st.rerun()
            else:
                # 弹出名称输入
                st.session_state.tuner_info = "请输入配置名称后再次点击保存"
                st.rerun()
    with col3:
        if st.button("🔄 重置默认", use_container_width=True):
            st.session_state.tuner_params = tuner.get_defaults()
            st.session_state.tuner_comparison = None
            st.session_state.tuner_warnings = []
            st.session_state.tuner_errors = []
            st.session_state.tuner_info = "已重置为默认参数"
            st.rerun()
    with col4:
        if st.button("🚀 应用到当前会话", use_container_width=True):
            is_valid, errors, _ = tuner.validate_params(st.session_state.tuner_params)
            if not is_valid:
                st.session_state.tuner_errors = list(errors)
                st.session_state.tuner_info = ""
            else:
                tuner.apply_config(st.session_state.tuner_params)
                st.session_state.tuner_info = (
                    "✅ 已应用到当前会话，下次分析生效（不影响 config.py 文件）"
                )
                st.session_state.tuner_errors = []
            st.rerun()

    # 保存配置名称输入
    if st.session_state.get("tuner_info") == "请输入配置名称后再次点击保存":
        with st.form("save_config_form"):
            cfg_name = st.text_input("配置名称", value=f"config_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            cfg_desc = st.text_input("描述", value="")
            submitted = st.form_submit_button("确认保存")
            if submitted:
                is_valid, errors, _ = tuner.validate_params(st.session_state.tuner_params)
                if not is_valid:
                    st.session_state.tuner_errors = list(errors)
                    st.session_state.tuner_info = ""
                else:
                    try:
                        tuner.save_config(cfg_name, st.session_state.tuner_params, cfg_desc)
                        st.session_state.tuner_info = f"✅ 已保存为 {cfg_name}"
                        st.session_state.tuner_errors = []
                    except ValueError as e:
                        st.session_state.tuner_errors = [str(e)]
                        st.session_state.tuner_info = ""
                st.rerun()

    # 信息提示
    if st.session_state.tuner_info:
        st.success(st.session_state.tuner_info)

    # 错误显示
    if st.session_state.tuner_errors:
        st.error("❌ 参数错误：\n- " + "\n- ".join(st.session_state.tuner_errors))

    # 警告显示
    if st.session_state.tuner_warnings:
        with st.container():
            st.markdown("##### ⚠️ 戒律守护警告")
            for w in st.session_state.tuner_warnings:
                st.warning(w)

    # 对比结果展示
    if st.session_state.tuner_comparison:
        comp = st.session_state.tuner_comparison
        st.markdown("---")
        st.subheader("📊 调参效果对比")
        st.caption("以下数据基于当前已加载的真实交易数据计算（戒律 M1）")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(
                "总命中数",
                comp["after"]["total_hits"],
                delta=comp["diff"]["total_hits_delta"],
            )
        with col2:
            st.metric(
                "高风险命中数",
                comp["after"]["high_risk_hits"],
                delta=comp["diff"]["high_risk_hits_delta"],
                delta_color="inverse" if comp["diff"]["high_risk_hits_delta"] < 0 else "normal",
            )
        with col3:
            before = comp["before"]["total_hits"]
            after = comp["after"]["total_hits"]
            if before > 0:
                delta_pct = (after - before) / before * 100
                st.metric("总命中变化%", f"{delta_pct:+.1f}%")
            else:
                st.metric("总命中变化%", "N/A")

        # 各规则命中数对比表
        st.markdown("#### 各规则命中数对比")
        rule_data = []
        all_rules = set(comp["before"]["rule_counts"].keys()) | set(
            comp["after"]["rule_counts"].keys()
        )
        for rule in sorted(all_rules):
            before_n = comp["before"]["rule_counts"].get(rule, 0)
            after_n = comp["after"]["rule_counts"].get(rule, 0)
            rule_data.append({
                "规则": rule,
                "调参前": before_n,
                "调参后": after_n,
                "变化": after_n - before_n,
            })
        st.dataframe(pd.DataFrame(rule_data), use_container_width=True)

    # 历史配置列表
    st.markdown("---")
    st.subheader("📁 已保存配置")
    configs = tuner.list_configs()
    if not configs:
        st.info("暂无保存的配置")
    else:
        for c in configs:
            col_a, col_b, col_c = st.columns([3, 1, 1])
            with col_a:
                st.write(f"**{c['name']}** - {c['description']}")
                st.caption(f"创建时间: {c['created_at']}")
            with col_b:
                if st.button("加载", key=f"load_{c['file']}"):
                    try:
                        loaded = tuner.load_config(c["name"])
                        st.session_state.tuner_params = loaded["params"]
                        st.session_state.tuner_comparison = None
                        st.session_state.tuner_warnings = []
                        st.session_state.tuner_errors = []
                        st.session_state.tuner_info = f"已加载配置 {c['name']}"
                        st.rerun()
                    except (FileNotFoundError, ValueError) as e:
                        st.error(f"加载失败: {e}")
            with col_c:
                if st.button("删除", key=f"del_{c['file']}"):
                    tuner.delete_config(c["name"])
                    st.session_state.tuner_info = f"已删除配置 {c['name']}"
                    st.rerun()


# ============================================================
# Tab 6: 历史对比
# ============================================================
with tab6:
    st.subheader("📈 历史分析对比")
    st.caption("基于真实历史记录多维度对比分析结果（戒律 M1: 真实数据；M4: 完整追溯）")

    from tools.history_manager import HistoryManager
    from tools.analysis_comparator import AnalysisComparator

    hm = HistoryManager()
    comparator = AnalysisComparator(hm)

    # 综合概览
    st.markdown("#### 📊 最近运行概览")
    overview = comparator.overview(limit=30)
    if overview["total_runs"] == 0:
        st.info("暂无历史记录，完成一次分析后再次查看")
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("历史记录数", overview["total_runs"])
        with col2:
            st.metric("时间范围起", overview["date_range"][0] if overview["date_range"] else "-")
        with col3:
            st.metric("时间范围止", overview["date_range"][1] if overview["date_range"] else "-")

        # 各指标趋势
        st.markdown("##### 各指标趋势")
        if overview["metric_stats"]:
            trend_data = []
            for metric_key, stat in overview["metric_stats"].items():
                trend = overview["trends"].get(metric_key, "stable")
                trend_emoji = {"rising": "📈", "falling": "📉", "stable": "➡️"}.get(trend, "➡️")
                trend_data.append({
                    "指标": stat["label"],
                    "最小值": stat["min"],
                    "最大值": stat["max"],
                    "平均值": round(stat["avg"], 2),
                    "最近值": stat["last"],
                    "趋势": f"{trend_emoji} {trend}",
                })
            st.dataframe(pd.DataFrame(trend_data), use_container_width=True)

    # 多记录对比
    st.markdown("---")
    st.markdown("#### 🔍 多记录对比")
    runs = hm.list_runs(limit=50)
    if len(runs) < 2:
        st.info("历史记录不足2条，无法对比。请完成至少2次分析后再来查看。")
    else:
        # 选择多个记录
        run_options = {
            f"{r.get('execution_id', '')} | {r.get('timestamp', '')} | 交易{r.get('transactions_count', 0)} | 报告{r.get('report_count', 0)}": r.get("execution_id", "")
            for r in runs
        }
        selected_labels = st.multiselect(
            "选择要对比的执行记录（至少2个）",
            options=list(run_options.keys()),
            default=list(run_options.keys())[:2] if len(run_options) >= 2 else [],
        )

        if len(selected_labels) >= 2:
            selected_ids = [run_options[lbl] for lbl in selected_labels]
            if st.button("🔍 开始对比", type="primary"):
                with st.spinner("正在对比真实历史记录..."):
                    comparison = comparator.compare(selected_ids)

                # 警告显示
                if comparison["warnings"]:
                    for w in comparison["warnings"]:
                        st.warning(f"⚠️ {w}")

                if not comparison["records"]:
                    st.error("对比失败，请检查选中的执行ID是否存在")
                else:
                    # 记录摘要表
                    st.markdown("##### 📋 执行记录摘要")
                    st.dataframe(pd.DataFrame(comparison["records"]), use_container_width=True)

                    # 数据指纹
                    if comparison["data_fingerprints"]:
                        st.markdown("##### 🔑 数据指纹对比")
                        fp = comparison["data_fingerprints"]
                        if fp["is_same_dataset"]:
                            st.success("✅ 所有对比记录使用了相同的数据集（指标差异来自系统/配置变化）")
                        else:
                            unique = fp["unique_count"]
                            st.warning(
                                f"⚠️ 对比记录使用了 {unique} 种不同数据集，"
                                f"指标差异可能来自数据本身而非系统变化"
                            )

                    # 核心指标对比
                    if comparison["metrics"]:
                        st.markdown("##### 📊 核心指标对比")
                        metric_rows = []
                        for metric_key, m in comparison["metrics"].items():
                            row = {
                                "指标": m["label"],
                                "单位": m["unit"],
                                "最小值": m["min"],
                                "最大值": m["max"],
                                "平均值": round(m["avg"], 2),
                                "差异": m["delta"],
                            }
                            # 加各记录的值
                            for i, v in enumerate(m["values"]):
                                row[f"记录{i+1}"] = v
                            metric_rows.append(row)
                        st.dataframe(pd.DataFrame(metric_rows), use_container_width=True)

                    # 风险分布对比
                    if comparison["risk_distribution"]:
                        st.markdown("##### 🎯 风险分布对比")
                        risk_rows = []
                        for level, info in comparison["risk_distribution"].items():
                            row = {
                                "风险等级": level,
                                "总数": info["total"],
                                "最小值": info["min"],
                                "最大值": info["max"],
                            }
                            for i, v in enumerate(info["values"]):
                                row[f"记录{i+1}"] = v
                            risk_rows.append(row)
                        st.dataframe(pd.DataFrame(risk_rows), use_container_width=True)

                    # 各规则命中对比
                    if comparison["rule_details"]:
                        st.markdown("##### ⚖️ 各规则命中数对比")
                        rule_rows = []
                        for rule, info in comparison["rule_details"].items():
                            row = {
                                "规则": rule,
                                "总数": info["total"],
                                "最小值": info["min"],
                                "最大值": info["max"],
                                "差异": info["delta"],
                            }
                            for i, v in enumerate(info["values"]):
                                row[f"记录{i+1}"] = v
                            rule_rows.append(row)
                        st.dataframe(pd.DataFrame(rule_rows), use_container_width=True)

    # 两两详细对比
    st.markdown("---")
    st.markdown("#### ⚔️ 两两详细对比")
    if len(runs) < 2:
        st.info("需要至少2条历史记录")
    else:
        col_a, col_b = st.columns(2)
        with col_a:
            label_a = st.selectbox("选择记录A", options=list(run_options.keys()), index=0, key="cmp_a")
        with col_b:
            label_b = st.selectbox(
                "选择记录B",
                options=list(run_options.keys()),
                index=min(1, len(run_options) - 1),
                key="cmp_b",
            )

        if st.button("🔍 两两对比", key="cmp_btn") and label_a != label_b:
            with st.spinner("正在详细对比..."):
                diff = comparator.compare_two(
                    run_options[label_a], run_options[label_b]
                )

            if "error" in diff:
                st.error(diff["error"])
            else:
                # 摘要
                st.success(f"📋 {diff['summary']}")

                # 数据集判定
                if diff["is_same_dataset"]:
                    st.info("🔑 两次分析使用相同数据集，差异主要来自系统/配置变化")
                else:
                    st.warning("🔑 两次分析使用不同数据集，差异可能来自数据本身")

                # 指标差异
                st.markdown("##### 📊 指标差异")
                diff_rows = []
                for metric_key, d in diff["metric_diffs"].items():
                    diff_rows.append({
                        "指标": d["label"],
                        "记录A": d["value_a"],
                        "记录B": d["value_b"],
                        "差异": d["diff"],
                        "变化%": f"{d['diff_pct']:+.1f}%" if d["diff_pct"] != float("inf") else "N/A",
                    })
                st.dataframe(pd.DataFrame(diff_rows), use_container_width=True)

                # 风险分布差异
                st.markdown("##### 🎯 风险分布差异")
                risk_rows = []
                for level, d in diff["risk_diff"].items():
                    risk_rows.append({
                        "风险等级": level,
                        "记录A": d["value_a"],
                        "记录B": d["value_b"],
                        "差异": d["diff"],
                    })
                st.dataframe(pd.DataFrame(risk_rows), use_container_width=True)

                # 规则命中差异
                if diff["rule_diff"]:
                    st.markdown("##### ⚖️ 规则命中差异")
                    rule_rows = []
                    for rule, d in diff["rule_diff"].items():
                        rule_rows.append({
                            "规则": rule,
                            "记录A": d["value_a"],
                            "记录B": d["value_b"],
                            "差异": d["diff"],
                        })
                    st.dataframe(pd.DataFrame(rule_rows), use_container_width=True)

    # 趋势分析
    st.markdown("---")
    st.markdown("#### 📈 趋势分析")
    trend_metric = st.selectbox(
        "选择分析指标",
        options=list(AnalysisComparator.METRIC_DEFINITIONS.keys()),
        format_func=lambda x: AnalysisComparator.METRIC_DEFINITIONS[x]["label"],
    )
    if st.button("📈 生成趋势"):
        with st.spinner("正在分析趋势..."):
            trend = comparator.find_trend(trend_metric, limit=30)
        if not trend["data_points"]:
            st.info("暂无数据点")
        else:
            trend_emoji = {"rising": "📈 上升", "falling": "📉 下降", "stable": "➡️ 稳定"}.get(
                trend["trend"], trend["trend"]
            )
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("趋势", trend_emoji)
            with col2:
                st.metric("最小值", trend["min"])
            with col3:
                st.metric("最大值", trend["max"])
            with col4:
                st.metric("平均值", round(trend["avg"], 2))

            # 趋势数据点
            trend_df = pd.DataFrame(
                trend["data_points"], columns=["时间", "值"]
            )
            st.line_chart(trend_df.set_index("时间"))

    # 离群值检测
    st.markdown("---")
    st.markdown("#### 🚨 离群值检测")
    st.caption("找出指标偏离均值较大的运行（戒律 P1: 异常运行需关注）")
    outlier_metric = st.selectbox(
        "选择检测指标",
        options=list(AnalysisComparator.METRIC_DEFINITIONS.keys()),
        format_func=lambda x: AnalysisComparator.METRIC_DEFINITIONS[x]["label"],
        key="outlier_metric",
    )
    if st.button("🚨 检测离群值"):
        with st.spinner("正在检测离群值..."):
            outliers = comparator.find_outliers(outlier_metric, limit=50)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("检测样本数", outliers["total_checked"])
        with col2:
            st.metric("均值", outliers["mean"])
        with col3:
            st.metric("标准差", outliers["std"])

        if outliers.get("message"):
            st.info(outliers["message"])

        if outliers["outliers"]:
            st.warning(f"发现 {len(outliers['outliers'])} 个离群运行：")
            outlier_rows = []
            for o in outliers["outliers"]:
                outlier_rows.append({
                    "执行ID": o["execution_id"],
                    "时间": o["timestamp"],
                    "值": o["value"],
                    "Z-score": o["z_score"],
                    "偏离均值": round(o["deviation"], 2),
                })
            st.dataframe(pd.DataFrame(outlier_rows), use_container_width=True)
        else:
            if not outliers.get("message"):
                st.success("✅ 未发现离群运行，所有指标都在正常范围内")


# ============================================================
# Tab 7: 记忆洞察
# ============================================================
with tab7:
    st.subheader("🧠 记忆洞察")
    st.caption("系统从历史反馈中学习，持续优化检测效果（戒律 M1: 真实数据；M2: 证据完整）")

    try:
        from tools.memory_manager import get_memory_manager
        from tools.memory_retriever import get_memory_retriever
        from tools.reflection_engine import get_reflection_engine

        mm = get_memory_manager()
        retriever = get_memory_retriever()
        reflection = get_reflection_engine()

        # 总览卡片
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("案件记忆", mm.get_memory_count("case"))
        with col2:
            st.metric("误报记忆", mm.get_memory_count("false_positive"))
        with col3:
            st.metric("漏报记忆", mm.get_memory_count("false_negative"))
        with col4:
            st.metric("规则统计", mm.get_memory_count("rule_stat"))

        mem_tab1, mem_tab2, mem_tab3, mem_tab4 = st.tabs([
            "📚 案件记忆", "⚠️ 误报分析", "🔍 漏报分析", "📊 规则表现"
        ])

        with mem_tab1:
            st.markdown("#### 历史案件记忆")
            cases = mm.list_cases(limit=50)
            if not cases:
                st.info("暂无案件记忆。分析并标记可疑交易后，系统会自动积累记忆。")
            else:
                case_rows = []
                for c in cases:
                    case_data = {}
                    full = mm.get_case(c["id"])
                    if full:
                        case_data = full.get("case_data", {})
                    case_rows.append({
                        "记忆ID": c["id"],
                        "创建时间": c["created_at"],
                        "风险评分": case_data.get("risk_score", "-"),
                        "交易笔数": case_data.get("transaction_count", "-"),
                        "衰减权重": round(c["weight"], 3),
                        "标签": ", ".join(c.get("tags", [])),
                    })
                st.dataframe(pd.DataFrame(case_rows), use_container_width=True)

                # 相似案例检索
                st.markdown("---")
                st.markdown("#### 🔎 相似案例检索")
                st.caption("输入案件特征，从历史记忆中找相似案例")

                sim_col1, sim_col2 = st.columns(2)
                with sim_col1:
                    sim_amount = st.number_input("交易金额（元）", 0, 10000000, 500000, step=10000)
                    sim_count = st.slider("交易笔数", 1, 200, 20)
                with sim_col2:
                    sim_risk = st.slider("规则风险分", 0, 100, 70)
                    sim_hits = st.slider("命中规则数", 0, 10, 3)

                if st.button("🔍 检索相似案例", key="search_similar"):
                    query_case = {
                        "amount": sim_amount,
                        "transaction_count": sim_count,
                        "risk_score": sim_risk,
                        "hit_rules": [f"R{i:03d}" for i in range(sim_hits)],
                    }
                    similar = retriever.search_similar_cases(query_case, top_k=5)
                    if similar:
                        st.success(f"找到 {len(similar)} 个相似案例")
                        sim_rows = []
                        for s in similar:
                            sim_rows.append({
                                "案件ID": s["case_id"],
                                "相似度": f"{s['similarity']*100:.1f}%",
                                "加权得分": round(s["weighted_score"], 3),
                                "衰减权重": round(s["decay_weight"], 3),
                                "创建时间": s["created_at"],
                            })
                        st.dataframe(pd.DataFrame(sim_rows), use_container_width=True)
                    else:
                        st.info("未找到足够相似的历史案例")

        with mem_tab2:
            st.markdown("#### 误报模式分析")
            st.caption("P2: 不误报 — 分析历史误报，持续优化减少误报")

            fp_result = reflection.analyze_false_positives()

            if fp_result["total_fp"] == 0:
                st.info("暂无误报记录。收到误报反馈后，系统会自动分析原因。")
            else:
                col_fp1, col_fp2 = st.columns(2)
                with col_fp1:
                    st.metric("误报总数", fp_result["total_fp"])
                with col_fp2:
                    st.metric("涉及规则数", len(fp_result["rule_fp_rate"]))

                st.markdown("##### 主要误报原因")
                if fp_result["top_reasons"]:
                    reason_df = pd.DataFrame(fp_result["top_reasons"])
                    st.bar_chart(reason_df.set_index("reason")["count"])

                st.markdown("##### 优化建议")
                for i, sug in enumerate(fp_result["suggestions"], 1):
                    st.info(f"💡 建议 {i}: {sug}")

        with mem_tab3:
            st.markdown("#### 漏报模式分析")
            st.caption("P1: 不遗漏 — 分析历史漏报，确保高风险交易不被放过")

            fn_result = reflection.analyze_false_negatives()

            if fn_result["total_fn"] == 0:
                st.info("暂无漏报记录。收到漏报反馈后，系统会自动分析原因。")
            else:
                col_fn1, col_fn2 = st.columns(2)
                with col_fn1:
                    st.metric("漏报总数", fn_result["total_fn"])
                with col_fn2:
                    st.metric("漏掉规则数", len(fn_result["top_missed_rules"]))

                if fn_result["top_missed_rules"]:
                    st.markdown("##### 最常漏掉的规则")
                    missed_df = pd.DataFrame(fn_result["top_missed_rules"])
                    st.bar_chart(missed_df.set_index("rule")["count"])

                st.markdown("##### 优化建议")
                for i, sug in enumerate(fn_result["suggestions"], 1):
                    st.warning(f"⚠️ 建议 {i}: {sug}")

        with mem_tab4:
            st.markdown("#### 规则历史表现")

            rule_perf = reflection.analyze_rule_performance()

            if rule_perf["rule_count"] == 0:
                st.info("暂无规则统计数据。积累反馈后，这里会展示各规则的历史表现。")
            else:
                col_rp1, col_rp2, col_rp3 = st.columns(3)
                with col_rp1:
                    st.metric("规则总数", rule_perf["rule_count"])
                with col_rp2:
                    st.metric("表现优秀", len(rule_perf["high_performance"]))
                with col_rp3:
                    st.metric("需改进", len(rule_perf["low_performance"]))

                if rule_perf["high_performance"]:
                    st.markdown("##### ✅ 表现优秀的规则")
                    hp_df = pd.DataFrame(rule_perf["high_performance"])
                    st.dataframe(hp_df, use_container_width=True)

                if rule_perf["low_performance"]:
                    st.markdown("##### ⚠️ 需改进的规则")
                    lp_df = pd.DataFrame(rule_perf["low_performance"])
                    st.dataframe(lp_df, use_container_width=True)

                if rule_perf["suggestions"]:
                    st.markdown("##### 🔧 调优建议")
                    for i, sug in enumerate(rule_perf["suggestions"], 1):
                        st.write(f"{i}. {sug}")

    except Exception as e:
        st.info(f"记忆系统暂不可用: {e}")


# ============================================================
# Tab 8: GNN 可解释性
# ============================================================
with tab8:
    st.subheader("🔬 GNN 图神经网络解释")
    st.caption("M2: 证据完整 — 解释 GNN 为什么判定某账户可疑")

    try:
        import torch
        from tools.gnn_model import is_pyg_available
        from tools.gnn_explainer import GNNExplainer, AML_FEATURE_NAMES

        if not is_pyg_available():
            st.warning("PyTorch Geometric 未安装，GNN 功能暂不可用。请运行: pip install torch-geometric")
        else:
            from tools.graph_builder import build_graph_from_transactions
            from tools.gnn_model import create_model

            if not transactions:
                st.info("请先上传或生成交易数据")
            else:
                # 构建图
                graph = build_graph_from_transactions(transactions)
                num_nodes = graph["num_accounts"]
                num_edges = len(graph["edges"])

                col_gnn1, col_gnn2, col_gnn3 = st.columns(3)
                with col_gnn1:
                    st.metric("账户节点数", num_nodes)
                with col_gnn2:
                    st.metric("交易边数", num_edges)
                with col_gnn3:
                    # 自动选型
                    from tools.gnn_model import select_model_by_size
                    rec_model = select_model_by_size(num_nodes, num_edges)
                    st.metric("推荐模型", rec_model.upper())

                # 模型选择
                model_type = st.selectbox(
                    "选择 GNN 模型",
                    ["auto", "gcn", "gat", "graphsage"],
                    format_func=lambda x: {
                        "auto": "自动选型",
                        "gcn": "GCN - 图卷积网络（中图，速度快）",
                        "gat": "GAT - 图注意力（小图，可解释性好）",
                        "graphsage": "GraphSAGE - 图采样（大图，效率高）",
                    }[x],
                    index=0,
                )

                # 特征张量
                x = torch.tensor(graph["features"], dtype=torch.float)
                edge_index = torch.tensor(graph["edge_index"], dtype=torch.long).t().contiguous() if graph["edge_index"] else torch.zeros((2, 0), dtype=torch.long)

                if num_nodes < 2:
                    st.info("账户数量太少，无法进行 GNN 分析")
                else:
                    # 生成伪标签（用规则风险分 50 以上作为可疑标签）
                    labels = (x[:, 5] > 0.5).long()

                    if st.button("🧠 训练并解释", key="train_gnn"):
                        with st.spinner("正在训练 GNN 模型并生成解释..."):
                            mtype = model_type if model_type != "auto" else rec_model
                            model = create_model(model_type=mtype, in_channels=6, hidden_channels=64)
                            model.train()

                            optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
                            for epoch in range(50):
                                model.train()
                                optimizer.zero_grad()
                                out = model(x, edge_index)
                                loss = torch.nn.functional.cross_entropy(out, labels)
                                loss.backward()
                                optimizer.step()

                            # 预测
                            model.eval()
                            with torch.no_grad():
                                probs = model.predict(x, edge_index).numpy()

                            # 排序，取风险最高的账户
                            accounts = list(graph["account_to_idx"].keys())
                            account_scores = list(zip(accounts, probs))
                            account_scores.sort(key=lambda x: x[1], reverse=True)

                            st.success(f"模型训练完成（{mtype.upper()}）")

                            # 高风险账户列表
                            st.markdown("#### 📊 账户风险排名（Top 10）")
                            top_df = pd.DataFrame([
                                {"排名": i+1, "账户": acc, "可疑概率": f"{p*100:.1f}%", "风险等级": "高" if p > 0.7 else "中" if p > 0.4 else "低"}
                                for i, (acc, p) in enumerate(account_scores[:10])
                            ])
                            st.dataframe(top_df, use_container_width=True)

                            # 选择账户查看解释
                            st.markdown("---")
                            st.markdown("#### 🔍 选择账户查看详细解释")
                            top_accounts = [acc for acc, _ in account_scores[:10]]
                            selected_account = st.selectbox("选择账户", top_accounts, key="explain_account")

                            if selected_account:
                                node_idx = graph["account_to_idx"][selected_account]
                                explainer = GNNExplainer(model)

                                # 注意力权重（GAT 专用）
                                attn_data = None
                                if mtype == "gat":
                                    with torch.no_grad():
                                        _, attn = model(x, edge_index, return_attention_weights=True)
                                    attn_data = attn

                                # 综合解释
                                explanation = explainer.explain_prediction(
                                    x, edge_index,
                                    node_index=node_idx,
                                    attention_data=attn_data,
                                    feature_names=AML_FEATURE_NAMES,
                                    account_names={i: acc for acc, i in graph["account_to_idx"].items()},
                                )

                                st.markdown(f"**解释结果：**")
                                st.info(explanation["explanation_text"])

                                col_exp1, col_exp2 = st.columns(2)

                                with col_exp1:
                                    st.markdown("##### 特征重要性")
                                    feat_df = pd.DataFrame(explanation["top_features"])
                                    if not feat_df.empty:
                                        st.bar_chart(feat_df.set_index("feature_name")["importance_ratio"])

                                with col_exp2:
                                    st.markdown("##### 重要邻居")
                                    if explanation["top_neighbors"]:
                                        neighbor_rows = []
                                        for n in explanation["top_neighbors"][:5]:
                                            n_idx = n.get("node_index", n.get("neighbor_index", 0))
                                            n_acc = list(graph["account_to_idx"].keys())[
                                                list(graph["account_to_idx"].values()).index(n_idx)
                                            ] if n_idx < len(accounts) else f"节点{n_idx}"
                                            score = n.get("attention_score", n.get("importance_ratio", 0))
                                            neighbor_rows.append({
                                                "邻居账户": n_acc,
                                                "重要性": round(score, 4),
                                            })
                                        st.dataframe(pd.DataFrame(neighbor_rows), use_container_width=True)
                                    else:
                                        st.info("暂无邻居数据")

    except ImportError as e:
        st.info(f"GNN 功能暂不可用: {e}")
    except Exception as e:
        st.error(f"GNN 分析出错: {e}")


# ============================================================
# Tab 9: 系统监控
# ============================================================
with tab9:
    st.subheader("🖥️ 系统监控")
    st.caption("实时监控系统状态、性能指标和资源使用")

    import time
    import platform
    import sys

    # 系统信息
    col_sys1, col_sys2, col_sys3, col_sys4 = st.columns(4)
    with col_sys1:
        st.metric("Python 版本", f"{sys.version_info.major}.{sys.version_info.minor}")
    with col_sys2:
        st.metric("操作系统", platform.system())
    with col_sys3:
        st.metric("处理器", platform.machine())
    with col_sys4:
        st.metric("当前时间", pd.Timestamp.now().strftime("%H:%M:%S"))

    mon_tab1, mon_tab2, mon_tab3 = st.tabs(["📊 性能指标", "🧪 健康检查", "📦 依赖版本"])

    with mon_tab1:
        st.markdown("#### 性能指标")

        # 模拟性能数据（基于实际分析结果）
        if result and transactions:
            step_times = result.get("step_times", {})
            total_time = sum(step_times.values()) if step_times else 0

            col_perf1, col_perf2, col_perf3 = st.columns(3)
            with col_perf1:
                st.metric("总耗时", f"{total_time:.2f}s" if total_time else "N/A")
            with col_perf2:
                st.metric("交易数", len(transactions))
            with col_perf3:
                tps = len(transactions) / total_time if total_time > 0 else 0
                st.metric("吞吐量", f"{tps:.1f} tx/s" if tps else "N/A")

            if step_times:
                st.markdown("##### 各阶段耗时")
                step_df = pd.DataFrame([
                    {"阶段": k.replace("_", " ").title(), "耗时(秒)": round(v, 3)}
                    for k, v in step_times.items()
                ])
                st.bar_chart(step_df.set_index("阶段")["耗时(秒)"])

                # 效率分析
                st.markdown("##### 效率分析")
                if tps > 100:
                    st.success(f"✅ 性能优秀，吞吐量 {tps:.1f} tx/s，满足生产要求")
                elif tps > 30:
                    st.info(f"ℹ️ 性能良好，吞吐量 {tps:.1f} tx/s")
                else:
                    st.warning(f"⚠️ 吞吐量偏低，建议优化慢节点（如 LLM 调用）")

            # 内存使用
            st.markdown("---")
            st.markdown("##### 内存使用估算")
            est_mem = (len(transactions) * 2) / 1024  # 假设每笔交易约 2KB
            st.info(f"当前数据约占用 {est_mem:.2f} MB 内存")
            st.caption("注：为估算值，实际占用取决于数据复杂度和缓存")

        else:
            st.info("暂无分析数据，请先上传并分析交易")

    with mon_tab2:
        st.markdown("#### 健康检查")

        checks = []

        # 1. 规则引擎
        try:
            from agents.rule_engine import RuleEngine
            engine = RuleEngine()
            checks.append({"组件": "规则引擎", "状态": "✅ 正常", "详情": f"{len(engine.rules)} 条规则已加载"})
        except Exception as e:
            checks.append({"组件": "规则引擎", "状态": "❌ 异常", "详情": str(e)})

        # 2. 数据存储
        try:
            from config import DATA_DIR
            import os
            if os.path.isdir(DATA_DIR):
                checks.append({"组件": "数据目录", "状态": "✅ 正常", "详情": DATA_DIR})
            else:
                checks.append({"组件": "数据目录", "状态": "⚠️ 不存在", "详情": DATA_DIR})
        except Exception as e:
            checks.append({"组件": "数据目录", "状态": "❌ 异常", "详情": str(e)})

        # 3. 记忆系统
        try:
            from tools.memory_manager import get_memory_manager
            mm = get_memory_manager()
            total = mm.get_memory_count()
            checks.append({"组件": "记忆系统", "状态": "✅ 正常", "详情": f"{total} 条记忆"})
        except Exception as e:
            checks.append({"组件": "记忆系统", "状态": "⚠️ 不可用", "详情": str(e)})

        # 4. LLM
        try:
            llm_key = __import__("os").environ.get("DEEPSEEK_API_KEY", "")
            if llm_key:
                checks.append({"组件": "LLM 服务", "状态": "✅ 已配置", "详情": "DeepSeek API Key 已设置"})
            else:
                checks.append({"组件": "LLM 服务", "状态": "⚠️ 未配置", "详情": "将使用降级模式（规则引擎）"})
        except Exception as e:
            checks.append({"组件": "LLM 服务", "状态": "❌ 异常", "详情": str(e)})

        # 5. GNN
        try:
            from tools.gnn_model import is_pyg_available
            if is_pyg_available():
                checks.append({"组件": "GNN 模型", "状态": "✅ 可用", "详情": "PyTorch Geometric 已安装"})
            else:
                checks.append({"组件": "GNN 模型", "状态": "⚠️ 未安装", "详情": "pip install torch-geometric"})
        except Exception as e:
            checks.append({"组件": "GNN 模型", "状态": "❌ 异常", "详情": str(e)})

        st.dataframe(pd.DataFrame(checks), use_container_width=True)

        # 整体状态
        healthy = sum(1 for c in checks if "✅" in c["状态"])
        if healthy == len(checks):
            st.success("✅ 所有组件运行正常")
        elif healthy >= len(checks) * 0.7:
            st.info(f"ℹ️ 系统基本正常，{healthy}/{len(checks)} 组件可用")
        else:
            st.warning(f"⚠️ 系统存在异常，{healthy}/{len(checks)} 组件可用")

    with mon_tab3:
        st.markdown("#### 核心依赖版本")

        deps = [
            {"包": "Python", "版本": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"},
            {"包": "Streamlit", "版本": __import__("streamlit").__version__ if "streamlit" in sys.modules else "N/A"},
            {"包": "Pandas", "版本": pd.__version__},
            {"包": "PyTorch", "版本": __import__("torch").__version__ if __import__("importlib").util.find_spec("torch") else "未安装"},
        ]

        # 检查可选依赖
        try:
            import torch_geometric
            deps.append({"包": "PyG", "版本": torch_geometric.__version__})
        except ImportError:
            deps.append({"包": "PyG", "版本": "未安装"})

        try:
            import langgraph
            deps.append({"包": "LangGraph", "版本": langgraph.__version__ if hasattr(langgraph, "__version__") else "已安装"})
        except ImportError:
            deps.append({"包": "LangGraph", "版本": "未安装"})

        st.dataframe(pd.DataFrame(deps), use_container_width=True)


# 底部
st.markdown("---")
st.caption("AML-Agent 反洗钱多智能体分析系统 | 严格遵循银行业务戒律")
