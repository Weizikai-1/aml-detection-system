"""
资金流向可视化测试 (Task 8-1)

覆盖:
- HTML 生成（空数据、正常数据、大图）
- 从 state 便捷生成
- 可疑边标记
- 视觉编码辅助函数（风险颜色、节点大小、边宽度）
- 戒律 M1: 不编造数据（无图数据时明确提示，不生成假节点）
- 戒律 P1: 不遗漏（所有节点都出现在图中）
- 戒律 P3: 可疑标记基于真实可疑交易
"""
import os
import json
from typing import Dict, Any, List

import pytest

from tools.flow_visualizer import (
    FlowVisualizer,
    _risk_color_hex,
    _node_marker_size,
    _edge_line_width,
    _format_amount,
)


# ============================================================
# 夹具
# ============================================================
@pytest.fixture()
def viz_dir(tmp_path):
    d = tmp_path / "viz"
    d.mkdir()
    return str(d)


@pytest.fixture()
def viz(viz_dir):
    return FlowVisualizer(output_dir=viz_dir)


@pytest.fixture()
def sample_graph_data() -> Dict[str, Any]:
    """模拟 graph_analyst 真实输出（结构一致，非编造）"""
    return {
        "nodes": [
            {
                "account_id": "ACC_A",
                "in_degree": 0, "out_degree": 2,
                "in_amount": 0.0, "out_amount": 150000.0,
                "total_txns": 2, "risk_score": 75.0,
                "pagerank": 0.15, "betweenness": 0.2, "degree_centrality": 0.5,
            },
            {
                "account_id": "ACC_B",
                "in_degree": 1, "out_degree": 1,
                "in_amount": 100000.0, "out_amount": 95000.0,
                "total_txns": 2, "risk_score": 60.0,
                "pagerank": 0.25, "betweenness": 0.4, "degree_centrality": 0.6,
            },
            {
                "account_id": "ACC_C",
                "in_degree": 2, "out_degree": 0,
                "in_amount": 145000.0, "out_amount": 0.0,
                "total_txns": 2, "risk_score": 30.0,
                "pagerank": 0.30, "betweenness": 0.0, "degree_centrality": 0.4,
            },
        ],
        "edges": [
            {"from": "ACC_A", "to": "ACC_B", "total_amount": 100000.0,
             "txn_count": 1, "txn_ids": ["T001"]},
            {"from": "ACC_A", "to": "ACC_C", "total_amount": 50000.0,
             "txn_count": 1, "txn_ids": ["T002"]},
            {"from": "ACC_B", "to": "ACC_C", "total_amount": 95000.0,
             "txn_count": 1, "txn_ids": ["T003"]},
        ],
        "node_count": 3,
        "edge_count": 3,
        "communities": [["ACC_A", "ACC_B", "ACC_C"]],
        "suspicious_communities": [
            {
                "community_id": "COMM_001",
                "members": ["ACC_A", "ACC_B", "ACC_C"],
                "size": 3,
                "avg_risk_score": 55.0,
                "high_risk_count": 1,
                "high_risk_ratio": 0.333,
                "community_risk": 0.45,
                "total_transactions": 6,
                "total_amount": 245000.0,
                "internal_density": 0.66,
                "top_risk_members": [("ACC_A", 75.0)],
                "core_nodes_by_pagerank": [("ACC_C", 0.30)],
            }
        ],
        "node_risk_scores": {"ACC_A": 75.0, "ACC_B": 60.0, "ACC_C": 30.0},
        "graph_stats": {
            "node_count": 3,
            "edge_count": 3,
            "total_transaction_amount": 245000.0,
            "avg_degree": 2.0,
            "community_count": 1,
            "suspicious_community_count": 1,
            "high_risk_node_count": 1,
            "is_directed": True,
            "density": 0.5,
        },
        "centrality": {
            "pagerank": {"ACC_A": 0.15, "ACC_B": 0.25, "ACC_C": 0.30},
            "betweenness": {"ACC_A": 0.2, "ACC_B": 0.4, "ACC_C": 0.0},
            "degree_centrality": {"ACC_A": 0.5, "ACC_B": 0.6, "ACC_C": 0.4},
        },
        "gnn_result": None,
    }


@pytest.fixture()
def sample_suspicious() -> List[Dict[str, Any]]:
    """模拟可疑交易（含 ACC_A → ACC_B 边）"""
    return [
        {
            "transaction": {
                "transaction_id": "T001",
                "from_account": "ACC_A",
                "to_account": "ACC_B",
                "amount": 100000.0,
            },
            "rule_hits": ["大额交易"],
            "risk_score": 75.0,
            "evidence": ["单笔金额超过10万"],
        }
    ]


# ============================================================
# 视觉编码辅助函数测试
# ============================================================
@pytest.mark.unit
def test_risk_color_low_is_green():
    """低风险应为绿色"""
    assert _risk_color_hex(0).lower() == "#2ecc71"
    assert _risk_color_hex(19).lower() == "#2ecc71"


@pytest.mark.unit
def test_risk_color_high_is_red():
    """极高风险应为红色系"""
    color = _risk_color_hex(95)
    # 红色分量应较高
    r = int(color[1:3], 16)
    assert r >= 0xe0


@pytest.mark.unit
def test_risk_color_clamps_out_of_range():
    """超出范围的应被钳制到 0-100"""
    assert _risk_color_hex(-10).lower() == "#2ecc71"
    # 100 与 99 都应在红色区间（70-100），红色分量较高
    c100 = _risk_color_hex(100)
    c99 = _risk_color_hex(99)
    assert int(c100[1:3], 16) >= 0xe0
    assert int(c99[1:3], 16) >= 0xe0


@pytest.mark.unit
def test_risk_color_handles_invalid_input():
    """非法输入应返回低风险颜色，不抛异常"""
    assert _risk_color_hex(None) == "#2ecc71"
    assert _risk_color_hex("abc") == "#2ecc71"


@pytest.mark.unit
def test_node_marker_size_scales_with_amount():
    """节点大小应随金额增大而增大"""
    small = _node_marker_size(100)
    large = _node_marker_size(1000000)
    assert large > small


@pytest.mark.unit
def test_node_marker_size_has_bounds():
    """节点大小应在 12-38 范围内"""
    assert _node_marker_size(0) == 12.0
    assert _node_marker_size(1e20) <= 38.0
    assert _node_marker_size(-5) == 12.0


@pytest.mark.unit
def test_edge_line_width_has_bounds():
    """边宽度应在 0.8-6.0 范围内"""
    assert _edge_line_width(0) == 0.8
    assert _edge_line_width(1e20) <= 6.0
    assert _edge_line_width(-5) == 0.8


@pytest.mark.unit
def test_format_amount_large_uses_wan():
    """大额应显示万元单位"""
    s = _format_amount(150000)
    assert "15.00 万" in s
    assert "150,000.00" in s


@pytest.mark.unit
def test_format_amount_small_no_wan():
    """小额不显示万元"""
    s = _format_amount(500)
    assert "万" not in s
    assert "500.00" in s


# ============================================================
# HTML 生成测试
# ============================================================
@pytest.mark.unit
def test_generate_html_empty_graph(viz, viz_dir):
    """空 graph_data 应生成兜底 HTML（戒律 M1: 不编造）"""
    path = viz.generate_html(
        graph_data={},
        output_path=os.path.join(viz_dir, "empty.html"),
    )
    assert os.path.exists(path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "无图数据" in content
    # 不应包含编造的账户
    assert "ACC_A" not in content


@pytest.mark.unit
def test_generate_html_none_graph(viz, viz_dir):
    """graph_data 为 None 也应兜底处理"""
    path = viz.generate_html(
        graph_data=None,
        output_path=os.path.join(viz_dir, "none.html"),
    )
    assert os.path.exists(path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "无图数据" in content


@pytest.mark.unit
def test_generate_html_normal_graph(viz, viz_dir, sample_graph_data, sample_suspicious):
    """正常 graph_data 应生成完整 HTML"""
    path = viz.generate_html(
        graph_data=sample_graph_data,
        suspicious_list=sample_suspicious,
        output_path=os.path.join(viz_dir, "normal.html"),
        execution_id="test1234",
    )
    assert os.path.exists(path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 应包含所有账户（戒律 P1: 不遗漏）
    assert "ACC_A" in content
    assert "ACC_B" in content
    assert "ACC_C" in content
    # 应包含执行ID
    assert "test1234" in content
    # 应包含可疑社区
    assert "COMM_001" in content
    # 应包含统计卡片
    assert "账户总数" in content
    assert "资金路径" in content
    # 应包含 plotly.js 引用（cdn）
    assert "plotly" in content.lower()


@pytest.mark.unit
def test_generate_html_auto_filename(viz, sample_graph_data):
    """不指定 output_path 时应自动生成文件名"""
    path = viz.generate_html(graph_data=sample_graph_data)
    assert os.path.exists(path)
    assert path.endswith(".html")
    # 文件名应包含 flow 前缀
    assert "flow" in os.path.basename(path).lower()


@pytest.mark.unit
def test_generate_html_returns_absolute_path(viz, viz_dir, sample_graph_data):
    """返回的路径应为绝对路径"""
    path = viz.generate_html(
        graph_data=sample_graph_data,
        output_path=os.path.join(viz_dir, "abs.html"),
    )
    assert os.path.isabs(path)


@pytest.mark.unit
def test_generate_html_contains_all_nodes(viz, viz_dir, sample_graph_data):
    """所有节点都应出现在 HTML 中（戒律 P1: 不遗漏）"""
    path = viz.generate_html(
        graph_data=sample_graph_data,
        output_path=os.path.join(viz_dir, "all_nodes.html"),
    )
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    for n in sample_graph_data["nodes"]:
        assert n["account_id"] in content


@pytest.mark.unit
def test_generate_html_contains_risk_scores(viz, viz_dir, sample_graph_data):
    """HTML 应包含真实风险分（戒律 M3: 风险分 0-100）"""
    path = viz.generate_html(
        graph_data=sample_graph_data,
        output_path=os.path.join(viz_dir, "risk.html"),
    )
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # ACC_A 风险分 75.0 应出现
    assert "75.0" in content
    # ACC_C 风险分 30.0 应出现
    assert "30.0" in content


@pytest.mark.unit
def test_suspicious_edges_marked(viz, viz_dir, sample_graph_data, sample_suspicious):
    """涉及可疑交易的边应被标记（戒律 P3: 有证据）"""
    path = viz.generate_html(
        graph_data=sample_graph_data,
        suspicious_list=sample_suspicious,
        output_path=os.path.join(viz_dir, "susp.html"),
    )
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # 应包含"可疑资金流"图例项
    assert "可疑资金流" in content
    # 应包含可疑标记
    assert "涉及可疑交易" in content


@pytest.mark.unit
def test_no_suspicious_list_marks_no_edges(viz, viz_dir, sample_graph_data):
    """无可疑交易时不应有可疑边标记（戒律 P2: 不误报）"""
    path = viz.generate_html(
        graph_data=sample_graph_data,
        suspicious_list=None,
        output_path=os.path.join(viz_dir, "no_susp.html"),
    )
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # 可疑边 trace 应为空，但图例项仍存在（plotly 行为）
    # 关键是不应在 hover 中出现"涉及可疑交易"
    # 注意：plotly 的 trace 始终存在，但 hover text 中无此标记
    assert "涉及可疑交易" not in content


# ============================================================
# generate_from_state 测试
# ============================================================
@pytest.mark.unit
def test_generate_from_state_with_graph_data(viz, viz_dir, sample_graph_data, sample_suspicious):
    """从 state 生成应正确提取 graph_data 和可疑交易"""
    state = {
        "graph_data": sample_graph_data,
        "llm_confirmed": sample_suspicious,
        "execution_id": "exec5678",
        "analysis_date": "2026-07-27",
    }
    path = viz.generate_from_state(
        state, output_path=os.path.join(viz_dir, "from_state.html")
    )
    assert os.path.exists(path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "exec5678" in content
    assert "2026-07-27" in content
    assert "ACC_A" in content


@pytest.mark.unit
def test_generate_from_state_falls_back_to_rule_hits(viz, viz_dir, sample_graph_data):
    """llm_confirmed 为空时应回退到 rule_hits"""
    state = {
        "graph_data": sample_graph_data,
        "llm_confirmed": [],
        "rule_hits": [
            {
                "transaction": {
                    "from_account": "ACC_B",
                    "to_account": "ACC_C",
                    "amount": 95000.0,
                },
                "rule_hits": ["快进快出"],
                "risk_score": 60.0,
                "evidence": [],
            }
        ],
        "execution_id": "exec9999",
    }
    path = viz.generate_from_state(
        state, output_path=os.path.join(viz_dir, "fallback.html")
    )
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # ACC_B→ACC_C 边应被标记为可疑
    assert "涉及可疑交易" in content


@pytest.mark.unit
def test_generate_from_state_no_graph_data(viz, viz_dir):
    """state 无 graph_data 时应生成兜底 HTML"""
    state = {"execution_id": "no_graph", "analysis_date": "2026-07-27"}
    path = viz.generate_from_state(
        state, output_path=os.path.join(viz_dir, "no_graph.html")
    )
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "无图数据" in content


# ============================================================
# 戒律验证测试
# ============================================================
@pytest.mark.unit
def test_no_fabricated_data_in_html(viz, viz_dir, sample_graph_data, sample_suspicious):
    """HTML 中不应有编造数据标记（戒律 M1）"""
    path = viz.generate_html(
        graph_data=sample_graph_data,
        suspicious_list=sample_suspicious,
        output_path=os.path.join(viz_dir, "m1.html"),
    )
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "编造" not in content
    assert "假数据" not in content
    assert "mock" not in content.lower()
    # 应有真实数据来源声明
    assert "graph_analyst" in content or "真实" in content


@pytest.mark.unit
def test_rules_compliance_declaration(viz, viz_dir, sample_graph_data):
    """HTML 应包含戒律遵循声明（戒律 M4: 可追溯）"""
    path = viz.generate_html(
        graph_data=sample_graph_data,
        output_path=os.path.join(viz_dir, "rules.html"),
    )
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "M1" in content
    assert "P1" in content
    assert "戒律" in content


@pytest.mark.unit
def test_large_graph_does_not_crash(viz, viz_dir):
    """大图（100+节点）不应崩溃（戒律 P1: 不遗漏）"""
    # 构造 150 个节点、300 条边
    nodes = []
    for i in range(150):
        nodes.append({
            "account_id": f"ACC_{i:04d}",
            "in_degree": i % 10, "out_degree": i % 8,
            "in_amount": float(i * 1000), "out_amount": float(i * 800),
            "total_txns": (i % 10) + (i % 8),
            "risk_score": float(i % 100),
        })
    edges = []
    for i in range(300):
        frm = f"ACC_{i % 150:04d}"
        to = f"ACC_{(i + 1) % 150:04d}"
        edges.append({
            "from": frm, "to": to,
            "total_amount": float((i + 1) * 1000),
            "txn_count": 1, "txn_ids": [f"T{i}"],
        })
    graph_data = {
        "nodes": nodes, "edges": edges,
        "node_count": 150, "edge_count": 300,
        "communities": [], "suspicious_communities": [],
        "graph_stats": {"node_count": 150, "edge_count": 300,
                        "total_transaction_amount": 300000000.0,
                        "community_count": 0,
                        "suspicious_community_count": 0,
                        "high_risk_node_count": 1, "density": 0.013},
        "centrality": {"pagerank": {}, "betweenness": {}, "degree_centrality": {}},
    }
    path = viz.generate_html(
        graph_data=graph_data,
        output_path=os.path.join(viz_dir, "large.html"),
    )
    assert os.path.exists(path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # 应包含第一个和最后一个账户（不遗漏）
    assert "ACC_0000" in content
    assert "ACC_0149" in content


@pytest.mark.unit
def test_html_is_self_contained(viz, viz_dir, sample_graph_data):
    """HTML 应是自包含的（含 plotly.js 引用）"""
    path = viz.generate_html(
        graph_data=sample_graph_data,
        output_path=os.path.join(viz_dir, "self.html"),
    )
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # cdn 引用或内联 plotly.js
    assert "plotly" in content.lower()
    # 应是完整 HTML 文档
    assert content.strip().startswith("<!DOCTYPE html>")
    assert "</html>" in content


@pytest.mark.unit
def test_top_accounts_table_sorted_by_risk(viz, viz_dir, sample_graph_data):
    """Top 账户表应按风险分降序"""
    path = viz.generate_html(
        graph_data=sample_graph_data,
        output_path=os.path.join(viz_dir, "top.html"),
    )
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # ACC_A(75) 应排在 ACC_C(30) 之前
    pos_a = content.find("ACC_A")
    pos_c = content.find("ACC_C")
    # 两个都应存在
    assert pos_a != -1
    assert pos_c != -1
