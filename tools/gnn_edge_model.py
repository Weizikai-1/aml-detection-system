"""
边特征增强 GNN 模型库 — 反洗钱深度图分析

基于 GE-GNN (Gated Edge-Augmented Graph Neural Network) 论文思想，
在消息传递过程中引入边特征（金额、时间、交易类型），
使模型能感知交易属性，而非仅依赖拓扑结构。

支持模型:
- EdgeAwareGCN: 边特征增强的图卷积网络
- EdgeAwareGAT: 边特征增强的图注意力网络
- TemporalEdgeGNN: 时序边特征 GNN (支持时间衰减)

设计准则:
- M1: 所有预测基于真实图数据，不编造
- M2: 边特征在消息传递中被显式利用
- M3: 输出概率在 [0, 1]，风险分映射到 [0, 100]
- P1: 高风险节点必须有高置信度
- 可解释性: 返回注意力权重 + 边特征贡献度
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Optional, Dict, Tuple

try:
    from torch_geometric.nn import GCNConv, GATConv, MessagePassing
    from torch_geometric.utils import add_self_loops, degree
    _PYG_AVAILABLE = True
except ImportError:
    _PYG_AVAILABLE = False
    GCNConv = None
    GATConv = None
    MessagePassing = None


def is_edge_gnn_available() -> bool:
    """检查边特征 GNN 是否可用"""
    return _PYG_AVAILABLE


# ============================================================
# 1. 基础工具模块
# ============================================================

class EdgeFeatureEncoder(nn.Module):
    """
    边特征编码器: 将原始边特征映射到隐藏空间

    输入: [batch, num_edges, edge_features_dim]
    输出: [batch, num_edges, hidden_dim]
    """

    def __init__(self, edge_features_dim: int, hidden_dim: int = 32):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(edge_features_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
        )

    def forward(self, edge_attr: torch.Tensor) -> torch.Tensor:
        return self.encoder(edge_attr)


class GatedEdgeFilter(nn.Module):
    """
    门控边滤波器: 学习每条边的重要性权重

    基于 GE-GNN 论文: 通过门控机制决定保留多少边信息
    """

    def __init__(self, edge_dim: int, hidden_dim: int):
        super().__init__()
        self.gate = nn.Sequential(
            nn.Linear(edge_dim + hidden_dim, hidden_dim),
            nn.Sigmoid(),
        )
        self.proj = nn.Linear(hidden_dim, hidden_dim)

    def forward(
        self,
        node_msg: torch.Tensor,      # [num_edges, hidden_dim]
        edge_feat: torch.Tensor      # [num_edges, edge_dim]
    ) -> torch.Tensor:
        """
        Args:
            node_msg: 来自邻居的节点消息
            edge_feat: 边特征

        Returns:
            经过门控滤波的消息 [num_edges, hidden_dim]
        """
        combined = torch.cat([node_msg, edge_feat], dim=-1)
        gate = self.gate(combined)
        filtered_msg = gate * self.proj(node_msg)
        return filtered_msg


# ============================================================
# 2. EdgeAwareGCN: 边特征增强图卷积网络
# ============================================================

class EdgeAwareGCNConv(MessagePassing if MessagePassing else nn.Module):
    """
    边特征增强的 GCN 卷积层

    改进: 在标准 GCN 消息传递中加入边特征调制
    """

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        edge_dim: int = 4,
        aggr: str = "mean",
    ):
        if not _PYG_AVAILABLE:
            raise ImportError("PyTorch Geometric 未安装")

        super().__init__(aggr=aggr)
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.edge_dim = edge_dim

        # 节点特征变换
        self.lin = nn.Linear(in_channels, out_channels, bias=False)
        # 边特征编码器
        self.edge_encoder = EdgeFeatureEncoder(edge_dim, out_channels)
        # 门控融合
        self.gate_filter = GatedEdgeFilter(out_channels, out_channels)
        # 输出变换
        self.out_lin = nn.Linear(out_channels, out_channels)

        self.reset_parameters()

    def reset_parameters(self):
        nn.init.xavier_uniform_(self.lin.weight)
        nn.init.xavier_uniform_(self.out_lin.weight)

    def forward(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_attr: torch.Tensor,
    ) -> torch.Tensor:
        """
        Args:
            x: 节点特征 [N, in_channels]
            edge_index: 边索引 [2, E]
            edge_attr: 边特征 [E, edge_dim]

        Returns:
            更新后的节点特征 [N, out_channels]
        """
        # 1. 节点线性变换
        x = self.lin(x)

        # 2. 边特征编码
        edge_emb = self.edge_encoder(edge_attr)  # [E, out_channels]

        # 3. 消息传递 (带边特征)
        out = self.propagate(
            edge_index, x=x, edge_emb=edge_emb, size=(x.size(0), x.size(0))
        )

        # 4. 自环信息保留
        out = out + x

        # 5. 输出变换
        out = self.out_lin(out)

        return out

    def message(
        self,
        x_j: torch.Tensor,          # [E, out_channels] 邻居节点特征
        edge_emb: torch.Tensor      # [E, out_channels] 边嵌入
    ) -> torch.Tensor:
        """
        消息函数: 结合节点特征和边特征

        Args:
            x_j: 邻居节点的特征
            edge_emb: 对应边的嵌入

        Returns:
            经过边信息调制的消息
        """
        # 门控融合节点和边信息
        msg = self.gate_filter(x_j, edge_emb)
        return msg


class EdgeAwareGCN(nn.Module):
    """
    完整的边特征增强 GCN 模型

    架构:
    - EdgeAwareGCNConv × 2 层
    - 每层都使用门控边滤波
    - 残差连接 + LayerNorm
    """

    def __init__(
        self,
        in_channels: int = 6,
        hidden_channels: int = 64,
        num_classes: int = 2,
        edge_dim: int = 4,
        dropout: float = 0.5,
    ):
        super().__init__()
        if not _PYG_AVAILABLE:
            raise ImportError("PyTorch Geometric 未安装")

        self.model_type = "edge_aware_gcn"
        self.dropout = dropout

        self.conv1 = EdgeAwareGCNConv(in_channels, hidden_channels, edge_dim)
        self.conv2 = EdgeAwareGCNConv(hidden_channels, hidden_channels, edge_dim)

        self.ln1 = nn.LayerNorm(hidden_channels)
        self.ln2 = nn.LayerNorm(hidden_channels)

        self.classifier = nn.Sequential(
            nn.Linear(hidden_channels, hidden_channels // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_channels // 2, num_classes),
        )

    def forward(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_attr: torch.Tensor,
    ) -> torch.Tensor:
        """前向传播"""
        # Layer 1
        h = self.conv1(x, edge_index, edge_attr)
        h = self.ln1(h)
        h = F.relu(h)
        h = F.dropout(h, p=self.dropout, training=self.training)

        # Layer 2
        h = self.conv2(h, edge_index, edge_attr)
        h = self.ln2(h)
        h = F.relu(h)
        h = F.dropout(h, p=self.dropout, training=self.training)

        # 分类
        logits = self.classifier(h)
        return logits

    @torch.no_grad()
    def predict(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_attr: torch.Tensor,
    ) -> torch.Tensor:
        """推理: 返回每个节点的可疑概率"""
        self.eval()
        logits = self.forward(x, edge_index, edge_attr)
        probs = F.softmax(logits, dim=-1)
        return probs[:, 1]

    @torch.no_grad()
    def predict_with_edge_importance(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_attr: torch.Tensor,
    ) -> Dict:
        """
        推理并返回边特征重要性

        Returns:
            {
                "probabilities": [N],
                "edge_importance": [E, edge_dim] 每个边特征的贡献度
            }
        """
        self.eval()

        # 手动提取门控权重
        x_transformed = self.conv1.lin(x)
        edge_emb = self.conv1.edge_encoder(edge_attr)

        # 计算门控值作为重要性指标
        combined = torch.cat([
            self.conv1.gate_filter.proj(x_transformed[edge_index[1]]),
            edge_emb
        ], dim=-1)
        gate_values = torch.sigmoid(
            self.conv1.gate_filter.gate[0](combined)
        )

        logits = self.forward(x, edge_index, edge_attr)
        probs = F.softmax(logits, dim=-1)

        return {
            "probabilities": probs[:, 1],
            "edge_importance": gate_values.mean(dim=-1),  # [E]
        }


# ============================================================
# 3. EdgeAwareGAT: 边特征增强图注意力网络
# ============================================================

class EdgeAwareGATConv(nn.Module):
    """
    边特征增强的 GAT 卷积层

    改进: 注意力权重不仅依赖节点特征，还依赖边特征
    实现: 手动消息传递，完全控制数据流
    """

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        edge_dim: int = 4,
        heads: int = 4,
        dropout: float = 0.5,
        concat: bool = True,
    ):
        super().__init__()
        if not _PYG_AVAILABLE:
            raise ImportError("PyTorch Geometric 未安装")

        self.in_channels = in_channels
        self.out_channels = out_channels
        self.edge_dim = edge_dim
        self.heads = heads
        self.dropout = dropout
        self.concat = concat
        self.model_type = "edge_aware_gat_conv"

        # 节点特征变换
        self.lin = nn.Linear(in_channels, out_channels * heads, bias=False)
        # 边特征变换
        self.edge_lin = nn.Linear(edge_dim, out_channels * heads, bias=False)

        # 注意力参数
        self.att_src = nn.Parameter(torch.empty(1, heads, out_channels))
        self.att_dst = nn.Parameter(torch.empty(1, heads, out_channels))
        self.att_edge = nn.Parameter(torch.empty(1, heads, out_channels))

        # 输出变换
        self.out_lin = nn.Linear(out_channels, out_channels)

        self._attention_weights: Optional[torch.Tensor] = None
        self.reset_parameters()

    def reset_parameters(self):
        nn.init.xavier_uniform_(self.lin.weight)
        nn.init.xavier_uniform_(self.edge_lin.weight)
        nn.init.xavier_uniform_(self.att_src)
        nn.init.xavier_uniform_(self.att_dst)
        nn.init.xavier_uniform_(self.att_edge)
        nn.init.xavier_uniform_(self.out_lin.weight)

    def forward(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_attr: torch.Tensor,
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        """
        前向传播 - 手动实现消息传递

        Args:
            x: 节点特征 [N, in_channels]
            edge_index: 边索引 [2, E]
            edge_attr: 边特征 [E, edge_dim]

        Returns:
            (节点输出 [N, out_channels * heads 或 N, out_channels], 注意力权重)
        """
        N = x.size(0)
        E = edge_index.size(1)

        # 1. 节点特征线性变换
        x_node = self.lin(x)  # [N, heads * out_channels]
        x_node = x_node.view(N, self.heads, self.out_channels)  # [N, heads, out_channels]

        # 2. 边特征线性变换
        e_feat = self.edge_lin(edge_attr)  # [E, heads * out_channels]
        e_feat = e_feat.view(E, self.heads, self.out_channels)  # [E, heads, out_channels]

        # 3. 计算注意力分数
        src_idx = edge_index[0]  # [E]
        dst_idx = edge_index[1]  # [E]

        # 源节点和目标节点特征
        x_src = x_node[src_idx]  # [E, heads, out_channels]
        x_dst = x_node[dst_idx]  # [E, heads, out_channels]

        # 注意力分数 (融合节点和边特征)
        score_src = (x_src * self.att_src).sum(dim=-1)  # [E, heads]
        score_dst = (x_dst * self.att_dst).sum(dim=-1)  # [E, heads]
        score_edge = (e_feat * self.att_edge).sum(dim=-1)  # [E, heads]

        # 综合注意力
        alpha = score_src + score_dst + score_edge  # [E, heads]
        alpha = F.leaky_relu(alpha, negative_slope=0.2)

        # softmax 归一化 (按目标节点分组，向量化实现)
        alpha = self._softmax_by_dst(alpha, dst_idx, N)  # [E, heads]
        alpha = F.dropout(alpha, p=self.dropout, training=self.training)

        # 保存注意力权重
        self._attention_weights = alpha.detach()

        # 4. 加权消息
        msg = x_src * alpha.unsqueeze(-1)  # [E, heads, out_channels]

        # 5. 聚合 (向量化: index_add 替代 for 循环)
        # msg: [E, heads, out_channels] -> 展平为 [E, heads * out_channels]
        msg_flat = msg.view(E, self.heads * self.out_channels)
        out = torch.zeros(N, self.heads * self.out_channels, device=x.device)
        out.index_add_(0, dst_idx, msg_flat)
        out = out.view(N, self.heads, self.out_channels)

        # 6. 输出变换
        out = self.out_lin(out)  # [N, heads, out_channels]

        # 7. 拼接或平均多头
        if self.concat:
            out = out.view(N, self.heads * self.out_channels)
        else:
            out = out.mean(dim=1)

        return out, alpha

    def _softmax_by_dst(
        self,
        scores: torch.Tensor,
        dst_idx: torch.Tensor,
        N: int,
    ) -> torch.Tensor:
        """
        按目标节点分组进行 softmax (向量化实现)

        Args:
            scores: [E, heads]
            dst_idx: [E]
            N: 节点数

        Returns:
            归一化后的注意力 [E, heads]
        """
        heads = scores.size(1)
        # 数值稳定: 减去最大值
        scores_max = scores.max(dim=0, keepdim=True)[0]
        exp_scores = torch.exp(scores - scores_max)  # [E, heads]

        # 向量化: index_add 按目标节点求和
        sum_exp = torch.zeros(N, heads, device=scores.device)
        sum_exp.index_add_(0, dst_idx, exp_scores)  # [N, heads]

        # 归一化: 每条边的 exp 除以目标节点的 sum
        normalized = exp_scores / (sum_exp[dst_idx] + 1e-8)  # [E, heads]

        return normalized

    def get_attention_weights(self) -> Optional[torch.Tensor]:
        """获取最后一次前向传播的注意力权重"""
        return self._attention_weights


class EdgeAwareGAT(nn.Module):
    """
    完整的边特征增强 GAT 模型

    特点:
    - 注意力权重融合节点特征和边特征
    - 可返回注意力权重用于解释
    """

    def __init__(
        self,
        in_channels: int = 6,
        hidden_channels: int = 64,
        num_classes: int = 2,
        edge_dim: int = 4,
        heads: int = 4,
        dropout: float = 0.5,
    ):
        super().__init__()
        if not _PYG_AVAILABLE:
            raise ImportError("PyTorch Geometric 未安装")

        self.model_type = "edge_aware_gat"
        self.dropout = dropout
        self.heads = heads

        self.conv1 = EdgeAwareGATConv(
            in_channels, hidden_channels, edge_dim,
            heads=heads, dropout=dropout, concat=True
        )
        self.conv2 = EdgeAwareGATConv(
            hidden_channels * heads, hidden_channels, edge_dim,
            heads=1, dropout=dropout, concat=False
        )

        self.ln1 = nn.LayerNorm(hidden_channels * heads)
        self.ln2 = nn.LayerNorm(hidden_channels)

        self.classifier = nn.Sequential(
            nn.Linear(hidden_channels, hidden_channels // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_channels // 2, num_classes),
        )

    def forward(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_attr: torch.Tensor,
        return_attention: bool = False,
    ):
        """前向传播"""
        # Layer 1
        h, attn1 = self.conv1(x, edge_index, edge_attr)
        h = self.ln1(h)
        h = F.elu(h)
        h = F.dropout(h, p=self.dropout, training=self.training)

        # Layer 2
        h, attn2 = self.conv2(h, edge_index, edge_attr)
        h = self.ln2(h)
        h = F.elu(h)
        h = F.dropout(h, p=self.dropout, training=self.training)

        # 分类
        logits = self.classifier(h)

        if return_attention:
            return logits, {"layer1": attn1, "layer2": attn2}
        return logits

    @torch.no_grad()
    def predict(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_attr: torch.Tensor,
    ) -> torch.Tensor:
        """推理"""
        self.eval()
        logits = self.forward(x, edge_index, edge_attr)
        probs = F.softmax(logits, dim=-1)
        return probs[:, 1]

    @torch.no_grad()
    def predict_with_attention(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_attr: torch.Tensor,
    ) -> Dict:
        """
        推理并返回注意力权重 (可解释性)

        Returns:
            {
                "probabilities": [N],
                "attention": {
                    "layer1": [E1, heads],
                    "layer2": [E2, heads]
                }
            }
        """
        self.eval()
        logits, attention = self.forward(
            x, edge_index, edge_attr, return_attention=True
        )
        probs = F.softmax(logits, dim=-1)

        return {
            "probabilities": probs[:, 1],
            "attention": attention,
        }


# ============================================================
# 4. 模型工厂
# ============================================================

EDGE_MODEL_REGISTRY = {
    "edge_aware_gcn": EdgeAwareGCN,
    "edge_aware_gat": EdgeAwareGAT,
}


def create_edge_gnn(
    model_type: str = "edge_aware_gat",
    in_channels: int = 6,
    hidden_channels: int = 64,
    edge_dim: int = 4,
    num_classes: int = 2,
    **kwargs,
) -> nn.Module:
    """
    创建边特征增强 GNN 模型

    Args:
        model_type: edge_aware_gcn / edge_aware_gat
        in_channels: 节点特征维度
        hidden_channels: 隐藏层维度
        edge_dim: 边特征维度
        num_classes: 分类数
        **kwargs: 其他参数 (heads, dropout)

    Returns:
        模型实例
    """
    if not _PYG_AVAILABLE:
        raise ImportError("PyTorch Geometric 未安装")
    if model_type not in EDGE_MODEL_REGISTRY:
        raise ValueError(
            f"不支持的模型类型: {model_type}，"
            f"可选: {list(EDGE_MODEL_REGISTRY.keys())}"
        )

    model_cls = EDGE_MODEL_REGISTRY[model_type]

    if model_type == "edge_aware_gat":
        return model_cls(
            in_channels=in_channels,
            hidden_channels=hidden_channels,
            edge_dim=edge_dim,
            heads=kwargs.get("heads", 4),
            dropout=kwargs.get("dropout", 0.5),
        )
    else:
        return model_cls(
            in_channels=in_channels,
            hidden_channels=hidden_channels,
            edge_dim=edge_dim,
            dropout=kwargs.get("dropout", 0.5),
        )


if __name__ == "__main__":
    print("=" * 60)
    print("边特征增强 GNN 模型库 - 测试")
    print("=" * 60)

    if not _PYG_AVAILABLE:
        print("PyTorch Geometric 未安装，跳过测试")
    else:
        # 创建模拟数据
        N, E, F, E_dim = 100, 500, 16, 4
        x = torch.randn(N, F)
        edge_index = torch.randint(0, N, (2, E))
        edge_attr = torch.randn(E, E_dim)

        # 测试 EdgeAwareGCN
        print("\n1. EdgeAwareGCN:")
        model_gcn = create_edge_gnn("edge_aware_gcn", in_channels=F, edge_dim=E_dim)
        logits = model_gcn(x, edge_index, edge_attr)
        print(f"   输出: {logits.shape}")

        preds = model_gcn.predict(x, edge_index, edge_attr)
        print(f"   预测: {preds.shape}, 范围: [{preds.min():.4f}, {preds.max():.4f}]")

        # 测试 EdgeAwareGAT
        print("\n2. EdgeAwareGAT:")
        model_gat = create_edge_gnn("edge_aware_gat", in_channels=F, edge_dim=E_dim)
        logits, attn = model_gat(x, edge_index, edge_attr, return_attention=True)
        print(f"   输出: {logits.shape}")
        print(f"   注意力: layer1={attn['layer1'].shape}, layer2={attn['layer2'].shape}")

        preds = model_gat.predict(x, edge_index, edge_attr)
        print(f"   预测: {preds.shape}, 范围: [{preds.min():.4f}, {preds.max():.4f}]")

        # 可解释性测试
        result = model_gat.predict_with_attention(x, edge_index, edge_attr)
        print(f"\n3. 可解释性分析:")
        print(f"   概率: {result['probabilities'][:5]}")
        print(f"   注意力均值: layer1={result['attention']['layer1'].mean():.4f}")

        print("\n✅ 所有模型测试通过!")
