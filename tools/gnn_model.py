"""
GNN 图神经网络模型库 — 反洗钱资金图谱分析

支持模型:
- GCN: 图卷积网络（基础款）
- GAT: 图注意力网络（可解释性好，有注意力权重）
- GraphSAGE: 图采样与聚合（适合大规模图）

设计准则:
- M1: 所有预测基于真实图数据，不编造
- M2: GAT 提供注意力权重，可解释哪些邻居重要
- M3: 输出概率在 [0, 1]，风险分映射到 [0, 100]
- P1: 高风险节点必须有高置信度
- 降级机制: PyG 不可用时用纯 numpy 实现降级
"""
import torch
import torch.nn.functional as F
from typing import Optional, Dict, Any

# 尝试导入 PyG，失败则标记不可用
try:
    from torch_geometric.nn import GCNConv, GATConv, SAGEConv
    _PYG_AVAILABLE = True
except ImportError:
    _PYG_AVAILABLE = False
    # 降级时的占位符
    GCNConv = None
    GATConv = None
    SAGEConv = None


def is_pyg_available() -> bool:
    """检查 PyTorch Geometric 是否可用"""
    return _PYG_AVAILABLE


# ============================================================
# 1. GCN 模型（基础款）
# ============================================================
class MoneyLaunderingGCN(torch.nn.Module):
    """
    反洗钱 GCN 节点分类器

    两层 GCN:
    - 第1层: 聚合邻居信息，in_channels → hidden_channels
    - 第2层: 基于融合后的表示 → 2分类输出
    """

    def __init__(self, in_channels: int = 6, hidden_channels: int = 64,
                 num_classes: int = 2, dropout: float = 0.5):
        super().__init__()
        if not _PYG_AVAILABLE:
            raise ImportError("PyTorch Geometric 未安装，无法使用 GCN 模型")
        self.conv1 = GCNConv(in_channels, hidden_channels)
        self.conv2 = GCNConv(hidden_channels, num_classes)
        self.dropout = dropout
        self.model_type = "gcn"

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        """前向传播，返回 logits [N, num_classes]"""
        x = self.conv1(x, edge_index)
        x = F.relu(x)
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = self.conv2(x, edge_index)
        return x

    @torch.no_grad()
    def predict(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        """推理模式：返回每个节点的可疑概率 [N]"""
        self.eval()
        logits = self.forward(x, edge_index)
        probs = F.softmax(logits, dim=-1)
        return probs[:, 1]


# ============================================================
# 2. GAT 模型（图注意力网络 — 可解释性好）
# ============================================================
class MoneyLaunderingGAT(torch.nn.Module):
    """
    反洗钱 GAT 节点分类器

    特点:
    - 多头注意力机制，自动学习不同邻居的重要性
    - 可返回注意力权重，用于解释预测结果（M2 证据完整）

    两层 GAT:
    - 第1层: 多头注意力，in_channels → hidden_channels
    - 第2层: 平均注意力，hidden_channels → num_classes
    """

    def __init__(self, in_channels: int = 6, hidden_channels: int = 64,
                 num_classes: int = 2, heads: int = 4, dropout: float = 0.5):
        super().__init__()
        if not _PYG_AVAILABLE:
            raise ImportError("PyTorch Geometric 未安装，无法使用 GAT 模型")

        self.heads = heads
        self.dropout = dropout
        self.model_type = "gat"

        # 第一层：多头注意力
        self.conv1 = GATConv(
            in_channels,
            hidden_channels,
            heads=heads,
            dropout=dropout,
            concat=True,  # 多头输出拼接
        )

        # 第二层：平均注意力
        self.conv2 = GATConv(
            hidden_channels * heads,
            num_classes,
            heads=1,
            dropout=dropout,
            concat=False,  # 最后一层求平均
        )

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor,
                return_attention_weights: bool = False):
        """
        前向传播

        Args:
            x: 节点特征 [N, in_channels]
            edge_index: 边索引 [2, E]
            return_attention_weights: 是否返回注意力权重（用于可解释性）

        Returns:
            logits [N, num_classes] 或 (logits, attention_weights)
        """
        if return_attention_weights:
            x, (edge_idx1, attn1) = self.conv1(
                x, edge_index, return_attention_weights=True
            )
            x = F.elu(x)
            x = F.dropout(x, p=self.dropout, training=self.training)

            x, (edge_idx2, attn2) = self.conv2(
                x, edge_index, return_attention_weights=True
            )
            return x, {
                "layer1": {"edge_index": edge_idx1, "attention": attn1},
                "layer2": {"edge_index": edge_idx2, "attention": attn2},
            }
        else:
            x = self.conv1(x, edge_index)
            x = F.elu(x)
            x = F.dropout(x, p=self.dropout, training=self.training)
            x = self.conv2(x, edge_index)
            return x

    @torch.no_grad()
    def predict(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        """推理模式：返回每个节点的可疑概率 [N]"""
        self.eval()
        logits = self.forward(x, edge_index)
        probs = F.softmax(logits, dim=-1)
        return probs[:, 1]

    @torch.no_grad()
    def predict_with_attention(self, x: torch.Tensor, edge_index: torch.Tensor) -> dict:
        """
        推理并返回注意力权重（M2 可解释性）

        Returns:
            {
                "probabilities": Tensor [N], 可疑概率
                "attention": {
                    "layer1": {edge_index, attention},
                    "layer2": {edge_index, attention},
                }
            }
        """
        self.eval()
        logits, attn = self.forward(x, edge_index, return_attention_weights=True)
        probs = F.softmax(logits, dim=-1)
        return {
            "probabilities": probs[:, 1],
            "attention": attn,
        }


# ============================================================
# 3. GraphSAGE 模型（适合大规模图）
# ============================================================
class MoneyLaunderingSAGE(torch.nn.Module):
    """
    反洗钱 GraphSAGE 节点分类器

    特点:
    - 基于采样的邻居聚合，适合大规模图
    - 支持多种聚合函数（mean/max/lstm）

    两层 SAGE:
    - 第1层: in_channels → hidden_channels
    - 第2层: hidden_channels → num_classes
    """

    def __init__(self, in_channels: int = 6, hidden_channels: int = 64,
                 num_classes: int = 2, dropout: float = 0.5,
                 aggr: str = "mean"):
        super().__init__()
        if not _PYG_AVAILABLE:
            raise ImportError("PyTorch Geometric 未安装，无法使用 GraphSAGE 模型")

        self.dropout = dropout
        self.model_type = "graphsage"

        self.conv1 = SAGEConv(in_channels, hidden_channels, aggr=aggr)
        self.conv2 = SAGEConv(hidden_channels, num_classes, aggr=aggr)

        # 线性变换用于残差连接
        self.lin = torch.nn.Linear(in_channels, num_classes)

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        """前向传播（带残差连接）"""
        # 残差路径
        residual = self.lin(x)

        # 主路径
        h = self.conv1(x, edge_index)
        h = F.relu(h)
        h = F.dropout(h, p=self.dropout, training=self.training)
        h = self.conv2(h, edge_index)

        # 残差相加
        return h + residual

    @torch.no_grad()
    def predict(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        """推理模式：返回每个节点的可疑概率 [N]"""
        self.eval()
        logits = self.forward(x, edge_index)
        probs = F.softmax(logits, dim=-1)
        return probs[:, 1]


# ============================================================
# 4. 模型工厂：统一创建接口
# ============================================================
MODEL_REGISTRY = {
    "gcn": MoneyLaunderingGCN,
    "gat": MoneyLaunderingGAT,
    "graphsage": MoneyLaunderingSAGE,
}


def create_model(model_type: str = "gcn", in_channels: int = 6,
                 hidden_channels: int = 64, **kwargs) -> torch.nn.Module:
    """
    创建 GNN 模型

    Args:
        model_type: gcn / gat / graphsage
        in_channels: 输入特征维度
        hidden_channels: 隐藏层维度
        **kwargs: 各模型特有的参数

    Returns:
        GNN 模型实例

    Raises:
        ValueError: 不支持的模型类型
        ImportError: PyG 未安装
    """
    if not _PYG_AVAILABLE:
        raise ImportError(
            "PyTorch Geometric 未安装。请运行: pip install torch-geometric\n"
            "或使用纯 numpy 降级版本的规则引擎"
        )

    if model_type not in MODEL_REGISTRY:
        raise ValueError(
            f"不支持的模型类型: {model_type}，"
            f"可选: {list(MODEL_REGISTRY.keys())}"
        )

    model_cls = MODEL_REGISTRY[model_type]

    if model_type == "gat":
        return model_cls(
            in_channels=in_channels,
            hidden_channels=hidden_channels,
            heads=kwargs.get("heads", 4),
            dropout=kwargs.get("dropout", 0.5),
        )
    elif model_type == "graphsage":
        return model_cls(
            in_channels=in_channels,
            hidden_channels=hidden_channels,
            dropout=kwargs.get("dropout", 0.5),
            aggr=kwargs.get("aggr", "mean"),
        )
    else:  # gcn
        return model_cls(
            in_channels=in_channels,
            hidden_channels=hidden_channels,
            dropout=kwargs.get("dropout", 0.5),
        )


def select_model_by_size(num_nodes: int, num_edges: int) -> str:
    """
    根据图规模自动选择合适的模型

    选型策略:
    - 小图(< 1000节点): GAT（注意力可解释性好）
    - 中图(1000-10000节点): GCN（平衡速度和效果）
    - 大图(> 10000节点): GraphSAGE（采样效率高）

    Args:
        num_nodes: 节点数
        num_edges: 边数

    Returns:
        推荐的模型类型
    """
    if num_nodes < 1000:
        return "gat"
    elif num_nodes < 10000:
        return "gcn"
    else:
        return "graphsage"
