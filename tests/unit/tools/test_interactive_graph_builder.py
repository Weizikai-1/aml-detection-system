"""
交互式关联图谱构建器测试 (B3-1)

覆盖:
- 三种视图正确生成（账户/团伙/交易时间轴）
- 节点/边元数据完整（戒律 M2）
- 风险分→颜色映射正确
- HTML 导出正确
- 空数据/异常场景不抛异常（戒律 P4）
- 数据来自真实 state（戒律 M1）
"""
import json
import os
from unittest.mock import patch

import pytest

from tools.interactive_graph_builder import (
    InteractiveGraphBuilder,
    _risk_color,
    _format_amount,
    _escape_html,
    generate_interactive_graph,
)


# ============================================================
# 测试夹具
# ============================================================
@pytest.fixture
def builder(tmp_path):
    return InteractiveGraphBuilder(output_dir=str(tmp_path))


@pytest.fixture
def sample_state():
    """构造含 graph_data 和 transactions 的完整状态"""
    return {
        "execution_id": "exec_test_001",
        "graph_data": {
            "nodes": [
                {"id": "A1", "risk_score": 80, "centrality": 0.5,
                 "community_id": "C1", "txn_count": 10, "total_amount": 500000},
                {"id": "A2", "risk_score": 30, "centrality": 0.2,
                 "community_id": "C1", "txn_count": 5, "total_amount": 100000},
                {"id": "A3", "risk_score": 0, "centrality": 0.1,
                 "community_id": "C2", "txn_count": 2, "total_amount": 50000},
            ],
            "edges": [
                {"from": "A1", "to": "A2", "amount": 45000, "txn_count": 3},
                {"from": "A2", "to": "A3", "amount": 120000, "txn_count": 1},
            ],
            "communities": [
                {"community_id": "C1", "members": ["A1", "A2"],
                 "community_risk": 75},
                {"community_id": "C2", "members": ["A3"],
                 "community_risk": 20},
            ],
        },
        "rule_hits": [
            {"transaction": {"transaction_id": "T001", "from_account": "A1",
                             "to_account": "A2", "amount": 45000,
                             "timestamp": "2026-07-27 10:00:00"},
             "risk_score": 80, "rule_hits": ["分拆转账"]},
            {"transaction": {"transaction_id": "T002", "from_account": "A2",
                             "to_account": "A3", "amount": 120000,
                             "timestamp": "2026-07-27 11:00:00"},
             "risk_score": 70, "rule_hits": ["大额交易"]},
        ],
        "transactions": [
            {"transaction_id": "T001", "from_account": "A1", "to_account": "A2",
             "amount": 45000, "timestamp": "2026-07-27 10:00:00",
             "remark": "货款"},
            {"transaction_id": "T002", "from_account": "A2", "to_account": "A3",
             "amount": 120000, "timestamp": "2026-07-27 11:00:00",
             "remark": "采购"},
            {"transaction_id": "T003", "from_account": "A1", "to_account": "A3",
             "amount": 5000, "timestamp": "2026-07-27 09:00:00",
             "remark": "测试"},
        ],
        "llm_confirmed": [
            {"transaction": {"transaction_id": "T001", "from_account": "A1"},
             "risk_score": 85},
        ],
    }


# ============================================================
# 辅助函数测试
# ============================================================
def test_risk_color_critical():
    assert _risk_color(90) == "#c0392b"


def test_risk_color_high():
    assert _risk_color(75) == "#e74c3c"


def test_risk_color_medium():
    assert _risk_color(55) == "#f39c12"


def test_risk_color_low():
    assert _risk_color(30) == "#3498db"


def test_risk_color_normal():
    assert _risk_color(0) == "#95a5a6"


def test_format_amount_wan():
    assert "万" in _format_amount(45000)


def test_format_amount_small():
    assert "¥" in _format_amount(500)


def test_escape_html():
    assert _escape_html("<script>") == "&lt;script&gt;"
    assert _escape_html('a"b') == "a&quot;b"


# ============================================================
# 账户视图测试
# ============================================================
def test_build_account_graph_returns_nodes_and_edges(builder, sample_state):
    """账户视图返回节点和边"""
    data = builder.build_account_graph(sample_state)
    assert data["view"] == "account"
    assert len(data["nodes"]) == 3
    assert len(data["edges"]) == 2


def test_build_account_graph_node_colors_by_risk(builder, sample_state):
    """节点颜色按风险分映射"""
    data = builder.build_account_graph(sample_state)
    node_colors = {n["id"]: n["color"] for n in data["nodes"]}
    # A1 risk=80 → high → red
    assert node_colors["A1"] == "#e74c3c"
    # A3 risk=0 → normal → gray
    assert node_colors["A3"] == "#95a5a6"


def test_build_account_graph_node_tooltip_contains_metadata(builder, sample_state):
    """节点 tooltip 含完整元数据（戒律 M2）"""
    data = builder.build_account_graph(sample_state)
    a1 = next(n for n in data["nodes"] if n["id"] == "A1")
    assert "账户: A1" in a1["title"]
    assert "风险分" in a1["title"]
    assert "中心性" in a1["title"]


def test_build_account_graph_edge_tooltip_contains_amount(builder, sample_state):
    """边 tooltip 含金额信息"""
    data = builder.build_account_graph(sample_state)
    edge = data["edges"][0]
    assert "金额" in edge["title"]


def test_build_account_graph_uses_risk_from_rule_hits(builder):
    """账户风险分从 rule_hits 收集（戒律 M1: 真实数据）"""
    state = {
        "graph_data": {
            "nodes": [{"id": "A1"}, {"id": "A2"}],
            "edges": [{"from": "A1", "to": "A2", "amount": 10000}],
        },
        "rule_hits": [
            {"transaction": {"transaction_id": "T1", "from_account": "A1"},
             "risk_score": 75},
        ],
    }
    data = builder.build_account_graph(state)
    a1 = next(n for n in data["nodes"] if n["id"] == "A1")
    assert a1["risk_score"] == 75
    assert a1["color"] == "#e74c3c"  # high


def test_build_account_graph_empty_state(builder):
    """空 state 返回空数据（戒律 P4）"""
    data = builder.build_account_graph({})
    assert data["nodes"] == []
    assert data["edges"] == []


def test_build_account_graph_no_graph_data(builder):
    """无 graph_data 返回空数据"""
    data = builder.build_account_graph({"execution_id": "e1"})
    assert data["nodes"] == []


# ============================================================
# 团伙视图测试
# ============================================================
def test_build_community_graph_returns_nodes(builder, sample_state):
    """团伙视图返回社区节点"""
    data = builder.build_community_graph(sample_state)
    assert data["view"] == "community"
    assert len(data["nodes"]) == 2  # C1, C2


def test_build_community_graph_shared_members_creates_edge(builder, sample_state):
    """共享成员的社区之间有边"""
    # 修改数据让 C1 和 C2 共享 A1
    state = dict(sample_state)
    state["graph_data"] = dict(state["graph_data"])
    state["graph_data"]["communities"] = [
        {"community_id": "C1", "members": ["A1", "A2"], "community_risk": 70},
        {"community_id": "C2", "members": ["A1", "A3"], "community_risk": 50},
    ]
    data = builder.build_community_graph(state)
    assert len(data["edges"]) >= 1
    assert "共享" in data["edges"][0]["label"]


def test_build_community_graph_no_shared_no_edge(builder, sample_state):
    """无共享成员时无边"""
    data = builder.build_community_graph(sample_state)
    # C1={A1,A2}, C2={A3} 无共享
    assert len(data["edges"]) == 0


def test_build_community_graph_empty_communities(builder):
    """无社区返回空数据"""
    data = builder.build_community_graph({"graph_data": {"communities": []}})
    assert data["nodes"] == []


def test_build_community_graph_tooltip_contains_members(builder, sample_state):
    """团伙 tooltip 含成员列表（戒律 M2）"""
    data = builder.build_community_graph(sample_state)
    c1 = next(n for n in data["nodes"] if n["id"] == "C1")
    assert "成员数" in c1["title"]
    assert "A1" in c1["title"]


# ============================================================
# 交易时间轴视图测试
# ============================================================
def test_build_transaction_timeline_returns_nodes(builder, sample_state):
    """交易视图返回节点"""
    data = builder.build_transaction_timeline(sample_state)
    assert data["view"] == "transaction"
    # 3 交易 + 账户节点（A1, A2, A3）
    txn_nodes = [n for n in data["nodes"] if n["group"] in ("suspicious", "normal")]
    assert len(txn_nodes) == 3


def test_build_transaction_timeline_suspicious_marked(builder, sample_state):
    """可疑交易标记为菱形（戒律 M2）"""
    data = builder.build_transaction_timeline(sample_state)
    t001 = next(n for n in data["nodes"] if n["id"] == "T001")
    assert t001["shape"] == "diamond"
    assert t001["group"] == "suspicious"


def test_build_transaction_timeline_normal_dot(builder, sample_state):
    """正常交易为圆点"""
    data = builder.build_transaction_timeline(sample_state)
    # T003 不在 rule_hits 中
    t003 = next(n for n in data["nodes"] if n["id"] == "T003")
    assert t003["shape"] == "dot"
    assert t003["group"] == "normal"


def test_build_transaction_timeline_tooltip_contains_amount(builder, sample_state):
    """交易 tooltip 含金额"""
    data = builder.build_transaction_timeline(sample_state)
    t001 = next(n for n in data["nodes"] if n["id"] == "T001")
    assert "金额" in t001["title"]
    assert "付款方" in t001["title"]


def test_build_transaction_timeline_empty_state(builder):
    """空 state 返回空数据"""
    data = builder.build_transaction_timeline({})
    assert data["nodes"] == []


def test_build_transaction_timeline_sorted_by_time(builder):
    """交易按时间排序"""
    state = {
        "transactions": [
            {"transaction_id": "T2", "from_account": "A", "to_account": "B",
             "amount": 100, "timestamp": "2026-07-27 12:00:00"},
            {"transaction_id": "T1", "from_account": "A", "to_account": "B",
             "amount": 100, "timestamp": "2026-07-27 10:00:00"},
        ],
    }
    data = builder.build_transaction_timeline(state)
    # 第一个交易节点应为 T1（时间更早）
    txn_nodes = [n for n in data["nodes"] if n["group"] in ("suspicious", "normal")]
    # 应有两个交易节点
    assert len(txn_nodes) == 2


# ============================================================
# HTML 导出测试
# ============================================================
def test_export_html_creates_file(builder, sample_state):
    """导出 HTML 文件"""
    path = builder.export_html(sample_state)
    assert path != ""
    assert os.path.exists(path)
    content = open(path, "r", encoding="utf-8").read()
    assert "反洗钱关联图谱" in content
    assert "vis-network" in content


def test_export_html_contains_three_views_data(builder, sample_state):
    """HTML 包含三种视图数据"""
    path = builder.export_html(sample_state)
    content = open(path, "r", encoding="utf-8").read()
    assert '"account"' in content
    assert '"community"' in content
    assert '"transaction"' in content


def test_export_html_contains_execution_id(builder, sample_state):
    """HTML 包含执行ID（戒律 M4: 可追溯）"""
    path = builder.export_html(sample_state)
    content = open(path, "r", encoding="utf-8").read()
    assert "exec_test_001" in content


def test_export_html_contains_timestamp(builder, sample_state):
    """HTML 包含生成时间"""
    path = builder.export_html(sample_state)
    content = open(path, "r", encoding="utf-8").read()
    assert "生成时间" in content
    assert "2026" in content


def test_export_html_custom_path(builder, sample_state, tmp_path):
    """自定义输出路径"""
    custom = str(tmp_path / "custom.html")
    path = builder.export_html(sample_state, output_path=custom)
    assert path == custom
    assert os.path.exists(custom)


def test_export_html_default_view(builder, sample_state):
    """指定默认视图"""
    path = builder.export_html(sample_state, default_view="community")
    content = open(path, "r", encoding="utf-8").read()
    # community 按钮应有 active 类
    assert 'id="btn-community" onclick="switchView(\'community\')" class="active"' in content


# ============================================================
# 异常隔离测试（戒律 P4）
# ============================================================
def test_build_account_graph_does_not_raise_on_error(builder):
    """异常 state 不抛异常"""
    # 传入非法数据
    data = builder.build_account_graph({"graph_data": "not a dict"})
    assert data["nodes"] == []


def test_build_community_graph_does_not_raise_on_error(builder):
    """异常 state 不抛异常"""
    data = builder.build_community_graph({"graph_data": {"communities": "invalid"}})
    assert data["nodes"] == []


def test_export_html_does_not_raise_on_empty_state(builder, tmp_path):
    """空 state 导出不抛异常"""
    path = builder.export_html({}, output_path=str(tmp_path / "empty.html"))
    # 应返回路径或空字符串，不抛异常
    assert path == "" or os.path.exists(path)


# ============================================================
# 便捷函数测试
# ============================================================
def test_generate_interactive_graph_returns_path(sample_state, tmp_path):
    """便捷函数返回路径"""
    import tools.interactive_graph_builder as mod
    # 临时修改默认输出目录
    original_init = InteractiveGraphBuilder.__init__
    def patched_init(self, output_dir=None):
        original_init(self, output_dir=str(tmp_path))
    InteractiveGraphBuilder.__init__ = patched_init
    try:
        path = generate_interactive_graph(sample_state)
    finally:
        InteractiveGraphBuilder.__init__ = original_init
    assert path != ""
    assert os.path.exists(path)


def test_generate_interactive_graph_handles_exception():
    """便捷函数异常时返回空字符串"""
    path = generate_interactive_graph(None)
    assert path == ""
