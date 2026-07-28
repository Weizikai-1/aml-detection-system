"""
GCN 图卷积网络模型 — 资金图谱节点分类

架构: 2层 GCN → 二分类(正常/可疑)
用途: 自动学习洗钱模式，识别规则引擎漏掉的隐蔽可疑账户
"""
import torch
import torch.nn.functional as F
from torch_geometric.nn import GCNConv


class MoneyLaunderingGCN(torch.nn.Module):
    """
    反洗钱 GCN 节点分类器

    输入: 图结构(边) + 节点特征(出入金额/度/次数/规则风险分)
    输出: 每个账户的可疑概率(0-1)

    两层 GCN:
    - 第1层: 聚合邻居信息，从6维特征 → 64维隐藏表示
    - 第2层: 基于融合后的表示 → 2分类输出
    """

    def __init__(self, in_channels: int = 6, hidden_channels: int = 64, num_classes: int = 2, dropout: float = 0.5):
        super().__init__()
        self.conv1 = GCNConv(in_channels, hidden_channels)
        self.conv2 = GCNConv(hidden_channels, num_classes)
        self.dropout = dropout

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        """
        前向传播

        Args:
            x: 节点特征矩阵 [N, in_channels]
            edge_index: 边索引 [2, E]

        Returns:
            logits [N, num_classes], 第1列=正常分数, 第2列=可疑分数
        """
        x = self.conv1(x, edge_index)
        x = F.relu(x)
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = self.conv2(x, edge_index)
        return x

    @torch.no_grad()
    def predict(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        """
        推理模式：返回每个节点的可疑概率 [0, 1]

        Args:
            x: 节点特征矩阵 [N, 6]
            edge_index: 边索引 [2, E]

        Returns:
            可疑概率 [N], 值域 [0, 1]
        """
        self.eval()
        logits = self.forward(x, edge_index)
        probs = F.softmax(logits, dim=-1)
        return probs[:, 1]  # 只返回"可疑"类的概率


def create_model(in_channels: int = 6) -> MoneyLaunderingGCN:
    """工厂函数：创建默认配置的 GCN 模型"""
    return MoneyLaunderingGCN(
        in_channels=in_channels,
        hidden_channels=64,
        num_classes=2,
        dropout=0.5,
    )
