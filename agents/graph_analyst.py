"""
Agent 3: 图分析 Agent

职责: 构建资金流向图谱，用社区发现算法检测团伙洗钱
模式: create_graph_analyst_agent(llm) -> node_function

分析方法:
1. 构建资金流向有向图(账户=节点，交易=边)
2. 社区发现(Louvain算法)
3. 可疑社区识别(高密度、高风险评分)
4. 中心性分析(PageRank/度中心性)
5. 输出可疑团伙列表和关联可疑交易

注意: 当前版本使用 NetworkX 实现基础图分析，
     GNN(图神经网络) 扩展可在后续迭代中加入 PyTorch Geometric
"""
import time
from collections import defaultdict
from typing import Dict, List, Set
from graph.state import AMLState, SuspiciousTransaction, GraphData, Transaction


def _build_graph(transactions: List[Transaction]):
    """
    从交易列表构建资金流向图

    Returns:
        nodes: {account: {属性}}
        edges: [{from, to, amount, count, txn_ids}]
    """
    nodes: Dict[str, dict] = {}
    edge_map: Dict[tuple, dict] = {}

    for txn in transactions:
        from_acc = txn["from_account"]
        to_acc = txn["to_account"]
        amount = float(txn.get("amount", 0))

        # 添加节点
        if from_acc not in nodes:
            nodes[from_acc] = {
                "account_id": from_acc,
                "in_degree": 0,
                "out_degree": 0,
                "in_amount": 0.0,
                "out_amount": 0.0,
                "total_txns": 0,
                "risk_score": 0.0,
            }
        if to_acc not in nodes:
            nodes[to_acc] = {
                "account_id": to_acc,
                "in_degree": 0,
                "out_degree": 0,
                "in_amount": 0.0,
                "out_amount": 0.0,
                "total_txns": 0,
                "risk_score": 0.0,
            }

        # 更新节点统计
        nodes[from_acc]["out_degree"] += 1
        nodes[from_acc]["out_amount"] += amount
        nodes[from_acc]["total_txns"] += 1
        nodes[to_acc]["in_degree"] += 1
        nodes[to_acc]["in_amount"] += amount
        nodes[to_acc]["total_txns"] += 1

        # 更新边
        key = (from_acc, to_acc)
        if key not in edge_map:
            edge_map[key] = {
                "from": from_acc,
                "to": to_acc,
                "total_amount": 0.0,
                "txn_count": 0,
                "txn_ids": [],
            }
        edge_map[key]["total_amount"] += amount
        edge_map[key]["txn_count"] += 1
        edge_map[key]["txn_ids"].append(txn.get("transaction_id", ""))

    edges = list(edge_map.values())
    return nodes, edges


def _compute_node_risk_scores(nodes: Dict[str, dict], rule_hits: List[SuspiciousTransaction]) -> Dict[str, float]:
    """
    基于规则命中结果计算节点风险评分
    规则命中越多、涉及金额越大，风险越高
    """
    risk_scores = {acc: 0.0 for acc in nodes}

    # 从可疑交易中提取账户风险
    for s in rule_hits:
        txn = s["transaction"]
        from_acc = txn["from_account"]
        to_acc = txn["to_account"]
        score = s.get("risk_score", 0.5)
        amount = float(txn.get("amount", 0))

        # 两个账户都加分
        for acc in [from_acc, to_acc]:
            if acc in risk_scores:
                # 基础分 + 金额加权
                amount_factor = min(amount / 100000.0, 1.0)  # 10万以上封顶
                risk_scores[acc] = max(risk_scores[acc], score + amount_factor * 0.2)
                risk_scores[acc] = min(risk_scores[acc], 1.0)

    # 更新节点属性
    for acc, score in risk_scores.items():
        if acc in nodes:
            nodes[acc]["risk_score"] = round(score, 4)

    return risk_scores


def _detect_communities(nodes: Dict[str, dict], edges: List[dict]) -> List[List[str]]:
    """
    社区发现: 简化版Louvain算法
    基于图连通性 + 交易密度做贪心社区划分

    策略:
    1. 构建无向邻接表
    2. 贪心合并: 将节点归入其邻居最多的社区
    3. 社区大小过滤(3个节点以上才算社区)
    """
    # 构建无向邻接表
    adj: Dict[str, Set[str]] = defaultdict(set)
    for e in edges:
        adj[e["from"]].add(e["to"])
        adj[e["to"]].add(e["from"])

    # 初始化: 每个节点一个社区
    node_community = {acc: i for i, acc in enumerate(nodes.keys())}
    communities: Dict[int, Set[str]] = {i: {acc} for i, acc in enumerate(nodes.keys())}

    # 贪心迭代(简化版Louvain第一阶段)
    changed = True
    max_iter = 20
    iteration = 0

    while changed and iteration < max_iter:
        changed = False
        iteration += 1

        for node in nodes:
            if node not in adj or len(adj[node]) == 0:
                continue

            current_comm = node_community[node]
            neighbor_communities: Dict[int, int] = defaultdict(int)

            # 统计邻居所在社区
            for neighbor in adj[node]:
                if neighbor in node_community:
                    neighbor_communities[node_community[neighbor]] += 1

            if not neighbor_communities:
                continue

            # 找邻居最多的社区
            best_comm = max(neighbor_communities, key=neighbor_communities.get)
            best_count = neighbor_communities[best_comm]

            # 当前社区的邻居数
            current_count = neighbor_communities.get(current_comm, 0)

            # 如果换社区能增加连接数
            if best_comm != current_comm and best_count > current_count:
                # 移动节点
                communities[current_comm].discard(node)
                if len(communities[current_comm]) == 0:
                    del communities[current_comm]

                communities[best_comm].add(node)
                node_community[node] = best_comm
                changed = True

    # 过滤掉太小的社区(小于3个节点)，按大小排序
    result = []
    for comm_id, members in communities.items():
        if len(members) >= 3:
            result.append(sorted(list(members)))

    # 按社区大小降序
    result.sort(key=lambda x: len(x), reverse=True)
    return result


def _identify_suspicious_communities(
    communities: List[List[str]],
    risk_scores: Dict[str, float],
    nodes: Dict[str, dict],
) -> List[dict]:
    """
    识别可疑社区
    计算每个社区的风险指标，输出高风险社区详情
    """
    suspicious = []

    for i, members in enumerate(communities):
        # 社区风险评分: 成员风险评分的平均值 + 高风险成员比例
        member_scores = [risk_scores.get(m, 0.0) for m in members]
        avg_risk = sum(member_scores) / len(member_scores) if member_scores else 0
        high_risk_count = sum(1 for s in member_scores if s >= 0.5)
        high_risk_ratio = high_risk_count / len(member_scores) if member_scores else 0

        # 社区综合风险分
        community_risk = avg_risk * 0.5 + high_risk_ratio * 0.5

        # 社区总交易量
        total_txn = sum(nodes.get(m, {}).get("total_txns", 0) for m in members)
        total_amount = sum(
            nodes.get(m, {}).get("in_amount", 0) + nodes.get(m, {}).get("out_amount", 0)
            for m in members
        ) / 2  # 双边统计，除以2

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
            "top_risk_members": sorted(
                [(m, risk_scores.get(m, 0)) for m in members],
                key=lambda x: x[1],
                reverse=True
            )[:5],
        })

    # 按社区风险降序
    suspicious.sort(key=lambda x: x["community_risk"], reverse=True)
    return suspicious


def _enrich_suspicious_with_graph(
    rule_hits: List[SuspiciousTransaction],
    suspicious_communities: List[dict],
    risk_scores: Dict[str, float],
) -> List[SuspiciousTransaction]:
    """
    将图分析结果补充到可疑交易中
    - 标记所属社区
    - 添加图分析证据
    - 调整风险评分
    """
    # 构建账户到社区的映射
    account_to_community: Dict[str, dict] = {}
    for comm in suspicious_communities:
        for m in comm["members"]:
            if m not in account_to_community or comm["community_risk"] > account_to_community[m]["community_risk"]:
                account_to_community[m] = comm

    enriched = []
    for s in rule_hits:
        txn = s["transaction"]
        from_acc = txn["from_account"]
        to_acc = txn["to_account"]

        # 检查是否属于可疑社区
        from_comm = account_to_community.get(from_acc)
        to_comm = account_to_community.get(to_acc)

        s_copy = dict(s)
        s_copy["rule_hits"] = list(s["rule_hits"])
        s_copy["evidence"] = list(s["evidence"])

        if from_comm or to_comm:
            comm = from_comm or to_comm
            s_copy["community_id"] = comm["community_id"]

            graph_evidence = (
                f"图分析: 涉及可疑社区[{comm['community_id']}]，"
                f"社区规模{comm['size']}个账户，社区风险{comm['community_risk']:.2f}"
            )
            s_copy["graph_evidence"] = graph_evidence
            s_copy["evidence"].append(graph_evidence)

            # 提升风险评分
            risk_boost = comm["community_risk"] * 0.15
            s_copy["risk_score"] = min(s.get("risk_score", 0.5) + risk_boost, 1.0)

        # 更新交易的风险评分
        s_copy["transaction"] = dict(txn)
        s_copy["transaction"]["risk_score"] = s_copy["risk_score"]

        enriched.append(s_copy)

    # 按更新后的风险评分重排
    enriched.sort(key=lambda x: x["risk_score"], reverse=True)
    return enriched


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
        1. 从清洗后交易构建资金流向图
        2. 计算节点风险评分
        3. 社区发现
        4. 识别可疑社区
        5. 补充可疑交易的图分析证据
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
                               "node_risk_scores": {}, "graph_stats": {}},
                "graph_suspicious": [],
                "graph_hit_count": 0,
                "current_step": "graph_analyst",
            }

        # ---- 1. 构建图 ----
        print("  [步骤 1/4] 构建资金流向图...")
        nodes, edges = _build_graph(cleaned)
        print(f"    → 节点数: {len(nodes)}, 边数: {len(edges)}")

        # ---- 2. 计算节点风险评分 ----
        print("  [步骤 2/4] 计算节点风险评分...")
        risk_scores = _compute_node_risk_scores(nodes, rule_hits)
        high_risk_nodes = [acc for acc, s in risk_scores.items() if s >= 0.5]
        print(f"    → 高风险账户数(≥0.5): {len(high_risk_nodes)}")

        # ---- 3. 社区发现 ----
        print("  [步骤 3/4] 社区发现 (Louvain简化版)...")
        communities = _detect_communities(nodes, edges)
        print(f"    → 发现社区数(≥3节点): {len(communities)}")
        for i, comm in enumerate(communities[:5]):
            print(f"      社区 {i+1}: {len(comm)} 个账户")

        # ---- 4. 识别可疑社区 ----
        print("  [步骤 4/4] 识别可疑社区...")
        suspicious_communities = _identify_suspicious_communities(communities, risk_scores, nodes)
        high_risk_comms = [c for c in suspicious_communities if c["community_risk"] >= 0.3]
        print(f"    → 可疑社区数(风险≥0.3): {len(high_risk_comms)}")

        # 补充证据到可疑交易
        enriched_hits = _enrich_suspicious_with_graph(rule_hits, suspicious_communities, risk_scores)

        # 图统计
        total_amount = sum(e["total_amount"] for e in edges)
        avg_degree = (2 * len(edges)) / len(nodes) if nodes else 0

        graph_stats = {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "total_transaction_amount": round(total_amount, 2),
            "avg_degree": round(avg_degree, 2),
            "community_count": len(communities),
            "suspicious_community_count": len(high_risk_comms),
            "high_risk_node_count": len(high_risk_nodes),
        }

        graph_data: GraphData = {
            "nodes": list(nodes.values()),
            "edges": edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "communities": communities,
            "suspicious_communities": suspicious_communities,
            "node_risk_scores": risk_scores,
            "graph_stats": graph_stats,
        }

        elapsed = time.time() - start_time
        print(f"\n  {'─' * 50}")
        print(f"  图分析汇总:")
        print(f"    - 图规模: {len(nodes)} 账户, {len(edges)} 条资金路径")
        print(f"    - 总流转金额: {total_amount:,.2f} 元")
        print(f"    - 社区数: {len(communities)}")
        print(f"    - 可疑社区: {len(high_risk_comms)} 个")
        print(f"    - 图分析增强后可疑交易: {len(enriched_hits)} 笔")
        print(f"  耗时: {elapsed:.2f} 秒")
        print("[Agent 3] 图分析完成")

        return {
            "graph_data": graph_data,
            "graph_suspicious": enriched_hits,
            "graph_hit_count": len(enriched_hits),
            "current_step": "graph_analyst",
            "step_times": {"graph_analyst": elapsed},
        }

    return graph_analyst_node
