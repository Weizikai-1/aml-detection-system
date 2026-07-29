"""
Agent 3: 图分析 Agent

职责: 构建资金流向图谱，用 NetworkX 图算法检测团伙洗钱
模式: create_graph_analyst_agent(llm) -> node_function

分析方法:
1. 构建资金流向有向图(账户=节点，交易=边，金额=权重)
2. PageRank 识别核心账户
3. 介数中心性识别资金中转枢纽
4. 社区发现(Greedy Modularity)检测团伙结构
5. 可疑社区识别 + 交易证据增强
6. GNN 节点分类:
   - PaySim 数据 + PyG 可用 → EdgeAwareGAT (边特征增强，可解释注意力)
   - 否则降级到 GCN/GAT (仅拓扑结构)
7. 合并 GNN 风险分到节点属性，补充图数据返回
"""
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
import networkx as nx
from graph.state import AMLState, SuspiciousTransaction, Transaction, GraphData

def _build_graph(transactions: List[Transaction]) -> nx.DiGraph:
    """
    从交易列表构建资金流向有向图

    节点属性: account_id, in_degree, out_degree, in_amount, out_amount, total_txns
    边属性: total_amount, txn_count, txn_ids
    """
    G = nx.DiGraph()

    for txn in transactions:
        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        # 戒律 M1: 缺失账户字段跳过，不编造
        if not from_acc or not to_acc:
            continue
        # 戒律 P2: 自转账导致自环，过滤掉
        if from_acc == to_acc:
            continue
        # 戒律 M1: amount 缺失（None）跳过，不编造
        raw_amount = txn.get("amount")
        if raw_amount is None:
            continue
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError):
            continue
        tid = txn.get("transaction_id", "")

        # 添加节点
        for acc in [from_acc, to_acc]:
            if not G.has_node(acc):
                G.add_node(acc,
                    account_id=acc,
                    in_degree=0,
                    out_degree=0,
                    in_amount=0.0,
                    out_amount=0.0,
                    total_txns=0,
                    risk_score=0.0,
                )

        # 更新出入度
        G.nodes[from_acc]["out_degree"] += 1
        G.nodes[from_acc]["out_amount"] += amount
        G.nodes[from_acc]["total_txns"] += 1
        G.nodes[to_acc]["in_degree"] += 1
        G.nodes[to_acc]["in_amount"] += amount
        G.nodes[to_acc]["total_txns"] += 1

        # 添加/更新边
        if G.has_edge(from_acc, to_acc):
            G[from_acc][to_acc]["total_amount"] += amount
            G[from_acc][to_acc]["txn_count"] += 1
            G[from_acc][to_acc]["txn_ids"].append(tid)
        else:
            G.add_edge(from_acc, to_acc,
                total_amount=amount,
                txn_count=1,
                txn_ids=[tid],
            )

    return G


def _compute_node_risk_scores(G: nx.DiGraph, rule_hits: List[SuspiciousTransaction]) -> Dict[str, float]:
    """
    基于规则命中结果计算节点风险评分
    规则命中越多、涉及金额越大，风险越高（百分制 0-100）
    """
    risk_scores = {n: 0.0 for n in G.nodes()}

    for s in rule_hits:
        txn = s.get("transaction", {})
        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        # 戒律 M1: 缺失账户字段跳过
        if not from_acc or not to_acc:
            continue
        # 戒律 M1: 风险评分可能缺失或非法，保守处理
        try:
            score = float(s.get("risk_score", 50))
        except (TypeError, ValueError):
            score = 50.0
        try:
            amount = float(txn.get("amount", 0))
        except (TypeError, ValueError):
            amount = 0.0

        for acc in [from_acc, to_acc]:
            if acc in risk_scores:
                amount_factor = min(amount / 100000.0, 1.0)
                new_score = score + amount_factor * 20  # 金额最多加20分
                # 戒律 M3: 限制在 0-100 范围
                risk_scores[acc] = max(risk_scores[acc], min(max(new_score, 0), 100.0))

    # 写回节点属性
    for acc, score in risk_scores.items():
        G.nodes[acc]["risk_score"] = round(score, 2)

    return risk_scores


def _compute_centrality(G: nx.DiGraph) -> Dict[str, Dict[str, float]]:
    """
    计算图中心性指标:
    - pagerank: 节点在资金流转中的重要性
    - betweenness: 资金中转枢纽程度
    - degree_centrality: 连接广泛程度
    """
    # PageRank: 用交易金额作为权重
    # 金额越大权重越高，归一化后作为 personalization
    total_out = {n: d["out_amount"] for n, d in G.nodes(data=True)}
    max_out = max(total_out.values()) if total_out else 1.0
    # 戒律 P4: 避免除零（所有节点 out_amount 都为 0 时）
    if max_out <= 0:
        max_out = 1.0
    personalization = {n: total_out[n] / max_out + 0.01 for n in G.nodes()}

    try:
        pagerank = nx.pagerank(G, alpha=0.85, personalization=personalization, max_iter=100)
    except nx.PowerIterationFailedConvergence:
        pagerank = {n: 1.0 / G.number_of_nodes() for n in G.nodes()}

    # 度中心性
    degree_centrality = nx.degree_centrality(G)

    # 介数中心性(大图可能慢，但80节点级没问题)
    # 修复：weight 参数表示"路径成本"（越大越不愿走），但 total_amount 是金额（越大越重要）
    # 使用金额倒数作为成本，使高金额边被视为"短路径"
    if G.number_of_nodes() > 1:
        for u, v, d in G.edges(data=True):
            amt = d.get("total_amount", 0)
            # 戒律 M1: 金额类型校验，避免非数值导致除零或类型异常
            if isinstance(amt, (int, float)) and amt > 0:
                d["weight_cost"] = 1.0 / amt
            else:
                d["weight_cost"] = 1.0
        betweenness = nx.betweenness_centrality(G, weight="weight_cost", normalized=True)
    else:
        betweenness = {n: 0.0 for n in G.nodes()}

    # 写回节点属性
    for n in G.nodes():
        G.nodes[n]["pagerank"] = round(pagerank.get(n, 0), 6)
        G.nodes[n]["betweenness"] = round(betweenness.get(n, 0), 6)
        G.nodes[n]["degree_centrality"] = round(degree_centrality.get(n, 0), 6)

    return {
        "pagerank": pagerank,
        "betweenness": betweenness,
        "degree_centrality": degree_centrality,
    }


def _detect_communities(G: nx.DiGraph) -> List[List[str]]:
    """
    社区发现: Greedy Modularity Communities

    基于模块度优化的贪心社区划分，比简单连通分量更精准。
    过滤掉小于3个节点的社区。
    """
    # 转为无向图做社区发现(资金双向流动视为连通)
    undirected = G.to_undirected()

    # 去除孤立节点
    isolated = [n for n in undirected.nodes() if undirected.degree(n) == 0]
    for n in isolated:
        undirected.remove_node(n)

    if undirected.number_of_nodes() == 0:
        return []

    try:
        communities = list(nx.community.greedy_modularity_communities(
            undirected,
            weight="total_amount",
        ))
    except Exception:
        # 退化: 按连通分量划分
        communities = list(nx.connected_components(undirected))

    # 过滤小于3的社区，转为列表，按大小排序
    result = [sorted(list(c)) for c in communities if len(c) >= 3]
    result.sort(key=lambda x: len(x), reverse=True)
    return result


def _identify_suspicious_communities(
    G: nx.DiGraph,
    communities: List[List[str]],
    risk_scores: Dict[str, float],
    centrality: Dict[str, Dict[str, float]],
) -> List[dict]:
    """
    识别可疑社区

    综合评估维度:
    - 平均风险评分
    - 高风险成员比例
    - 社区规模
    - 内部交易密度
    """
    pagerank = centrality.get("pagerank", {})
    suspicious = []

    for i, members in enumerate(communities):
        member_scores = [risk_scores.get(m, 0.0) for m in members]
        avg_risk = sum(member_scores) / len(member_scores) if member_scores else 0
        high_risk_count = sum(1 for s in member_scores if s >= 50)
        high_risk_ratio = high_risk_count / len(member_scores) if member_scores else 0

        # 社区总交易量
        total_txn = sum(G.nodes[m]["total_txns"] for m in members)
        total_amount = sum(
            G.nodes[m]["in_amount"] + G.nodes[m]["out_amount"]
            for m in members
        ) / 2

        # 内部边密度
        internal_edges = 0
        for u in members:
            for v in members:
                if G.has_edge(u, v):
                    internal_edges += 1
        max_internal = len(members) * (len(members) - 1)
        density = internal_edges / max_internal if max_internal > 0 else 0

        # 社区综合风险分（百分制）: 风险评分*0.5 + 高风险比例*30 + 密度*20
        community_risk = avg_risk * 0.5 + high_risk_ratio * 30 + density * 20

        # 前5高风险成员
        top_risk = sorted(
            [(m, risk_scores.get(m, 0)) for m in members],
            key=lambda x: x[1],
            reverse=True,
        )[:5]

        # 核心节点(PageRank top3)
        top_pr = sorted(
            [(m, pagerank.get(m, 0)) for m in members],
            key=lambda x: x[1],
            reverse=True,
        )[:3]

        suspicious.append({
            "community_id": f"COMM_{i + 1:03d}",
            "members": members,
            "size": len(members),
            "avg_risk_score": round(avg_risk, 4),
            "high_risk_count": high_risk_count,
            "high_risk_ratio": round(high_risk_ratio, 3),
            "community_risk": round(community_risk, 4),
            "total_transactions": total_txn,
            "total_amount": round(total_amount, 2),
            "internal_density": round(density, 4),
            "top_risk_members": top_risk,
            "core_nodes_by_pagerank": top_pr,
        })

    suspicious.sort(key=lambda x: x["community_risk"], reverse=True)
    return suspicious


def _enrich_suspicious_with_graph(
    G: nx.DiGraph,
    rule_hits: List[SuspiciousTransaction],
    suspicious_communities: List[dict],
    risk_scores: Dict[str, float],
    centrality: Dict[str, Dict[str, float]],
) -> List[SuspiciousTransaction]:
    """
    将图分析结果补充到可疑交易中
    - 标记所属社区
    - 添加图分析证据(PageRank/介数/社区)
    - 调整风险评分
    """
    pagerank = centrality.get("pagerank", {})
    betweenness = centrality.get("betweenness", {})

    # 构建账户到社区的映射(取风险最高的社区)
    account_to_community: Dict[str, dict] = {}
    for comm in suspicious_communities:
        for m in comm["members"]:
            if m not in account_to_community or comm["community_risk"] > account_to_community[m]["community_risk"]:
                account_to_community[m] = comm

    enriched = []
    for s in rule_hits:
        txn = s.get("transaction", {})
        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        # 戒律 M1: 缺失账户字段跳过
        if not from_acc or not to_acc:
            continue

        s_copy = dict(s)
        s_copy["rule_hits"] = list(s.get("rule_hits", []))
        s_copy["evidence"] = list(s.get("evidence", []))
        s_copy["transaction"] = dict(txn)

        # 图分析证据
        graph_evidence_parts = []

        # PageRank 指标
        pr_max = max(pagerank.get(from_acc, 0), pagerank.get(to_acc, 0))
        if pr_max > 0.05:
            graph_evidence_parts.append(f"PageRank高({pr_max:.4f})")

        # 介数中心性
        bt_max = max(betweenness.get(from_acc, 0), betweenness.get(to_acc, 0))
        if bt_max > 0.1:
            graph_evidence_parts.append(f"介数中心性高({bt_max:.3f})")

        # 社区信息
        from_comm = account_to_community.get(from_acc)
        to_comm = account_to_community.get(to_acc)
        comm = from_comm or to_comm

        if comm:
            s_copy["community_id"] = comm["community_id"]
            graph_evidence_parts.append(
                f"涉及可疑社区[{comm['community_id']}]({comm['size']}账户,风险{comm['community_risk']:.2f})"
            )

        if graph_evidence_parts:
            graph_evidence = "图分析: " + "，".join(graph_evidence_parts)
            s_copy["graph_evidence"] = graph_evidence
            s_copy["evidence"].append(graph_evidence)

            # 风险评分提升（百分制）: 社区风险*0.15 + 中心性加成(最多10分)
            risk_boost = 0.0
            if comm:
                risk_boost += comm["community_risk"] * 0.15
            risk_boost += min(pr_max * 50, 10)
            # 戒律 M3: 限制在 0-100 范围（双向夹紧）
            try:
                base_score = float(s.get("risk_score", 50))
            except (TypeError, ValueError):
                base_score = 50.0
            s_copy["risk_score"] = max(min(base_score + risk_boost, 100), 0)

        s_copy["transaction"]["risk_score"] = s_copy["risk_score"]
        enriched.append(s_copy)

    enriched.sort(key=lambda x: x["risk_score"], reverse=True)
    return enriched


def _run_edge_gnn(
    cleaned: List[Transaction],
    enriched_hits: List[SuspiciousTransaction],
) -> Tuple[Optional[dict], Optional[dict], str]:
    """
    使用 EdgeAwareGAT 进行节点可疑性分类（边特征增强）

    当数据为 PaySim 格式且 PyG 可用时启用，利用交易边特征
    (对数金额/交易类型/时间步/大额标记) 显式参与消息传递。

    戒律:
    - M1: 基于真实图数据，不编造
    - M2: 边特征在消息传递中被显式利用
    - M3: 输出概率限制在 [0, 1]
    - P2: 异常静默跳过，不阻塞主流程

    Returns:
        (gnn_result, metrics, model_label) 或 (None, None, "")
    """
    try:
        import pandas as pd
        import torch
        import torch.nn.functional as F
        from tools.dataset_builder import AMLGraphBuilder
        from tools.gnn_edge_model import create_edge_gnn, is_edge_gnn_available
    except ImportError:
        return None, None, ""

    if not is_edge_gnn_available():
        return None, None, ""

    # 1. 字段检测：必须有 PaySim 必需列
    df = pd.DataFrame(cleaned)
    required = ["nameOrig", "nameDest", "amount", "step", "type"]
    if not all(c in df.columns for c in required):
        return None, None, ""

    # 2. 构建同构图（账户-账户，带边特征）
    builder = AMLGraphBuilder()
    builder.build_from_transactions(df, use_transaction_nodes=False)

    data = builder.to_pyg_data()

    # 3. 用规则命中结果作为弱监督标签（risk_score ≥ 50 → 可疑）
    rule_risk_scores: Dict[str, float] = {}
    for s in enriched_hits:
        txn = s.get("transaction") or {}
        try:
            score = float(s.get("risk_score", 50))
        except (TypeError, ValueError):
            score = 50.0
        for acc in (txn.get("from_account"), txn.get("to_account")):
            if acc:
                rule_risk_scores[acc] = max(rule_risk_scores.get(acc, 0), score)

    labels = torch.zeros(builder.node_features.shape[0], dtype=torch.long)
    for acc, idx in builder.node_to_idx.items():
        if rule_risk_scores.get(acc, 0) >= 50:
            labels[idx] = 1
    data.y = labels

    # 4. 创建 EdgeAwareGAT 模型
    in_channels = data.x.size(1)
    edge_dim = data.edge_attr.size(1)
    model = create_edge_gnn(
        model_type="edge_aware_gat",
        in_channels=in_channels,
        hidden_channels=32,
        edge_dim=edge_dim,
        heads=2,
        dropout=0.3,
    )

    optimizer = torch.optim.Adam(model.parameters(), lr=0.01, weight_decay=5e-4)

    # 5. 8:2 划分训练/验证集
    n = data.num_nodes
    perm = torch.randperm(n)
    train_mask = torch.zeros(n, dtype=torch.bool)
    val_mask = torch.zeros(n, dtype=torch.bool)
    split = int(0.8 * n)
    train_mask[perm[:split]] = True
    val_mask[perm[split:]] = True

    metrics = {"final_train_acc": 0.0, "final_val_acc": 0.0, "epochs": 100}

    # 6. 训练（戒律 P4: 训练集为空时跳过避免 cross_entropy 抛错）
    if train_mask.sum() > 0:
        for _ in range(100):
            model.train()
            optimizer.zero_grad()
            out = model(data.x, data.edge_index, data.edge_attr)
            loss = F.cross_entropy(out[train_mask], data.y[train_mask])
            loss.backward()
            optimizer.step()

        model.eval()
        with torch.no_grad():
            pred = model(data.x, data.edge_index, data.edge_attr).argmax(dim=1)
            metrics["final_train_acc"] = (
                (pred[train_mask] == data.y[train_mask]).float().mean().item()
            )
            if val_mask.sum() > 0:
                metrics["final_val_acc"] = (
                    (pred[val_mask] == data.y[val_mask]).float().mean().item()
                )

    # 7. 推理（戒律 M3: softmax 概率 ∈ [0, 1]）
    model.eval()
    with torch.no_grad():
        probs = model.predict(data.x, data.edge_index, data.edge_attr).cpu().numpy()

    idx_to_account = {v: k for k, v in builder.node_to_idx.items()}
    scores = {idx_to_account[i]: float(probs[i]) for i in range(len(probs))}
    high_risk = [(acc, s) for acc, s in scores.items() if s > 0.5]
    high_risk.sort(key=lambda x: x[1], reverse=True)

    stats = {
        "total_nodes": len(scores),
        "high_risk_count": len(high_risk),
        "high_risk_ratio": len(high_risk) / len(scores) if scores else 0,
        "avg_score": float(probs.mean()) if len(probs) > 0 else 0.0,
        "max_score": float(probs.max()) if len(probs) > 0 else 0.0,
        "min_score": float(probs.min()) if len(probs) > 0 else 0.0,
    }

    gnn_result = {
        "scores": scores,
        "high_risk": high_risk,
        "stats": stats,
        "model_type": "edge_aware_gat",
    }
    return gnn_result, metrics, "EdgeAwareGAT"


def _generate_gnn_alerts(
    gnn_result: dict,
    metrics: Optional[dict],
    model_label: str,
    enriched_hits: List[SuspiciousTransaction],
    cleaned: List[Transaction],
    G: nx.DiGraph,
) -> List[SuspiciousTransaction]:
    """
    将 GNN 高风险账户转为可疑交易告警（仅对规则未命中账户）

    戒律:
    - M1/P3: 不编造合成交易，关联真实交易作为证据
    - M3: 风险分映射到 [0, 100]
    - P1: 高风险节点必须有高置信度

    Returns:
        新发现的可疑交易列表
    """
    # 概率 → 百分制（戒律 M3: 双向夹紧到 [0, 100]）
    gnn_scores_pct = {
        acc: round(max(min(s, 1), 0) * 100, 2)
        for acc, s in gnn_result["scores"].items()
    }
    gnn_high_risk_pct = [
        (acc, round(max(min(s, 1), 0) * 100, 2))
        for acc, s in gnn_result["high_risk"]
    ]
    avg_score_pct = gnn_result["stats"]["avg_score"] * 100

    print(f"    → {model_label} 高风险账户(≥50分): {len(gnn_high_risk_pct)} 个")
    print(f"    → {model_label} 平均风险分: {avg_score_pct:.2f}")
    if metrics:
        print(
            f"    → {model_label} Train Acc: {metrics['final_train_acc']:.2%} | "
            f"Val Acc: {metrics['final_val_acc']:.2%}"
        )

    # 合并 GNN 分数到节点属性
    for acc, score in gnn_scores_pct.items():
        if acc in G.nodes:
            G.nodes[acc]["gnn_risk_score"] = score

    # 已命中账户集合
    hit_accounts = set()
    for s in enriched_hits:
        txn = s.get("transaction", {})
        for k in ("from_account", "to_account"):
            v = txn.get(k)
            if v:
                hit_accounts.add(v)

    # 新发现告警
    new_suspects = [
        (acc, score) for acc, score in gnn_high_risk_pct
        if acc not in hit_accounts
    ]
    if new_suspects:
        print(f"    → {model_label} 新发现 {len(new_suspects)} 个规则漏掉的高风险账户")

    discovered: List[SuspiciousTransaction] = []
    for acc, score in new_suspects[:10]:  # 最多 10 个，避免膨胀
        node_data = G.nodes[acc]
        related_txns = [
            t for t in cleaned
            if t.get("from_account") == acc or t.get("to_account") == acc
        ][:5]
        # 戒律 M1/P3: 无真实关联交易则跳过，禁止编造合成交易
        if not related_txns:
            print(f"    → 账户[{acc}]无真实关联交易，跳过告警生成（戒律 M1/P3）")
            continue
        primary_txn = related_txns[0]
        evidence = (
            f"{model_label}节点分类: 账户[{acc}]被预测为高风险({score:.2f}分)，"
            f"规则引擎未命中。关联真实交易{len(related_txns)}笔，"
            f"入账总额{node_data.get('in_amount', 0):,.2f}，"
            f"出账总额{node_data.get('out_amount', 0):,.2f}"
        )
        discovered.append({
            "transaction": primary_txn,
            "rule_hits": [f"{model_label}节点分类"],
            "risk_score": score,
            "evidence": [evidence],
            "graph_evidence": evidence,
            "llm_analysis": None,
            "llm_confidence": None,
            "is_false_positive": None,
            "community_id": None,
            "gnn_alert": True,
            "related_txn_count": len(related_txns),
        })
    return discovered


def create_graph_analyst_agent(llm=None):
    """
    创建图分析Agent

    Args:
        llm: LLM 实例(保留接口，图分析以算法为主)

    Returns:
        可直接传入 StateGraph.add_node 的节点函数
    """

    def graph_analyst_node(state: AMLState) -> dict:
        """
        图分析节点函数

        工作内容:
        1. 构建资金流向有向图
        2. 计算节点风险评分
        3. 计算中心性指标(PageRank/介数/度中心性)
        4. 社区发现
        5. 识别可疑社区
        6. 增强可疑交易证据
        """
        start_time = time.time()
        print("\n" + "=" * 60)
        print("[Agent 3] 图分析 Agent 启动")
        print("=" * 60)

        cleaned = state.get("cleaned_transactions", [])
        rule_hits = state.get("rule_hits", [])

        print(f"  输入交易数: {len(cleaned)}")
        print(f"  规则命中数: {len(rule_hits)}")

        if len(cleaned) == 0:
            print("[Agent 3] 无交易数据，跳过图分析")
            return {
                "graph_data": {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0,
                               "communities": [], "suspicious_communities": [],
                               "node_risk_scores": {}, "graph_stats": {},
                               "centrality": {}, "gnn_result": None},
                "graph_suspicious": [],
                "graph_hit_count": 0,
                "current_step": "graph_analyst",
            }

        # ---- 1. 构建图 ----
        print("  [步骤 1/6] 构建资金流向图...")
        G = _build_graph(cleaned)
        node_count = G.number_of_nodes()
        edge_count = G.number_of_edges()
        print(f"    → 节点数: {node_count}, 边数: {edge_count}")

        # ---- 2. 计算节点风险评分 ----
        print("  [步骤 2/6] 计算节点风险评分...")
        risk_scores = _compute_node_risk_scores(G, rule_hits)
        high_risk_nodes = [acc for acc, s in risk_scores.items() if s >= 50]
        print(f"    → 高风险账户数(≥50分): {len(high_risk_nodes)}")

        # ---- 3. 中心性分析 ----
        print("  [步骤 3/6] 中心性分析 (PageRank / 介数 / 度中心性)...")
        centrality = _compute_centrality(G)
        pr_top = sorted(centrality["pagerank"].items(), key=lambda x: x[1], reverse=True)[:3]
        bt_top = sorted(centrality["betweenness"].items(), key=lambda x: x[1], reverse=True)[:3]
        print(f"    → PageRank Top3: {', '.join(f'{n}({v:.4f})' for n, v in pr_top)}")
        print(f"    → 介数中心性 Top3: {', '.join(f'{n}({v:.3f})' for n, v in bt_top)}")

        # ---- 4. 社区发现 ----
        print("  [步骤 4/6] 社区发现 (Greedy Modularity)...")
        communities = _detect_communities(G)
        print(f"    → 发现社区数(≥3节点): {len(communities)}")
        for i, comm in enumerate(communities[:5]):
            print(f"      社区 {i+1}: {len(comm)} 个账户")

        # ---- 5. 识别可疑社区 ----
        print("  [步骤 5/6] 识别可疑社区...")
        suspicious_communities = _identify_suspicious_communities(
            G, communities, risk_scores, centrality
        )
        high_risk_comms = [c for c in suspicious_communities if c["community_risk"] >= 0.3]
        print(f"    → 可疑社区数(风险≥0.3): {len(high_risk_comms)}")

        # 增强可疑交易证据
        enriched_hits = _enrich_suspicious_with_graph(
            G, rule_hits, suspicious_communities, risk_scores, centrality
        )

        # ---- 6. GNN 节点分类 ----
        print("  [步骤 6/6] GNN 可疑账户识别...")
        gnn_result = None
        gnn_metrics = None
        gnn_model_label = ""
        gnn_discovered_suspicious: List[SuspiciousTransaction] = []

        if node_count > 5:
            # 优先尝试 EdgeAwareGAT（PaySim 数据 + PyG 可用）
            # 戒律 P2: 异常静默降级，不阻塞主流程
            if state.get("paysim_features") is not None:
                try:
                    gnn_result, gnn_metrics, gnn_model_label = _run_edge_gnn(
                        cleaned, enriched_hits
                    )
                    if gnn_result is not None:
                        print(f"    → 使用 EdgeAwareGAT (边特征增强)")
                except Exception as e:
                    print(f"    → EdgeAwareGAT 失败，降级到标准 GNN: {e}")
                    gnn_result = None

            # 降级路径: 标准 GCN/GAT (仅拓扑结构)
            if gnn_result is None:
                try:
                    from tools.gnn_trainer import prepare_gnn_data, train_gnn, infer_gnn
                    gnn_data = prepare_gnn_data(cleaned, enriched_hits)
                    model, gnn_metrics = train_gnn(gnn_data, epochs=200, verbose=False)
                    gnn_result = infer_gnn(model, gnn_data)
                    gnn_model_label = "GNN"
                    print(f"    → 使用标准 GNN (GCN/GAT)")
                except ImportError:
                    print("    → PyTorch/PyG 未安装，跳过 GNN 分析")
                except Exception as e:
                    print(f"    → GNN 分析失败: {str(e)}")

            # 生成告警（EdgeGNN 与标准 GNN 共用逻辑）
            if gnn_result is not None:
                gnn_discovered_suspicious = _generate_gnn_alerts(
                    gnn_result,
                    gnn_metrics,
                    gnn_model_label,
                    enriched_hits,
                    cleaned,
                    G,
                )
        else:
            print(f"    → 节点数({node_count})不足，跳过 GNN 分析")

        # 图统计
        total_amount = sum(d["total_amount"] for _, _, d in G.edges(data=True))
        avg_degree = (2 * edge_count) / node_count if node_count else 0

        graph_stats = {
            "node_count": node_count,
            "edge_count": edge_count,
            "total_transaction_amount": round(total_amount, 2),
            "avg_degree": round(avg_degree, 2),
            "community_count": len(communities),
            "suspicious_community_count": len(high_risk_comms),
            "high_risk_node_count": len(high_risk_nodes),
            "is_directed": True,
            "density": round(nx.density(G), 6),
        }

        # 节点数据(转为列表字典)
        nodes_data = []
        for n, data in G.nodes(data=True):
            node_dict = dict(data)
            node_dict["account_id"] = n
            nodes_data.append(node_dict)

        # 边数据
        edges_data = []
        for u, v, data in G.edges(data=True):
            edge_dict = dict(data)
            edge_dict["from"] = u
            edge_dict["to"] = v
            edges_data.append(edge_dict)

        graph_data: GraphData = {
            "nodes": nodes_data,
            "edges": edges_data,
            "node_count": node_count,
            "edge_count": edge_count,
            "communities": communities,
            "suspicious_communities": suspicious_communities,
            "node_risk_scores": risk_scores,
            "graph_stats": graph_stats,
            "centrality": {
                "pagerank": {k: round(v, 6) for k, v in centrality["pagerank"].items()},
                "betweenness": {k: round(v, 6) for k, v in centrality["betweenness"].items()},
                "degree_centrality": {k: round(v, 6) for k, v in centrality["degree_centrality"].items()},
            },
            "gnn_result": gnn_result,
        }

        # 合并 GNN 新发现到可疑列表
        all_suspicious = enriched_hits + gnn_discovered_suspicious

        elapsed = time.time() - start_time
        print(f"\n  {'─' * 50}")
        print(f"  图分析汇总:")
        print(f"    - 图规模: {node_count} 账户, {edge_count} 条资金路径")
        print(f"    - 总流转金额: {total_amount:,.2f} 元")
        print(f"    - 图密度: {graph_stats['density']:.6f}")
        print(f"    - 社区数: {len(communities)}")
        print(f"    - 可疑社区: {len(high_risk_comms)} 个")
        print(f"    - 图分析增强后可疑交易: {len(enriched_hits)} 笔")
        if gnn_result:
            label = gnn_model_label or "GNN"
            print(f"    - {label} 高风险账户: {gnn_result['stats']['high_risk_count']} 个 (均分 {gnn_result['stats']['avg_score']:.4f})")
            if gnn_discovered_suspicious:
                print(f"    - {label} 新发现可疑: {len(gnn_discovered_suspicious)} 笔（规则引擎未命中）")
        print(f"  耗时: {elapsed:.2f} 秒")
        print("[Agent 3] 图分析完成")

        return {
            "graph_data": graph_data,
            "graph_suspicious": all_suspicious,
            "graph_hit_count": len(all_suspicious),
            "current_step": "graph_analyst",
            "step_times": {"graph_analyst": elapsed},
        }

    return graph_analyst_node
