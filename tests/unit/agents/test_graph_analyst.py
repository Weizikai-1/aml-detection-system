"""
图分析 Agent 单元测试

测试覆盖:
1. 图构建正确性
2. 节点风险评分
3. 中心性计算(PageRank/介数/度中心性)
4. 社区发现
5. 可疑社区识别
6. 证据增强
"""
from agents.graph_analyst import (
    _build_graph,
    _compute_node_risk_scores,
    _compute_centrality,
    _detect_communities,
    _identify_suspicious_communities,
    _enrich_suspicious_with_graph,
    create_graph_analyst_agent,
)
from graph.state import AMLState, SuspiciousTransaction


def _make_txn(tid, frm, to, amount, ts="2026-07-26T10:00:00"):
    return {
        "transaction_id": tid,
        "from_account": frm,
        "to_account": to,
        "amount": amount,
        "timestamp": ts,
        "transaction_type": "transfer",
        "remark": "",
    }


# ============ 测试: 图构建 ============

def test_build_graph_basic():
    """基础图构建: 3账户2笔交易"""
    txns = [
        _make_txn("T1", "A", "B", 10000),
        _make_txn("T2", "B", "C", 5000),
    ]
    G = _build_graph(txns)

    assert G.number_of_nodes() == 3
    assert G.number_of_edges() == 2
    assert G.has_node("A")
    assert G.has_edge("A", "B")


def test_build_graph_edge_aggregation():
    """同方向多笔交易应聚合到同一条边"""
    txns = [
        _make_txn("T1", "A", "B", 10000),
        _make_txn("T2", "A", "B", 20000),
        _make_txn("T3", "A", "B", 5000),
    ]
    G = _build_graph(txns)

    assert G.number_of_edges() == 1
    assert G["A"]["B"]["total_amount"] == 35000
    assert G["A"]["B"]["txn_count"] == 3
    assert len(G["A"]["B"]["txn_ids"]) == 3


def test_build_graph_node_stats():
    """节点出入度和金额统计正确"""
    txns = [
        _make_txn("T1", "A", "B", 10000),
        _make_txn("T2", "B", "C", 5000),
        _make_txn("T3", "C", "B", 3000),
    ]
    G = _build_graph(txns)

    assert G.nodes["B"]["in_degree"] == 2
    assert G.nodes["B"]["out_degree"] == 1
    assert G.nodes["B"]["in_amount"] == 13000
    assert G.nodes["B"]["out_amount"] == 5000
    assert G.nodes["B"]["total_txns"] == 3


# ============ 测试: 节点风险评分 ============

def test_node_risk_scores():
    """规则命中的账户应有更高风险评分"""
    txns = [
        _make_txn("T1", "A", "B", 80000),
        _make_txn("T2", "C", "D", 1000),
    ]
    G = _build_graph(txns)

    rule_hits: list[SuspiciousTransaction] = [
        {
            "transaction": txns[0],
            "rule_hits": ["large_amount"],
            "risk_score": 80,
            "evidence": ["大额交易"],
        }
    ]

    scores = _compute_node_risk_scores(G, rule_hits)

    assert scores["A"] > 50
    assert scores["B"] > 50
    assert scores["C"] < 30
    assert scores["D"] < 30


# ============ 测试: 中心性计算 ============

def test_centrality_computation():
    """PageRank、介数中心性、度中心性均能正确计算"""
    txns = [
        _make_txn("T1", "A", "B", 10000),
        _make_txn("T2", "B", "C", 10000),
        _make_txn("T3", "B", "D", 10000),
        _make_txn("T4", "C", "E", 10000),
    ]
    G = _build_graph(txns)
    centrality = _compute_centrality(G)

    assert "pagerank" in centrality
    assert "betweenness" in centrality
    assert "degree_centrality" in centrality

    # B 是中心节点，度中心性最高
    assert centrality["degree_centrality"]["B"] == max(centrality["degree_centrality"].values())

    # 所有值都是 0-1 之间
    for v in centrality["pagerank"].values():
        assert 0 <= v <= 1
    for v in centrality["betweenness"].values():
        assert 0 <= v <= 1
    for v in centrality["degree_centrality"].values():
        assert 0 <= v <= 1


# ============ 测试: 社区发现 ============

def test_community_detection():
    """两个独立团伙应被识别为两个社区"""
    txns = [
        # 团伙1: A-B-C-A 闭环
        _make_txn("T1", "A", "B", 10000),
        _make_txn("T2", "B", "C", 10000),
        _make_txn("T3", "C", "A", 10000),
        # 团伙2: D-E-F-D 闭环
        _make_txn("T4", "D", "E", 10000),
        _make_txn("T5", "E", "F", 10000),
        _make_txn("T6", "F", "D", 10000),
    ]
    G = _build_graph(txns)
    communities = _detect_communities(G)

    # 至少2个社区
    assert len(communities) >= 2
    # 每个社区3个账户
    assert all(len(c) == 3 for c in communities[:2])


# ============ 测试: 可疑社区识别 ============

def test_suspicious_community_identification():
    """高风险成员比例高的社区应排名靠前"""
    txns = [
        _make_txn("T1", "A", "B", 10000),
        _make_txn("T2", "B", "C", 10000),
        _make_txn("T3", "C", "A", 10000),
    ]
    G = _build_graph(txns)

    rule_hits: list[SuspiciousTransaction] = [
        {"transaction": txns[0], "rule_hits": ["smurfing"], "risk_score": 90, "evidence": ["分拆转账"]},
        {"transaction": txns[1], "rule_hits": ["smurfing"], "risk_score": 90, "evidence": ["分拆转账"]},
        {"transaction": txns[2], "rule_hits": ["smurfing"], "risk_score": 90, "evidence": ["分拆转账"]},
    ]

    risk_scores = _compute_node_risk_scores(G, rule_hits)
    centrality = _compute_centrality(G)
    communities = _detect_communities(G)
    suspicious = _identify_suspicious_communities(G, communities, risk_scores, centrality)

    assert len(suspicious) > 0
    assert suspicious[0]["avg_risk_score"] > 50
    assert suspicious[0]["community_risk"] > 30
    assert "top_risk_members" in suspicious[0]
    assert "core_nodes_by_pagerank" in suspicious[0]


# ============ 测试: 证据增强 ============

def test_enrich_suspicious_with_graph():
    """图分析应增强可疑交易的证据链"""
    txns = [
        _make_txn("T1", "A", "B", 80000),
        _make_txn("T2", "B", "C", 70000),
        _make_txn("T3", "C", "A", 75000),
    ]
    G = _build_graph(txns)

    rule_hits: list[SuspiciousTransaction] = [
        {"transaction": txns[0], "rule_hits": ["large_amount"], "risk_score": 70, "evidence": ["大额交易"]},
    ]

    risk_scores = _compute_node_risk_scores(G, rule_hits)
    centrality = _compute_centrality(G)
    communities = _detect_communities(G)
    suspicious_communities = _identify_suspicious_communities(G, communities, risk_scores, centrality)

    enriched = _enrich_suspicious_with_graph(
        G, rule_hits, suspicious_communities, risk_scores, centrality
    )

    assert len(enriched) == 1
    # 证据链增加了图分析内容
    assert len(enriched[0]["evidence"]) >= 2
    assert any("图分析" in e for e in enriched[0]["evidence"])
    # 风险评分因图分析而提升
    assert enriched[0]["risk_score"] >= rule_hits[0]["risk_score"]


# ============ 测试: Agent 节点函数 ============

def test_graph_analyst_agent_empty():
    """空交易应返回空图"""
    agent = create_graph_analyst_agent()
    state: AMLState = {
        "cleaned_transactions": [],
        "rule_hits": [],
    }
    result = agent(state)

    assert result["graph_data"]["node_count"] == 0
    assert result["graph_hit_count"] == 0


def test_graph_analyst_agent_full():
    """完整流程: 构建图→中心性→社区→增强"""
    txns = [
        _make_txn(f"T{i}", f"A{i}", f"B{i}", 50000 + i * 1000)
        for i in range(5)
    ]
    # 加几个社区内交易
    txns += [
        _make_txn("T10", "A0", "A1", 30000),
        _make_txn("T11", "A1", "A2", 30000),
        _make_txn("T12", "A2", "A0", 30000),
    ]

    rule_hits: list[SuspiciousTransaction] = [
        {"transaction": txns[i], "rule_hits": ["large_amount"],
         "risk_score": 70, "evidence": [f"大额交易{i}"]}
        for i in range(5)
    ]

    agent = create_graph_analyst_agent()
    state: AMLState = {
        "cleaned_transactions": txns,
        "rule_hits": rule_hits,
    }
    result = agent(state)

    assert result["graph_data"]["node_count"] > 0
    assert result["graph_data"]["edge_count"] > 0
    assert "centrality" in result["graph_data"]
    assert result["graph_hit_count"] == len(txns) if len(txns) < len(rule_hits) else result["graph_hit_count"] >= len(rule_hits)
    assert "graph_stats" in result["graph_data"]
    assert result["current_step"] == "graph_analyst"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
