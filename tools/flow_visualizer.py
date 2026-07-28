"""
资金流向可视化 - 交互式 HTML 报告

将 graph_analyst 产出的 GraphData 渲染为自包含的 HTML 文件，
便于分析师在浏览器中探索账户间资金流转、风险分布与可疑社区。

戒律遵循:
- M1 真实数据: 所有节点/边数据来自 graph_analyst 真实输出，不编造
- M4 可追溯: 节点/边 hover 显示完整真实指标（交易数、金额、风险分、PageRank、社区）
- P1 不遗漏: 显示全部账户和资金路径，不因数量多而过滤
- P3 有证据: 风险着色基于真实 risk_score，可疑边标记基于真实可疑交易
- P2 不误报: 可疑标记只来自规则/图分析命中的交易，不主观标注

输出:
    reports/flow_<execution_id>_<timestamp>.html  (自包含，可直接浏览器打开)
"""
import os
import math
import json
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

import networkx as nx
import plotly.graph_objects as go


# ============================================================
# 视觉编码辅助函数（戒律 M3: 风险分 0-100 → 颜色梯度）
# ============================================================
def _risk_color_hex(score: float) -> str:
    """
    风险分(0-100) → 颜色十六进制
    0-20: 绿 (#2ecc71)
    20-50: 黄绿 → 黄 (#f1c40f)
    50-70: 橙 (#e67e22)
    70-100: 红 (#e74c3c)
    """
    try:
        s = float(score)
    except (TypeError, ValueError):
        s = 0.0
    s = max(0.0, min(100.0, s))
    if s < 20:
        return "#2ecc71"
    elif s < 50:
        # 绿 → 黄渐变
        t = (s - 20) / 30.0
        r = int(0x2e + (0xf1 - 0x2e) * t)
        g = int(0xcc + (0xc4 - 0xcc) * t)
        b = int(0x71 + (0x0f - 0x71) * t)
        return f"#{r:02x}{g:02x}{b:02x}"
    elif s < 70:
        # 黄 → 橙渐变
        t = (s - 50) / 20.0
        r = int(0xf1 + (0xe6 - 0xf1) * t)
        g = int(0xc4 + (0x7e - 0xc4) * t)
        b = int(0x0f + (0x22 - 0x0f) * t)
        return f"#{r:02x}{g:02x}{b:02x}"
    else:
        # 橙 → 红渐变
        t = (s - 70) / 30.0
        r = int(0xe6 + (0xe7 - 0xe6) * t)
        g = int(0x7e + (0x4c - 0x7e) * t)
        b = int(0x22 + (0x3c - 0x22) * t)
        return f"#{r:02x}{g:02x}{b:02x}"


def _node_marker_size(amount: float) -> float:
    """金额 → 节点大小（对数缩放，范围 12-38）"""
    try:
        a = float(amount)
    except (TypeError, ValueError):
        a = 0.0
    if a <= 0:
        return 12.0
    # log10 缩放
    raw = math.log10(a + 1)
    # 100元 → ~2, 1万 → 4, 100万 → 6, 1亿 → 8
    size = 12 + raw * 3.5
    return max(12.0, min(38.0, size))


def _edge_line_width(amount: float) -> float:
    """金额 → 边宽度（范围 0.8-6.0）"""
    try:
        a = float(amount)
    except (TypeError, ValueError):
        a = 0.0
    if a <= 0:
        return 0.8
    raw = math.log10(a + 1)
    width = 0.8 + raw * 0.65
    return max(0.8, min(6.0, width))


def _format_amount(amount: float) -> str:
    """金额格式化（带千分位）"""
    try:
        a = float(amount)
    except (TypeError, ValueError):
        a = 0.0
    if a >= 10000:
        return f"{a:,.2f} 元 ({a / 10000:.2f} 万)"
    return f"{a:,.2f} 元"


# ============================================================
# 主可视化类
# ============================================================
class FlowVisualizer:
    """交互式资金流向可视化器"""

    def __init__(self, output_dir: str = None):
        if output_dir is None:
            from config import REPORTS_DIR
            output_dir = REPORTS_DIR
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    # ------------------------------------------------------------
    # 对外主接口
    # ------------------------------------------------------------
    def generate_html(
        self,
        graph_data: Dict[str, Any],
        suspicious_list: Optional[List[Dict[str, Any]]] = None,
        output_path: Optional[str] = None,
        title: str = "反洗钱资金流向分析",
        execution_id: str = "",
    ) -> str:
        """
        生成交互式 HTML 报告

        Args:
            graph_data: graph_analyst 产出的图数据（含 nodes/edges/communities 等）
            suspicious_list: 可疑交易列表（用于标记可疑边），None时从graph_data推断
            output_path: 输出路径，None时自动生成
            title: 报告标题
            execution_id: 执行ID（用于文件命名）

        Returns:
            生成的 HTML 文件绝对路径
        """
        if output_path is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = f"_{execution_id}" if execution_id else ""
            output_path = os.path.join(
                self.output_dir, f"flow{suffix}_{ts}.html"
            )

        # 戒律 M1: 数据来源真实，缺失时明确提示而非编造
        if not graph_data or not graph_data.get("nodes"):
            html = self._build_empty_html(title, "无图数据（图分析未运行或无交易数据）")
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(html)
            return os.path.abspath(output_path)

        nodes = graph_data.get("nodes", [])
        edges = graph_data.get("edges", [])
        communities = graph_data.get("communities", [])
        suspicious_communities = graph_data.get("suspicious_communities", [])
        graph_stats = graph_data.get("graph_stats", {})
        centrality = graph_data.get("centrality", {}) or {}

        # 标记可疑边（戒律 P3: 仅基于真实命中的可疑交易）
        suspicious_edge_keys = self._collect_suspicious_edges(suspicious_list, edges)

        # 构建主图 figure
        main_fig = self._build_main_graph(
            nodes, edges, communities, centrality, suspicious_edge_keys
        )

        # 风险分布直方图
        risk_fig = self._build_risk_distribution(nodes)

        # Top 高风险账户表
        top_accounts_html = self._build_top_accounts_table(nodes, centrality)

        # 可疑社区列表
        communities_html = self._build_communities_section(suspicious_communities)

        # 统计卡片
        stats_cards = self._build_stats_cards(graph_stats, nodes, edges, suspicious_communities)

        # 拼接最终 HTML（主图含 plotly.js，其余片段复用）
        main_html = main_fig.to_html(
            full_html=False, include_plotlyjs="cdn", div_id="main-graph"
        )
        risk_html = risk_fig.to_html(
            full_html=False, include_plotlyjs=False, div_id="risk-dist"
        )

        final_html = self._assemble_html(
            title=title,
            stats_cards=stats_cards,
            main_html=main_html,
            risk_html=risk_html,
            top_accounts_html=top_accounts_html,
            communities_html=communities_html,
            execution_id=execution_id,
        )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(final_html)

        return os.path.abspath(output_path)

    def generate_from_state(
        self,
        state: Dict[str, Any],
        output_path: Optional[str] = None,
    ) -> str:
        """
        从工作流最终状态便捷生成可视化

        Args:
            state: AMLState 最终状态
            output_path: 输出路径

        Returns:
            生成的 HTML 文件绝对路径
        """
        graph_data = state.get("graph_data") or {}
        suspicious = (
            state.get("llm_confirmed")
            or state.get("graph_suspicious")
            or state.get("rule_hits")
            or []
        )
        execution_id = state.get("execution_id", "")
        title = f"反洗钱资金流向分析 - {state.get('analysis_date', '')}"
        return self.generate_html(
            graph_data=graph_data,
            suspicious_list=suspicious,
            output_path=output_path,
            title=title,
            execution_id=execution_id,
        )

    # ------------------------------------------------------------
    # 内部构建方法
    # ------------------------------------------------------------
    def _collect_suspicious_edges(
        self,
        suspicious_list: Optional[List[Dict[str, Any]]],
        edges: List[Dict[str, Any]],
    ) -> set:
        """收集涉及可疑交易的边 key 集合 (from, to)"""
        keys = set()
        if not suspicious_list:
            return keys
        for s in suspicious_list:
            txn = s.get("transaction", {}) if isinstance(s, dict) else {}
            frm = txn.get("from_account", "")
            to = txn.get("to_account", "")
            if frm and to:
                keys.add((frm, to))
        return keys

    def _build_main_graph(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        communities: List[List[str]],
        centrality: Dict[str, Any],
        suspicious_edge_keys: set,
    ) -> go.Figure:
        """构建主资金流向图"""
        # 构建账套到社区的映射
        account_to_comm: Dict[str, str] = {}
        for i, members in enumerate(communities):
            comm_id = f"COMM_{i + 1:03d}"
            for m in members:
                account_to_comm[m] = comm_id

        # 用 networkx 计算布局（确定性 seed 保证可复现）
        G = nx.DiGraph()
        for n in nodes:
            G.add_node(n.get("account_id", ""))
        for e in edges:
            frm = e.get("from", "")
            to = e.get("to", "")
            if frm and to:
                G.add_edge(frm, to)

        if G.number_of_nodes() == 0:
            # 空图兜底
            fig = go.Figure()
            fig.update_layout(
                annotations=[
                    dict(
                        text="无账户数据",
                        showarrow=False,
                        xref="paper", yref="paper",
                        x=0.5, y=0.5,
                        font=dict(size=20),
                    )
                ],
                plot_bgcolor="white",
            )
            return fig

        # spring_layout 确定性 seed
        try:
            pos = nx.spring_layout(G, seed=42, k=0.5, iterations=80)
        except Exception:
            # 退化：圆形布局
            pos = nx.circular_layout(G)

        pagerank = centrality.get("pagerank", {}) or {}
        betweenness = centrality.get("betweenness", {}) or {}

        # ---- 边 traces ----
        # 普通边和可疑边分两个 trace，便于图例区分
        edge_x_normal, edge_y_normal = [], []
        edge_x_susp, edge_y_susp = [], []
        edge_hover_normal, edge_hover_susp = [], []

        arrow_annotations = []
        # 大量边时不画箭头，避免渲染卡顿
        draw_arrows = len(edges) <= 200

        for e in edges:
            frm = e.get("from", "")
            to = e.get("to", "")
            if frm not in pos or to not in pos:
                continue
            x0, y0 = pos[frm]
            x1, y1 = pos[to]
            amount = float(e.get("total_amount", 0))
            txn_count = int(e.get("txn_count", 0))
            txn_ids = e.get("txn_ids", []) or []
            is_susp = (frm, to) in suspicious_edge_keys

            hover = (
                f"路径: {frm} → {to}<br>"
                f"总金额: {_format_amount(amount)}<br>"
                f"交易笔数: {txn_count}<br>"
                f"交易ID: {', '.join(txn_ids[:5])}"
                + (f" 等{len(txn_ids)}笔" if len(txn_ids) > 5 else "")
                + (f"<br><b>⚠ 涉及可疑交易</b>" if is_susp else "")
            )

            if is_susp:
                edge_x_susp += [x0, x1, None]
                edge_y_susp += [y0, y1, None]
                edge_hover_susp += [hover, hover, None]
            else:
                edge_x_normal += [x0, x1, None]
                edge_y_normal += [y0, y1, None]
                edge_hover_normal += [hover, hover, None]

            # 箭头（指向 to 节点，缩进避免被节点遮挡）
            if draw_arrows:
                # 缩进 15%
                ax = x0 + (x1 - x0) * 0.85
                ay = y0 + (y1 - y0) * 0.85
                arrow_annotations.append(
                    dict(
                        ax=x0 + (x1 - x0) * 0.7,
                        ay=y0 + (y1 - y0) * 0.7,
                        x=ax, y=ay,
                        xref="x", yref="y",
                        axref="x", ayref="y",
                        showarrow=True,
                        arrowhead=2,
                        arrowsize=0.8,
                        arrowwidth=_edge_line_width(amount),
                        arrowcolor="#e74c3c" if is_susp else "#bdc3c7",
                        opacity=0.7,
                    )
                )

        edge_trace_normal = go.Scatter(
            x=edge_x_normal, y=edge_y_normal,
            mode="lines",
            line=dict(width=1.5, color="#bdc3c7"),
            hoverinfo="text",
            text=edge_hover_normal,
            hovertemplate="%{text}<extra></extra>",
            name="正常资金流",
            opacity=0.6,
        )
        edge_trace_susp = go.Scatter(
            x=edge_x_susp, y=edge_y_susp,
            mode="lines",
            line=dict(width=2.5, color="#e74c3c"),
            hoverinfo="text",
            text=edge_hover_susp,
            hovertemplate="%{text}<extra></extra>",
            name="可疑资金流",
            opacity=0.85,
        )

        # ---- 节点 trace ----
        node_x, node_y = [], []
        node_color, node_size = [], []
        node_text, node_hover = [], []

        for n in nodes:
            acc = n.get("account_id", "")
            if acc not in pos:
                continue
            x, y = pos[acc]
            node_x.append(x)
            node_y.append(y)

            risk = float(n.get("risk_score", 0) or 0)
            in_amt = float(n.get("in_amount", 0) or 0)
            out_amt = float(n.get("out_amount", 0) or 0)
            total_txns = int(n.get("total_txns", 0) or 0)
            in_deg = int(n.get("in_degree", 0) or 0)
            out_deg = int(n.get("out_degree", 0) or 0)
            pr = float(pagerank.get(acc, 0) or 0)
            bt = float(betweenness.get(acc, 0) or 0)
            gnn_score = n.get("gnn_risk_score")
            comm = account_to_comm.get(acc, "无")

            node_color.append(_risk_color_hex(risk))
            # 节点大小按 max(in, out) 金额
            node_size.append(_node_marker_size(max(in_amt, out_amt)))

            # 节点标签：账户ID（节点数少时显示）
            node_text.append(acc if len(nodes) <= 60 else "")

            hover = (
                f"账户: <b>{acc}</b><br>"
                f"风险分: <b>{risk:.1f}</b> / 100<br>"
                f"──────────<br>"
                f"入度: {in_deg} | 出度: {out_deg}<br>"
                f"入账总额: {_format_amount(in_amt)}<br>"
                f"出账总额: {_format_amount(out_amt)}<br>"
                f"交易笔数: {total_txns}<br>"
                f"PageRank: {pr:.6f}<br>"
                f"介数中心性: {bt:.4f}<br>"
                f"所属社区: {comm}"
            )
            if gnn_score is not None:
                hover += f"<br>GNN风险分: {float(gnn_score):.1f}"
            node_hover.append(hover)

        node_trace = go.Scatter(
            x=node_x, y=node_y,
            mode="markers+text" if len(nodes) <= 60 else "markers",
            text=node_text,
            textposition="bottom center",
            textfont=dict(size=9, color="#2c3e50"),
            marker=dict(
                size=node_size,
                color=node_color,
                line=dict(width=1.2, color="#34495e"),
                opacity=0.9,
            ),
            hoverinfo="text",
            hovertext=node_hover,
            hovertemplate="%{hovertext}<extra></extra>",
            name="账户",
        )

        fig = go.Figure(data=[edge_trace_normal, edge_trace_susp, node_trace])

        fig.update_layout(
            title=dict(
                text="<b>资金流向图谱</b>（节点=账户，边=资金流向，颜色=风险分）",
                x=0.5, xanchor="center",
                font=dict(size=15),
            ),
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom", y=1.02,
                xanchor="right", x=1,
            ),
            hovermode="closest",
            margin=dict(b=20, l=20, r=20, t=60),
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            plot_bgcolor="white",
            annotations=arrow_annotations if draw_arrows else [],
            height=650,
        )

        return fig

    def _build_risk_distribution(self, nodes: List[Dict[str, Any]]) -> go.Figure:
        """风险分分布直方图"""
        scores = []
        for n in nodes:
            try:
                scores.append(float(n.get("risk_score", 0) or 0))
            except (TypeError, ValueError):
                scores.append(0.0)

        fig = go.Figure(data=[
            go.Histogram(
                x=scores,
                xbins=dict(start=0, end=100, size=10),
                marker_color="#3498db",
                marker_line_color="#2980b9",
                marker_line_width=1,
                hovertemplate="风险区间: %{x}<br>账户数: %{y}<extra></extra>",
            )
        ])
        fig.update_layout(
            title=dict(text="<b>账户风险分分布</b>", font=dict(size=13)),
            xaxis=dict(title="风险分 (0-100)", range=[0, 100]),
            yaxis=dict(title="账户数"),
            margin=dict(b=40, l=40, r=20, t=50),
            height=300,
            plot_bgcolor="white",
        )
        return fig

    def _build_top_accounts_table(
        self,
        nodes: List[Dict[str, Any]],
        centrality: Dict[str, Any],
    ) -> str:
        """Top 10 高风险账户表（HTML）"""
        pagerank = centrality.get("pagerank", {}) or {}
        sorted_nodes = sorted(
            nodes,
            key=lambda n: float(n.get("risk_score", 0) or 0),
            reverse=True,
        )
        top = sorted_nodes[:10]

        rows = []
        for i, n in enumerate(top, 1):
            acc = n.get("account_id", "")
            risk = float(n.get("risk_score", 0) or 0)
            in_amt = float(n.get("in_amount", 0) or 0)
            out_amt = float(n.get("out_amount", 0) or 0)
            txns = int(n.get("total_txns", 0) or 0)
            pr = float(pagerank.get(acc, 0) or 0)
            color = _risk_color_hex(risk)
            rows.append(
                f"<tr>"
                f"<td>{i}</td>"
                f"<td><span class='dot' style='background:{color}'></span>{acc}</td>"
                f"<td><b style='color:{color}'>{risk:.1f}</b></td>"
                f"<td>{txns}</td>"
                f"<td>{_format_amount(in_amt)}</td>"
                f"<td>{_format_amount(out_amt)}</td>"
                f"<td>{pr:.6f}</td>"
                f"</tr>"
            )

        if not rows:
            return "<p class='empty'>暂无账户数据</p>"

        return (
            "<table class='data-table'>"
            "<thead><tr>"
            "<th>#</th><th>账户</th><th>风险分</th><th>交易笔数</th>"
            "<th>入账总额</th><th>出账总额</th><th>PageRank</th>"
            "</tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody>"
            "</table>"
        )

    def _build_communities_section(
        self,
        suspicious_communities: List[Dict[str, Any]],
    ) -> str:
        """可疑社区列表（HTML）"""
        if not suspicious_communities:
            return "<p class='empty'>未发现可疑社区</p>"

        # 按 community_risk 降序
        sorted_comms = sorted(
            suspicious_communities,
            key=lambda c: float(c.get("community_risk", 0) or 0),
            reverse=True,
        )

        cards = []
        for c in sorted_comms[:10]:  # 最多展示 10 个
            comm_id = c.get("community_id", "")
            size = c.get("size", 0)
            avg_risk = float(c.get("avg_risk_score", 0) or 0)
            comm_risk = float(c.get("community_risk", 0) or 0)
            high_risk = c.get("high_risk_count", 0)
            members = c.get("members", []) or []
            total_amt = float(c.get("total_amount", 0) or 0)
            density = float(c.get("internal_density", 0) or 0)
            top_risk = c.get("top_risk_members", []) or []

            members_str = ", ".join(members[:8]) + (
                f" 等{len(members)}个" if len(members) > 8 else ""
            )
            top_str = ", ".join(
                f"{m}({s:.0f})" for m, s in top_risk[:5]
            )

            risk_color = _risk_color_hex(comm_risk * 100)
            cards.append(
                f"<div class='comm-card' style='border-left:4px solid {risk_color}'>"
                f"<div class='comm-header'>"
                f"<span class='comm-id'>{comm_id}</span>"
                f"<span class='comm-risk' style='color:{risk_color}'>"
                f"社区风险 {comm_risk:.2f}</span>"
                f"</div>"
                f"<div class='comm-stats'>"
                f"<span>规模: <b>{size}</b> 账户</span>"
                f"<span>平均风险: <b>{avg_risk:.1f}</b></span>"
                f"<span>高风险成员: <b>{high_risk}</b></span>"
                f"<span>内部密度: <b>{density:.2f}</b></span>"
                f"<span>总流转: <b>{_format_amount(total_amt)}</b></span>"
                f"</div>"
                f"<div class='comm-members'>成员: {members_str}</div>"
                f"<div class='comm-top'>高风险成员: {top_str}</div>"
                f"</div>"
            )

        return "".join(cards)

    def _build_stats_cards(
        self,
        graph_stats: Dict[str, Any],
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        suspicious_communities: List[Dict[str, Any]],
    ) -> str:
        """顶部统计卡片"""
        node_count = graph_stats.get("node_count", len(nodes))
        edge_count = graph_stats.get("edge_count", len(edges))
        total_amount = float(graph_stats.get("total_transaction_amount", 0) or 0)
        comm_count = graph_stats.get("community_count", 0)
        susp_comm_count = graph_stats.get("suspicious_community_count", len(suspicious_communities))
        high_risk_nodes = graph_stats.get("high_risk_node_count", 0)
        density = float(graph_stats.get("density", 0) or 0)

        # 风险分布统计
        risk_dist = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        for n in nodes:
            r = float(n.get("risk_score", 0) or 0)
            if r >= 85:
                risk_dist["critical"] += 1
            elif r >= 70:
                risk_dist["high"] += 1
            elif r >= 50:
                risk_dist["medium"] += 1
            else:
                risk_dist["low"] += 1

        cards = [
            ("账户总数", node_count, "#3498db"),
            ("资金路径", edge_count, "#1abc9c"),
            ("总流转金额", _format_amount(total_amount), "#9b59b6"),
            ("图密度", f"{density:.6f}", "#34495e"),
            ("社区数", comm_count, "#16a085"),
            ("可疑社区", susp_comm_count, "#e67e22"),
            ("高风险账户", high_risk_nodes, "#e74c3c"),
        ]

        cards_html = []
        for label, value, color in cards:
            cards_html.append(
                f"<div class='stat-card' style='border-top:3px solid {color}'>"
                f"<div class='stat-label'>{label}</div>"
                f"<div class='stat-value' style='color:{color}'>{value}</div>"
                f"</div>"
            )

        # 风险分布条（提前取值，避免 f-string 中使用转义引号）
        low_n = risk_dist["low"]
        med_n = risk_dist["medium"]
        high_n = risk_dist["high"]
        crit_n = risk_dist["critical"]
        risk_bar = (
            "<div class='risk-bar'>"
            f"<span class='risk-seg low' title='低风险(0-49): {low_n}个'>低 {low_n}</span>"
            f"<span class='risk-seg medium' title='中风险(50-69): {med_n}个'>中 {med_n}</span>"
            f"<span class='risk-seg high' title='高风险(70-84): {high_n}个'>高 {high_n}</span>"
            f"<span class='risk-seg critical' title='极高风险(85-100): {crit_n}个'>极高 {crit_n}</span>"
            "</div>"
        )

        return "<div class='stats-grid'>" + "".join(cards_html) + "</div>" + risk_bar

    # ------------------------------------------------------------
    # HTML 组装
    # ------------------------------------------------------------
    def _assemble_html(
        self,
        title: str,
        stats_cards: str,
        main_html: str,
        risk_html: str,
        top_accounts_html: str,
        communities_html: str,
        execution_id: str,
    ) -> str:
        """拼接最终自包含 HTML"""
        gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        exec_str = f" | 执行ID: {execution_id}" if execution_id else ""

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                 "Microsoft YaHei", sans-serif;
    background: #f5f6fa; color: #2c3e50; line-height: 1.6; padding: 20px;
  }}
  header {{
    background: linear-gradient(135deg, #2c3e50, #34495e);
    color: white; padding: 24px 30px; border-radius: 10px; margin-bottom: 20px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
  }}
  header h1 {{ font-size: 22px; margin-bottom: 6px; }}
  header .meta {{ font-size: 13px; opacity: 0.85; }}
  section {{
    background: white; border-radius: 10px; padding: 20px 24px;
    margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);
  }}
  section h2 {{
    font-size: 16px; color: #2c3e50; margin-bottom: 14px;
    padding-bottom: 8px; border-bottom: 2px solid #ecf0f1;
  }}
  .stats-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px; margin-bottom: 16px;
  }}
  .stat-card {{
    background: white; padding: 14px; border-radius: 8px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08); text-align: center;
  }}
  .stat-label {{ font-size: 12px; color: #7f8c8d; margin-bottom: 4px; }}
  .stat-value {{ font-size: 20px; font-weight: 700; }}
  .risk-bar {{
    display: flex; margin-top: 10px; border-radius: 6px; overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  }}
  .risk-seg {{
    flex: 1; padding: 8px 12px; color: white; font-size: 13px;
    font-weight: 600; text-align: center;
  }}
  .risk-seg.low {{ background: #2ecc71; }}
  .risk-seg.medium {{ background: #f1c40f; color: #2c3e50; }}
  .risk-seg.high {{ background: #e67e22; }}
  .risk-seg.critical {{ background: #e74c3c; }}
  .data-table {{
    width: 100%; border-collapse: collapse; font-size: 13px;
  }}
  .data-table th {{
    background: #ecf0f1; padding: 10px 8px; text-align: left;
    font-weight: 600; color: #34495e; border-bottom: 2px solid #bdc3c7;
  }}
  .data-table td {{
    padding: 8px; border-bottom: 1px solid #ecf0f1;
  }}
  .data-table tr:hover {{ background: #f8f9fa; }}
  .data-table .dot {{
    display: inline-block; width: 10px; height: 10px; border-radius: 50%;
    margin-right: 6px; vertical-align: middle;
  }}
  .comm-card {{
    background: #fafbfc; padding: 12px 16px; border-radius: 6px;
    margin-bottom: 10px; border-left: 4px solid #bdc3c7;
  }}
  .comm-header {{
    display: flex; justify-content: space-between;
    margin-bottom: 8px; font-size: 14px;
  }}
  .comm-id {{ font-weight: 700; color: #2c3e50; }}
  .comm-risk {{ font-weight: 600; }}
  .comm-stats {{
    display: flex; flex-wrap: wrap; gap: 14px; font-size: 12px;
    color: #7f8c8d; margin-bottom: 6px;
  }}
  .comm-members, .comm-top {{
    font-size: 12px; color: #34495e; margin-top: 4px;
    word-break: break-all;
  }}
  .empty {{
    color: #95a5a6; font-style: italic; padding: 20px; text-align: center;
  }}
  footer {{
    text-align: center; color: #95a5a6; font-size: 12px;
    padding: 16px; margin-top: 10px;
  }}
  footer .rules {{
    margin-top: 6px; font-size: 11px; opacity: 0.8;
  }}
  .grid-2 {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 20px;
  }}
  @media (max-width: 900px) {{
    .grid-2 {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div class="meta">生成时间: {gen_time}{exec_str} | 数据来源: graph_analyst 真实输出</div>
</header>

<section>
  <h2>分析概览</h2>
  {stats_cards}
</section>

<section>
  <h2>资金流向图谱</h2>
  {main_html}
  <p style="font-size:12px;color:#7f8c8d;margin-top:8px;">
    提示: 鼠标悬停节点/边查看详情；滚轮缩放；拖拽平移。
    节点颜色: <span style="color:#2ecc71">低风险</span> /
    <span style="color:#f1c40f">中风险</span> /
    <span style="color:#e67e22">高风险</span> /
    <span style="color:#e74c3c">极高风险</span>。
    红色边代表可疑资金流路径（基于真实可疑交易标记）。
  </p>
</section>

<div class="grid-2">
  <section>
    <h2>风险分分布</h2>
    {risk_html}
  </section>
  <section>
    <h2>Top 10 高风险账户</h2>
    {top_accounts_html}
  </section>
</div>

<section>
  <h2>可疑社区</h2>
  {communities_html}
</section>

<footer>
  反洗钱多Agent系统 - 资金流向可视化报告
  <div class="rules">
    遵循业务戒律: M1 真实数据 | M4 可追溯 | P1 不遗漏 | P3 有证据 | P2 不误报
  </div>
</footer>
</body>
</html>"""

    def _build_empty_html(self, title: str, message: str) -> str:
        """空数据时的兜底 HTML"""
        gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><title>{title}</title>
<style>
  body {{ font-family: "Microsoft YaHei", sans-serif; background:#f5f6fa;
         display:flex; align-items:center; justify-content:center;
         min-height:100vh; color:#2c3e50; }}
  .empty-box {{ background:white; padding:40px 60px; border-radius:12px;
               text-align:center; box-shadow:0 2px 12px rgba(0,0,0,0.08); }}
  .empty-box h1 {{ font-size:20px; margin-bottom:12px; }}
  .empty-box p {{ color:#7f8c8d; }}
  .meta {{ margin-top:16px; font-size:12px; color:#95a5a6; }}
</style>
</head>
<body>
<div class="empty-box">
  <h1>{title}</h1>
  <p>{message}</p>
  <div class="meta">生成时间: {gen_time}</div>
</div>
</body>
</html>"""
