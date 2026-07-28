"""
GNN 可解释性模块 — 解释图神经网络的预测结果

职责:
- 邻居重要性：哪些邻居账户导致了高风险评分
- 特征重要性：哪些节点特征贡献最大
- 子图解释：哪些子图结构最可疑
- 路径解释：哪条资金路径最可疑

设计准则:
- M1: 所有解释基于真实计算结果，不编造
- M2: 每个高风险预测必须有对应的解释证据
- P1: 解释不影响原始预测结果（解释是附加的）
"""
import torch
import numpy as np
from typing import Dict, List, Optional, Tuple
from collections import defaultdict


class GNNExplainer:
    """
    GNN 可解释性分析器

    支持三种解释方法:
    1. 注意力权重解释（GAT专用）
    2. 特征重要性分析（基于梯度/扰动）
    3. 邻居节点重要性排序
    """

    def __init__(self, model=None):
        self.model = model

    # ============================================================
    # 1. 注意力权重解释（GAT 专用）
    # ============================================================
    def explain_with_attention(self, attention_data: dict,
                               node_index: int,
                               top_k: int = 5) -> dict:
        """
        基于 GAT 注意力权重解释预测

        M2: 注意力权重就是证据，可追溯

        Args:
            attention_data: GAT 模型返回的注意力数据
            node_index: 要解释的节点索引
            top_k: 返回最重要的 K 个邻居

        Returns:
            {
                "node_index": 节点索引,
                "top_neighbors": [{node_index, attention_score, direction}],
                "layer1_attention": 第一层注意力,
                "layer2_attention": 第二层注意力,
            }
        """
        if not attention_data:
            return {"node_index": node_index, "top_neighbors": [], "reason": "无注意力数据"}

        # 聚合各层注意力权重
        all_neighbors = defaultdict(float)

        for layer_name in ["layer1", "layer2"]:
            layer_data = attention_data.get(layer_name, {})
            edge_index = layer_data.get("edge_index", None)
            attn = layer_data.get("attention", None)

            if edge_index is None or attn is None:
                continue

            # attn 形状: [E, heads] → 取平均
            if attn.dim() > 1:
                attn_mean = attn.mean(dim=-1)
            else:
                attn_mean = attn

            src_nodes = edge_index[0].detach().cpu().numpy()
            dst_nodes = edge_index[1].detach().cpu().numpy()
            attn_values = attn_mean.detach().cpu().numpy()

            # 找出指向目标节点的边（入边）
            mask = dst_nodes == node_index
            for i in np.where(mask)[0]:
                src = int(src_nodes[i])
                score = float(attn_values[i])
                all_neighbors[src] += score

            # 也考虑出边（双向都可能重要）
            mask_out = src_nodes == node_index
            for i in np.where(mask_out)[0]:
                dst = int(dst_nodes[i])
                score = float(attn_values[i]) * 0.5  # 出边权重减半
                all_neighbors[dst] += score

        # 排序并取 top_k
        sorted_neighbors = sorted(
            all_neighbors.items(), key=lambda x: x[1], reverse=True
        )[:top_k]

        top_neighbors = [
            {
                "node_index": node,
                "attention_score": round(score, 4),
                "rank": idx + 1,
            }
            for idx, (node, score) in enumerate(sorted_neighbors)
        ]

        return {
            "node_index": node_index,
            "top_neighbors": top_neighbors,
            "total_attention_score": round(sum(x[1] for x in sorted_neighbors), 4),
        }

    # ============================================================
    # 2. 特征重要性分析（基于扰动）
    # ============================================================
    def feature_importance(self, x: torch.Tensor,
                           edge_index: torch.Tensor,
                           node_index: int,
                           feature_names: List[str] = None) -> List[dict]:
        """
        特征重要性分析（扰动法）

        对每个特征做微小扰动，观察预测变化，
        变化越大说明该特征越重要。

        M1: 基于真实计算，不编造

        Args:
            x: 节点特征 [N, F]
            edge_index: 边索引 [2, E]
            node_index: 目标节点
            feature_names: 特征名称列表

        Returns:
            按重要性排序的特征列表
        """
        if self.model is None:
            return []

        self.model.eval()

        # 原始预测
        with torch.no_grad():
            orig_prob = self.model.predict(x, edge_index)[node_index].item()

        importances = []
        num_features = x.shape[1]

        if feature_names is None:
            feature_names = [f"feature_{i}" for i in range(num_features)]

        eps = 0.1  # 扰动幅度

        for i in range(num_features):
            # 扰动第 i 个特征
            x_perturbed = x.clone()
            x_perturbed[node_index, i] += eps

            with torch.no_grad():
                new_prob = self.model.predict(x_perturbed, edge_index)[node_index].item()

            # 重要性 = 预测变化的绝对值
            importance = abs(new_prob - orig_prob)
            importances.append({
                "feature_index": i,
                "feature_name": feature_names[i] if i < len(feature_names) else f"feature_{i}",
                "importance": round(float(importance), 6),
                "original_value": round(float(x[node_index, i].item()), 4),
                "direction": "increase" if new_prob > orig_prob else "decrease",
            })

        # 按重要性排序
        importances.sort(key=lambda x: x["importance"], reverse=True)

        # 归一化重要性（方便理解）
        total = sum(i["importance"] for i in importances)
        if total > 0:
            for i in importances:
                i["importance_ratio"] = round(i["importance"] / total, 4)
        else:
            for i in importances:
                i["importance_ratio"] = 0.0

        return importances

    # ============================================================
    # 3. 邻居节点重要性（基于节点删除）
    # ============================================================
    def neighbor_importance(self, x: torch.Tensor,
                            edge_index: torch.Tensor,
                            node_index: int,
                            top_k: int = 5) -> List[dict]:
        """
        邻居节点重要性（删除法）

        依次删除每个邻居节点，观察预测变化，
        变化越大说明该邻居越重要。

        M1: 基于真实计算

        Args:
            x: 节点特征 [N, F]
            edge_index: 边索引 [2, E]
            node_index: 目标节点
            top_k: 返回前 K 个最重要的邻居

        Returns:
            按重要性排序的邻居列表
        """
        if self.model is None:
            return []

        self.model.eval()

        # 原始预测
        with torch.no_grad():
            orig_prob = self.model.predict(x, edge_index)[node_index].item()

        # 找出所有邻居节点
        edge_idx_np = edge_index.cpu().numpy()
        neighbors = set()

        # 入边邻居
        mask_in = edge_idx_np[1] == node_index
        for n in edge_idx_np[0][mask_in]:
            neighbors.add(int(n))

        # 出边邻居
        mask_out = edge_idx_np[0] == node_index
        for n in edge_idx_np[1][mask_out]:
            neighbors.add(int(n))

        if not neighbors:
            return []

        importances = []

        for neighbor_idx in neighbors:
            # 删除该邻居（将其特征置零，等效于从图中移除）
            x_modified = x.clone()
            x_modified[neighbor_idx] = 0.0

            with torch.no_grad():
                new_prob = self.model.predict(x_modified, edge_index)[node_index].item()

            importance = abs(new_prob - orig_prob)
            importances.append({
                "node_index": neighbor_idx,
                "importance": round(float(importance), 6),
                "original_prob": round(orig_prob, 4),
                "new_prob": round(new_prob, 4),
                "impact": "increase" if new_prob > orig_prob else "decrease",
            })

        # 按重要性排序
        importances.sort(key=lambda x: x["importance"], reverse=True)

        # 归一化
        total = sum(i["importance"] for i in importances)
        if total > 0:
            for i in importances:
                i["importance_ratio"] = round(i["importance"] / total, 4)
        else:
            for i in importances:
                i["importance_ratio"] = 0.0

        return importances[:top_k]

    # ============================================================
    # 4. 综合解释（生成人类可读的解释）
    # ============================================================
    def explain_prediction(self, x: torch.Tensor,
                           edge_index: torch.Tensor,
                           node_index: int,
                           attention_data: dict = None,
                           feature_names: List[str] = None,
                           account_names: Dict[int, str] = None) -> dict:
        """
        综合解释一个节点的预测结果

        M2: 返回完整的证据链

        Args:
            x: 节点特征
            edge_index: 边索引
            node_index: 目标节点
            attention_data: GAT 注意力数据（可选）
            feature_names: 特征名称
            account_names: 账户ID到名称的映射

        Returns:
            {
                "node_index": 节点索引,
                "predicted_probability": 预测概率,
                "risk_level": 风险等级,
                "top_neighbors": 最重要的邻居,
                "top_features": 最重要的特征,
                "explanation_text": 自然语言解释,
            }
        """
        if self.model is None:
            return {"error": "无可用模型"}

        # 获取预测概率
        with torch.no_grad():
            prob = self.model.predict(x, edge_index)[node_index].item()

        # 风险等级
        if prob >= 0.7:
            risk_level = "high"
        elif prob >= 0.4:
            risk_level = "medium"
        else:
            risk_level = "low"

        # 特征重要性
        feat_imp = self.feature_importance(x, edge_index, node_index, feature_names)
        top_features = feat_imp[:3]  # 前3个最重要的特征

        # 邻居重要性
        if attention_data:
            attn_result = self.explain_with_attention(attention_data, node_index, top_k=5)
            top_neighbors = attn_result.get("top_neighbors", [])
        else:
            neighbor_imp = self.neighbor_importance(x, edge_index, node_index, top_k=5)
            top_neighbors = [
                {
                    "node_index": n["node_index"],
                    "importance_score": n["importance_ratio"],
                    "impact": n["impact"],
                }
                for n in neighbor_imp
            ]

        # 生成自然语言解释
        explanation_parts = []

        # 风险说明
        explanation_parts.append(
            f"该账户预测可疑概率为 {prob*100:.1f}%，风险等级: {risk_level}"
        )

        # 特征解释
        if top_features:
            feat_names = [f["feature_name"] for f in top_features]
            explanation_parts.append(
                f"主要影响特征: {', '.join(feat_names)}"
            )

        # 邻居解释
        if top_neighbors:
            neighbor_count = len(top_neighbors)
            explanation_parts.append(
                f"与 {neighbor_count} 个高关联度邻居账户有关"
            )

        # 加上账户名称
        if account_names and node_index in account_names:
            explanation_parts.append(
                f"账户: {account_names[node_index]}"
            )

        explanation_text = "。".join(explanation_parts) + "。"

        return {
            "node_index": node_index,
            "predicted_probability": round(prob, 4),
            "risk_level": risk_level,
            "top_neighbors": top_neighbors,
            "top_features": top_features,
            "explanation_text": explanation_text,
        }


# 预设的反洗钱节点特征名称
AML_FEATURE_NAMES = [
    "入度",
    "出度",
    "总入金金额",
    "总出金金额",
    "交易频次",
    "规则引擎风险分",
]
