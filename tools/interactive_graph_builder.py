"""
交互式关联图谱构建器 (Interactive Graph Builder)

职责:
- 生成交互式 HTML 关联图谱（基于 vis.js CDN，无新 Python 依赖）
- 支持三种视图切换: 账户级 / 团伙级 / 交易时间轴
- 节点点击展开元数据（交易ID/规则命中/风险分）

设计原则:
- M1: 图数据来自真实工作流状态，不编造
- M2: 节点/边附完整元数据（交易ID/规则命中/风险分/证据）
- M4: 图谱附生成时间/数据源/版本信息
- P4: 构建失败不影响主流程

与 flow_visualizer.py 的区别:
- flow_visualizer: 静态 plotly 图，用于报告嵌入
- interactive_graph_builder: 交互式 vis.js 图，用于合规官探索
"""
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# 模块版本（戒律 M4: 可追溯）
__INTERACTIVE_GRAPH_BUILDER_VERSION__ = "1.0.0"

# vis.js CDN（戒律 M4: 版本固定可追溯）
_VIS_NETWORK_CDN = "https://unpkg.com/vis-network/9.1.9/dist/vis-network.min.js"
_VIS_NETWORK_CSS = "https://unpkg.com/vis-network/9.1.9/styles/vis-network.min.css"


def _safe_float(val, default=0.0) -> float:
    """安全转 float"""
    try:
        if val is None:
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _risk_color(score: float) -> str:
    """风险分→颜色（0-100，越高越红）"""
    if score >= 85:
        return "#c0392b"  # 深红 - critical
    elif score >= 70:
        return "#e74c3c"  # 红 - high
    elif score >= 50:
        return "#f39c12"  # 橙 - medium
    elif score > 0:
        return "#3498db"  # 蓝 - low
    return "#95a5a6"  # 灰 - normal


def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符"""
    if not isinstance(text, str):
        text = str(text)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _format_amount(amount: float) -> str:
    """格式化金额"""
    try:
        if amount >= 10000:
            return f"¥{amount/10000:.2f}万"
        return f"¥{amount:.2f}"
    except Exception:
        return str(amount)


class InteractiveGraphBuilder:
    """
    交互式关联图谱构建器

    主入口:
        build_account_graph(state) -> Dict (节点+边数据)
        build_community_graph(state) -> Dict
        build_transaction_timeline(state) -> Dict
        export_html(state, output_path) -> str (生成完整 HTML)

    戒律遵守:
    - M1: 图数据来自真实 state.graph_data / state.transactions
    - M2: 节点 title 含完整元数据（账户/交易/风险分/规则）
    - M4: HTML 附生成时间/数据源/版本
    - P4: 所有构建 try/except，失败返回空数据不抛异常
    """

    def __init__(self, output_dir: str = None):
        """
        Args:
            output_dir: HTML 输出目录，默认 config.REPORTS_DIR
        """
        if output_dir is None:
            try:
                from config import REPORTS_DIR
                output_dir = REPORTS_DIR
            except Exception:
                output_dir = "reports"
        self.output_dir = output_dir
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception:
            pass

    # ============================================================
    # 视图1: 账户级关联图谱
    # ============================================================
    def build_account_graph(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        构建账户级关联图谱（资金流向）

        数据源:
        - state.graph_data.nodes: 账户节点
        - state.graph_data.edges: 资金流向边
        - state.rule_hits: 风险分标记

        Returns:
            {"nodes": [...], "edges": [...], "view": "account"}
        """
        try:
            graph_data = state.get("graph_data") or {}
            raw_nodes = graph_data.get("nodes", []) or []
            raw_edges = graph_data.get("edges", []) or []

            # 收集账户风险分（从 rule_hits / llm_confirmed）
            account_risk = self._collect_account_risk(state)

            nodes = []
            for n in raw_nodes:
                if not isinstance(n, dict):
                    continue
                acc = n.get("id") or n.get("account") or ""
                if not acc:
                    continue
                risk = _safe_float(n.get("risk_score") or account_risk.get(acc, 0))
                nodes.append({
                    "id": acc,
                    "label": acc,
                    "color": _risk_color(risk),
                    "title": self._build_account_tooltip(acc, n, risk),
                    "size": self._node_size_by_degree(acc, raw_edges),
                    "group": "account",
                    "risk_score": risk,
                })

            edges = []
            for e in raw_edges:
                if not isinstance(e, dict):
                    continue
                src = e.get("from") or e.get("source") or ""
                dst = e.get("to") or e.get("target") or ""
                if not src or not dst:
                    continue
                amount = _safe_float(e.get("amount") or e.get("weight") or 0)
                edges.append({
                    "from": src,
                    "to": dst,
                    "label": _format_amount(amount) if amount > 0 else "",
                    "title": self._build_edge_tooltip(e),
                    "width": self._edge_width(amount),
                    "arrows": "to",
                })

            return {"nodes": nodes, "edges": edges, "view": "account"}
        except Exception as e:
            print(f"  [交互图谱] 账户视图构建失败: {e}")
            return {"nodes": [], "edges": [], "view": "account"}

    # ============================================================
    # 视图2: 团伙级关联图谱
    # ============================================================
    def build_community_graph(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        构建团伙级关联图谱（基于 GNN 社区检测）

        数据源:
        - state.graph_data.communities: 社区列表
        - 社区间通过共享账户/资金往来连接

        Returns:
            {"nodes": [...], "edges": [...], "view": "community"}
        """
        try:
            graph_data = state.get("graph_data") or {}
            communities = graph_data.get("communities", []) or []
            if not communities:
                return {"nodes": [], "edges": [], "view": "community"}

            # 节点：每个社区一个节点
            nodes = []
            comm_members = {}
            for c in communities:
                if not isinstance(c, dict):
                    continue
                cid = c.get("community_id") or c.get("id") or ""
                if not cid:
                    continue
                members = c.get("members", []) or []
                comm_members[cid] = set(members)
                risk = _safe_float(c.get("community_risk") or c.get("risk_score") or 0)
                nodes.append({
                    "id": cid,
                    "label": f"{cid}\n({len(members)}人)",
                    "color": _risk_color(risk),
                    "title": self._build_community_tooltip(c),
                    "size": 20 + min(len(members), 30),
                    "group": "community",
                    "risk_score": risk,
                })

            # 边：社区间共享账户或资金往来
            edges = []
            cid_list = list(comm_members.keys())
            for i, c1 in enumerate(cid_list):
                for c2 in cid_list[i+1:]:
                    shared = comm_members[c1] & comm_members[c2]
                    if shared:
                        edges.append({
                            "from": c1,
                            "to": c2,
                            "label": f"共享{len(shared)}人",
                            "title": f"共享账户: {', '.join(list(shared)[:5])}",
                            "width": 1 + min(len(shared), 5),
                        })

            return {"nodes": nodes, "edges": edges, "view": "community"}
        except Exception as e:
            print(f"  [交互图谱] 团伙视图构建失败: {e}")
            return {"nodes": [], "edges": [], "view": "community"}

    # ============================================================
    # 视图3: 交易时间轴视图
    # ============================================================
    def build_transaction_timeline(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        构建交易时间轴视图（按时间排序的交易节点）

        数据源:
        - state.transactions / cleaned_transactions
        - state.rule_hits: 标记可疑交易

        Returns:
            {"nodes": [...], "edges": [...], "view": "transaction"}
        """
        try:
            transactions = (
                state.get("cleaned_transactions")
                or state.get("transactions")
                or []
            )
            if not transactions:
                return {"nodes": [], "edges": [], "view": "transaction"}

            # 收集可疑交易ID集
            suspicious_ids = set()
            rule_hits = state.get("rule_hits", []) or []
            for s in rule_hits:
                if isinstance(s, dict):
                    t = s.get("transaction") or {}
                    if isinstance(t, dict):
                        tid = t.get("transaction_id")
                        if tid:
                            suspicious_ids.add(tid)

            # 按时间排序
            sorted_txns = sorted(
                transactions,
                key=lambda t: t.get("timestamp", "") if isinstance(t, dict) else "",
            )

            nodes = []
            edges = []
            prev_id = None
            for idx, t in enumerate(sorted_txns):
                if not isinstance(t, dict):
                    continue
                tid = t.get("transaction_id", f"T{idx}")
                ts = t.get("timestamp", "")
                amount = _safe_float(t.get("amount", 0))
                is_suspicious = tid in suspicious_ids
                risk = _safe_float(t.get("risk_score"), 50 if is_suspicious else 0)

                nodes.append({
                    "id": tid,
                    "label": tid,
                    "color": _risk_color(risk),
                    "title": self._build_transaction_tooltip(t, is_suspicious),
                    "shape": "diamond" if is_suspicious else "dot",
                    "size": 12 if is_suspicious else 8,
                    "group": "suspicious" if is_suspicious else "normal",
                    "risk_score": risk,
                })

                # 按时间顺序连接（资金流向）
                src = t.get("from_account", "")
                dst = t.get("to_account", "")
                if src and dst:
                    # 添加账户节点（如果不存在）
                    if src not in [n["id"] for n in nodes]:
                        nodes.append({
                            "id": src,
                            "label": src,
                            "color": "#bdc3c7",
                            "title": f"账户: {src}",
                            "shape": "triangle",
                            "size": 10,
                            "group": "account",
                        })
                    if dst not in [n["id"] for n in nodes]:
                        nodes.append({
                            "id": dst,
                            "label": dst,
                            "color": "#bdc3c7",
                            "title": f"账户: {dst}",
                            "shape": "triangle",
                            "size": 10,
                            "group": "account",
                        })
                    edges.append({
                        "from": src,
                        "to": dst,
                        "label": _format_amount(amount),
                        "title": f"交易 {tid}\n时间: {ts}\n金额: {_format_amount(amount)}",
                        "arrows": "to",
                        "width": self._edge_width(amount),
                    })

            return {"nodes": nodes, "edges": edges, "view": "transaction"}
        except Exception as e:
            print(f"  [交互图谱] 交易视图构建失败: {e}")
            return {"nodes": [], "edges": [], "view": "transaction"}

    # ============================================================
    # 导出 HTML
    # ============================================================
    def export_html(
        self,
        state: Dict[str, Any],
        output_path: str = None,
        default_view: str = "account",
    ) -> str:
        """
        生成完整交互式 HTML

        Args:
            state: 工作流状态
            output_path: 输出路径，默认 reports/interactive_graph_<execution_id>.html
            default_view: 默认视图 account/community/transaction

        Returns:
            生成的 HTML 文件路径
        """
        try:
            # 构建三种视图数据
            account_data = self.build_account_graph(state)
            community_data = self.build_community_graph(state)
            transaction_data = self.build_transaction_timeline(state)

            execution_id = state.get("execution_id", "unknown")
            if output_path is None:
                output_path = os.path.join(
                    self.output_dir,
                    f"interactive_graph_{execution_id}.html",
                )

            html = self._assemble_html(
                account_data, community_data, transaction_data,
                execution_id, default_view,
            )

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(html)

            return output_path
        except Exception as e:
            print(f"  [交互图谱] HTML 导出失败: {e}")
            return ""

    # ============================================================
    # 内部：元数据 Tooltip 构建
    # ============================================================
    def _build_account_tooltip(self, acc: str, node_data: Dict, risk: float) -> str:
        """构建账户节点 tooltip（戒律 M2: 含完整元数据）"""
        try:
            lines = [f"<b>账户: {_escape_html(acc)}</b>"]
            lines.append(f"风险分: {risk:.1f}/100")
            centrality = node_data.get("centrality")
            if centrality is not None:
                lines.append(f"中心性: {_safe_float(centrality):.4f}")
            community = node_data.get("community_id")
            if community:
                lines.append(f"所属团伙: {_escape_html(str(community))}")
            txn_count = node_data.get("txn_count")
            if txn_count is not None:
                lines.append(f"交易笔数: {txn_count}")
            total_amount = node_data.get("total_amount")
            if total_amount is not None:
                lines.append(f"交易总额: {_format_amount(_safe_float(total_amount))}")
            return "<br>".join(lines)
        except Exception:
            return f"账户: {_escape_html(acc)}"

    def _build_edge_tooltip(self, edge: Dict) -> str:
        """构建边的 tooltip"""
        try:
            src = edge.get("from") or edge.get("source") or ""
            dst = edge.get("to") or edge.get("target") or ""
            amount = _safe_float(edge.get("amount") or edge.get("weight") or 0)
            txn_count = edge.get("txn_count", 1)
            lines = [
                f"<b>{_escape_html(str(src))} → {_escape_html(str(dst))}</b>",
                f"金额: {_format_amount(amount)}",
                f"交易笔数: {txn_count}",
            ]
            return "<br>".join(lines)
        except Exception:
            return "资金流向"

    def _build_community_tooltip(self, community: Dict) -> str:
        """构建团伙节点 tooltip"""
        try:
            cid = community.get("community_id") or community.get("id") or ""
            members = community.get("members", []) or []
            risk = _safe_float(community.get("community_risk") or community.get("risk_score") or 0)
            lines = [
                f"<b>团伙: {_escape_html(str(cid))}</b>",
                f"成员数: {len(members)}",
                f"团伙风险分: {risk:.1f}/100",
            ]
            if members:
                shown = members[:10]
                lines.append(f"成员(前10): {', '.join(_escape_html(str(m)) for m in shown)}")
            return "<br>".join(lines)
        except Exception:
            return "团伙"

    def _build_transaction_tooltip(self, txn: Dict, is_suspicious: bool) -> str:
        """构建交易节点 tooltip"""
        try:
            tid = txn.get("transaction_id", "")
            lines = [f"<b>交易: {_escape_html(str(tid))}</b>"]
            lines.append(f"时间: {_escape_html(str(txn.get('timestamp', '')))}")
            lines.append(f"金额: {_format_amount(_safe_float(txn.get('amount', 0)))}")
            lines.append(f"付款方: {_escape_html(str(txn.get('from_account', '')))}")
            lines.append(f"收款方: {_escape_html(str(txn.get('to_account', '')))}")
            remark = txn.get("remark", "")
            if remark:
                lines.append(f"备注: {_escape_html(str(remark))}")
            if is_suspicious:
                lines.append("<b style='color:red'>⚠ 可疑交易</b>")
            return "<br>".join(lines)
        except Exception:
            return "交易"

    # ============================================================
    # 内部：辅助计算
    # ============================================================
    def _collect_account_risk(self, state: Dict[str, Any]) -> Dict[str, float]:
        """从 rule_hits/llm_confirmed 收集账户风险分"""
        account_risk = {}
        try:
            for key in ("rule_hits", "llm_confirmed", "false_positives"):
                for s in (state.get(key) or []):
                    if not isinstance(s, dict):
                        continue
                    t = s.get("transaction") or {}
                    if not isinstance(t, dict):
                        continue
                    acc = t.get("from_account") or t.get("to_account")
                    if not acc:
                        continue
                    risk = _safe_float(s.get("risk_score") or t.get("risk_score"))
                    if risk > account_risk.get(acc, 0):
                        account_risk[acc] = risk
        except Exception:
            pass
        return account_risk

    def _node_size_by_degree(self, acc: str, edges: List[Dict]) -> int:
        """根据连接度计算节点大小"""
        try:
            degree = 0
            for e in edges:
                if not isinstance(e, dict):
                    continue
                if (e.get("from") == acc or e.get("to") == acc
                    or e.get("source") == acc or e.get("target") == acc):
                    degree += 1
            return 10 + min(degree * 3, 30)
        except Exception:
            return 15

    def _edge_width(self, amount: float) -> float:
        """根据金额计算边宽度"""
        try:
            if amount <= 0:
                return 1.0
            if amount < 10000:
                return 1.0
            if amount < 100000:
                return 2.0
            if amount < 1000000:
                return 3.0
            return 4.0
        except Exception:
            return 1.0

    # ============================================================
    # 内部：HTML 拼装
    # ============================================================
    def _assemble_html(
        self,
        account_data: Dict,
        community_data: Dict,
        transaction_data: Dict,
        execution_id: str,
        default_view: str,
    ) -> str:
        """拼装完整 HTML（含 vis.js CDN + 视图切换）"""
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 序列化数据为 JSON
        account_json = json.dumps(account_data, ensure_ascii=False)
        community_json = json.dumps(community_data, ensure_ascii=False)
        transaction_json = json.dumps(transaction_data, ensure_ascii=False)

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>反洗钱关联图谱 - {_escape_html(execution_id)}</title>
    <link href="{_VIS_NETWORK_CSS}" rel="stylesheet" type="text/css" />
    <style>
        body {{
            font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            margin: 0;
            padding: 0;
            background: #f5f6fa;
        }}
        .header {{
            background: #2c3e50;
            color: white;
            padding: 15px 20px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }}
        .header h1 {{
            margin: 0;
            font-size: 20px;
        }}
        .header .meta {{
            font-size: 12px;
            color: #bdc3c7;
            margin-top: 5px;
        }}
        .toolbar {{
            background: white;
            padding: 10px 20px;
            border-bottom: 1px solid #eee;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .toolbar button {{
            padding: 8px 16px;
            border: 1px solid #3498db;
            background: white;
            color: #3498db;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.2s;
        }}
        .toolbar button:hover {{
            background: #3498db;
            color: white;
        }}
        .toolbar button.active {{
            background: #3498db;
            color: white;
        }}
        .toolbar .stats {{
            margin-left: auto;
            font-size: 13px;
            color: #7f8c8d;
        }}
        #mynetwork {{
            width: 100%;
            height: calc(100vh - 110px);
            background: white;
        }}
        .legend {{
            position: absolute;
            bottom: 20px;
            left: 20px;
            background: white;
            padding: 10px 15px;
            border-radius: 4px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.15);
            font-size: 12px;
        }}
        .legend .item {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin: 4px 0;
        }}
        .legend .color {{
            width: 16px;
            height: 16px;
            border-radius: 50%;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>反洗钱关联图谱</h1>
        <div class="meta">
            执行ID: {_escape_html(execution_id)} |
            生成时间: {_escape_html(generated_at)} |
            构建器版本: {__INTERACTIVE_GRAPH_BUILDER_VERSION__} |
            数据源: 真实工作流状态（戒律 M1）
        </div>
    </div>
    <div class="toolbar">
        <button id="btn-account" onclick="switchView('account')" class="{'active' if default_view == 'account' else ''}">账户视图</button>
        <button id="btn-community" onclick="switchView('community')" class="{'active' if default_view == 'community' else ''}">团伙视图</button>
        <button id="btn-transaction" onclick="switchView('transaction')" class="{'active' if default_view == 'transaction' else ''}">交易时间轴</button>
        <span class="stats" id="stats"></span>
    </div>
    <div id="mynetwork"></div>
    <div class="legend">
        <div class="item"><div class="color" style="background:#c0392b"></div>critical (≥85)</div>
        <div class="item"><div class="color" style="background:#e74c3c"></div>high (70-84)</div>
        <div class="item"><div class="color" style="background:#f39c12"></div>medium (50-69)</div>
        <div class="item"><div class="color" style="background:#3498db"></div>low (1-49)</div>
        <div class="item"><div class="color" style="background:#95a5a6"></div>normal (0)</div>
    </div>

    <script src="{_VIS_NETWORK_CDN}"></script>
    <script>
        // 三种视图数据（戒律 M1: 来自真实工作流状态）
        var viewData = {{
            account: {account_json},
            community: {community_json},
            transaction: {transaction_json}
        }};

        var network = null;
        var currentView = "{default_view}";

        function renderView(viewName) {{
            var data = viewData[viewName];
            if (!data || !data.nodes) data = {{nodes: [], edges: []}};

            var container = document.getElementById('mynetwork');
            var nodes = new vis.DataSet(data.nodes);
            var edges = new vis.DataSet(data.edges);

            var options = {{
                nodes: {{
                    shape: 'dot',
                    scaling: {{ min: 10, max: 35 }},
                    font: {{ size: 12, face: 'Microsoft YaHei' }}
                }},
                edges: {{
                    arrows: {{ to: {{ enabled: true, scaleFactor: 0.5 }} }},
                    font: {{ size: 10, strokeWidth: 0, strokeColor: '#ffffff' }},
                    color: {{ color: '#bdc3c7', highlight: '#3498db' }},
                    smooth: {{ type: 'continuous' }}
                }},
                physics: {{
                    stabilization: {{ iterations: 150 }},
                    barnesHut: {{
                        gravitationalConstant: -8000,
                        springConstant: 0.04,
                        springLength: 120
                    }}
                }},
                interaction: {{
                    hover: true,
                    tooltipDelay: 200,
                    navigationButtons: true,
                    keyboard: true
                }}
            }};

            if (network) network.destroy();
            network = new vis.Network(container, {{ nodes: nodes, edges: edges }}, options);

            // 更新统计
            var stats = document.getElementById('stats');
            stats.textContent = '节点: ' + data.nodes.length + ' | 边: ' + data.edges.length;
        }}

        function switchView(viewName) {{
            currentView = viewName;
            // 更新按钮状态
            document.getElementById('btn-account').classList.remove('active');
            document.getElementById('btn-community').classList.remove('active');
            document.getElementById('btn-transaction').classList.remove('active');
            document.getElementById('btn-' + viewName).classList.add('active');
            renderView(viewName);
        }}

        // 初始渲染
        renderView(currentView);
    </script>
</body>
</html>"""


# ============================================================
# 模块级便捷函数
# ============================================================
def generate_interactive_graph(state: Dict[str, Any], output_path: str = None) -> str:
    """
    便捷函数: 从工作流状态生成交互式图谱

    Args:
        state: 工作流最终状态
        output_path: 输出路径

    Returns:
        HTML 文件路径，失败返回空字符串
    """
    try:
        builder = InteractiveGraphBuilder()
        return builder.export_html(state, output_path)
    except Exception as e:
        print(f"  [交互图谱] 生成失败: {e}")
        return ""
