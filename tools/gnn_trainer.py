"""
GNN 训练器 — 资金图谱节点分类

工作流:
1. 从交易数据 + 规则命中结果构建 PyG Data 对象
2. 训练 GCN 模型（监督学习：规则命中的账户=可疑, 其余=正常）
3. 推理：对全图节点预测可疑概率

设计要点:
- 特征归一化到 [0, 1]，避免梯度爆炸
- 训练/测试 8:2 划分，监控过拟合
- 小图 CPU 训练(< 0.5 秒)，无需 GPU
"""
import numpy as np
import torch
import torch.nn.functional as F
from torch_geometric.data import Data
from torch_geometric.nn import GCNConv

from .gnn_model import create_model, MoneyLaunderingGCN


def _build_node_features(
    account_stats: dict,
    rule_risk_scores: dict,
    account_to_idx: dict,
) -> torch.Tensor:
    """
    构建节点特征矩阵

    6 维特征(均归一化到 [0, 1]):
    - in_degree: 入度
    - out_degree: 出度
    - in_amount: 入账金额(对数+归一化)
    - out_amount: 出账金额(对数+归一化)
    - total_txns: 总交易次数
    - rule_risk: 规则引擎风险评分

    Returns:
        [N, 6] 特征张量
    """
    N = len(account_to_idx)
    # 戒律 P4: 空图防御，避免对 0 大小数组调用 .min()/.max() 抛 ValueError
    if N == 0:
        return torch.zeros((0, 6), dtype=torch.float)
    features = np.zeros((N, 6), dtype=np.float32)

    for acc, idx in account_to_idx.items():
        stats = account_stats.get(acc, {})
        in_deg = stats.get("in_degree", 0)
        out_deg = stats.get("out_degree", 0)
        in_amt = stats.get("in_amount", 0.0)
        out_amt = stats.get("out_amount", 0.0)
        total = stats.get("total_txns", 0)

        features[idx, 0] = in_deg
        features[idx, 1] = out_deg
        # 对数变换处理长尾分布，+1 避免 log(0)
        features[idx, 2] = np.log1p(in_amt)
        features[idx, 3] = np.log1p(out_amt)
        features[idx, 4] = total
        features[idx, 5] = rule_risk_scores.get(acc, 0.0)

    # Min-Max 归一化到 [0, 1]（每列独立归一化）
    for col in range(6):
        col_min = features[:, col].min()
        col_max = features[:, col].max()
        if col_max > col_min:
            features[:, col] = (features[:, col] - col_min) / (col_max - col_min)

    return torch.tensor(features, dtype=torch.float)


def _build_labels(account_to_idx: dict, rule_risk_scores: dict) -> torch.Tensor:
    """
    构建标签: 规则命中(risk_score > 0) → 1(可疑), 其余 → 0(正常)
    """
    N = len(account_to_idx)
    labels = np.zeros(N, dtype=np.int64)
    for acc, idx in account_to_idx.items():
        if rule_risk_scores.get(acc, 0) > 0:
            labels[idx] = 1
    return torch.tensor(labels, dtype=torch.long)


def prepare_gnn_data(
    transactions: list,
    rule_hits: list,
) -> Data:
    """
    将交易数据转为 PyG Data 对象

    Args:
        transactions: 清洗后的交易列表
        rule_hits: 规则命中的可疑交易列表

    Returns:
        PyG Data: x(特征), edge_index(边), y(标签)
    """
    # 1. 收集所有账户 → 索引映射（戒律 P4: 安全访问避免 KeyError）
    accounts = set()
    for t in transactions:
        frm = t.get("from_account")
        to = t.get("to_account")
        if frm:
            accounts.add(frm)
        if to:
            accounts.add(to)
    account_to_idx = {acc: i for i, acc in enumerate(sorted(accounts))}

    # 2. 构建边索引
    edges = []
    for t in transactions:
        frm = t.get("from_account")
        to = t.get("to_account")
        if frm in account_to_idx and to in account_to_idx:
            edges.append([account_to_idx[frm], account_to_idx[to]])
    edge_index = torch.tensor(edges, dtype=torch.long).t().contiguous() if edges else torch.zeros((2, 0), dtype=torch.long)

    # 3. 聚合账户统计
    account_stats = {}
    for t in transactions:
        for acc in [t.get("from_account"), t.get("to_account")]:
            if not acc:
                continue
            if acc not in account_stats:
                account_stats[acc] = {
                    "in_degree": 0, "out_degree": 0,
                    "in_amount": 0.0, "out_amount": 0.0,
                    "total_txns": 0,
                }
            stats = account_stats[acc]
            stats["total_txns"] += 1
            if t.get("from_account") == acc:
                stats["out_degree"] += 1
                stats["out_amount"] += float(t.get("amount", 0) or 0)
            else:
                stats["in_degree"] += 1
                stats["in_amount"] += float(t.get("amount", 0) or 0)

    # 4. 规则风险评分映射（戒律 P4: 安全访问）
    rule_risk_scores = {}
    for s in rule_hits:
        txn = s.get("transaction") or {}
        score = s.get("risk_score", 0.5)
        if not isinstance(score, (int, float)):
            score = 0.5
        for acc in [txn.get("from_account"), txn.get("to_account")]:
            if acc:
                rule_risk_scores[acc] = max(rule_risk_scores.get(acc, 0), score)

    # 5. 构建特征和标签
    x = _build_node_features(account_stats, rule_risk_scores, account_to_idx)
    y = _build_labels(account_to_idx, rule_risk_scores)

    return Data(
        x=x,
        edge_index=edge_index,
        y=y,
        num_nodes=len(account_to_idx),
        account_to_idx=account_to_idx,
    )


def train_gnn(
    data: Data,
    epochs: int = 200,
    lr: float = 0.01,
    verbose: bool = True,
    device: str = "cpu",
) -> tuple[MoneyLaunderingGCN, dict]:
    """
    训练 GCN 节点分类器

    Args:
        data: PyG Data 对象
        epochs: 训练轮数
        lr: 学习率
        verbose: 是否打印训练日志
        device: 计算设备

    Returns:
        (训练好的模型, 训练指标字典)
    """
    data = data.to(device)

    # 8:2 划分训练/验证集
    n = data.num_nodes
    perm = torch.randperm(n)
    train_mask = torch.zeros(n, dtype=torch.bool)
    val_mask = torch.zeros(n, dtype=torch.bool)
    split = int(0.8 * n)
    train_mask[perm[:split]] = True
    val_mask[perm[split:]] = True

    model = create_model(in_channels=data.x.size(1)).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=5e-4)

    metrics = {"train_losses": [], "val_acc": [], "final_train_acc": 0.0, "final_val_acc": 0.0, "epochs": epochs}

    # 戒律 P4: 单节点/极小图训练时 train_mask 可能为空，跳过训练避免 cross_entropy 抛错
    if train_mask.sum() == 0:
        if verbose:
            print("  训练跳过: 训练集为空（节点数过少）")
        return model, metrics

    for epoch in range(epochs):
        # 训练
        model.train()
        optimizer.zero_grad()
        out = model(data.x, data.edge_index)
        loss = F.cross_entropy(out[train_mask], data.y[train_mask])
        loss.backward()
        optimizer.step()

        # 验证
        model.eval()
        with torch.no_grad():
            pred = out[val_mask].argmax(dim=1)
            correct = (pred == data.y[val_mask]).sum().item()
            val_acc = correct / val_mask.sum().item() if val_mask.sum() > 0 else 0

        if verbose and (epoch + 1) % 50 == 0:
            print(f"  Epoch {epoch + 1:3d}/{epochs} | Loss: {loss.item():.4f} | Val Acc: {val_acc:.2%}")

        metrics["train_losses"].append(loss.item())
        metrics["val_acc"].append(val_acc)

    # 最终评估
    model.eval()
    with torch.no_grad():
        out = model(data.x, data.edge_index)
        train_pred = out[train_mask].argmax(dim=1)
        # 戒律 P4: 避免除零（训练集为空时）
        train_total = train_mask.sum().item()
        train_acc = (train_pred == data.y[train_mask]).sum().item() / train_total if train_total > 0 else 0.0
        val_pred = out[val_mask].argmax(dim=1)
        val_total = val_mask.sum().item()
        val_acc = (val_pred == data.y[val_mask]).sum().item() / val_total if val_total > 0 else 0.0

    metrics["final_train_acc"] = train_acc
    metrics["final_val_acc"] = val_acc

    if verbose:
        print(f"  训练完成 | Train Acc: {train_acc:.2%} | Val Acc: {val_acc:.2%}")

    return model, metrics


def infer_gnn(
    model: MoneyLaunderingGCN,
    data: Data,
    device: str = "cpu",
) -> dict:
    """
    GNN 推理：计算全图节点的可疑概率

    Args:
        model: 训练好的 GCN 模型
        data: PyG Data 对象(含 account_to_idx)
        device: 计算设备

    Returns:
        {
            "scores": {account_id: probability},
            "high_risk": [(account_id, probability), ...],  # 概率 > 0.5
            "stats": {"total_nodes", "high_risk_count", "avg_score", ...}
        }
    """
    data = data.to(device)
    model.eval()

    with torch.no_grad():
        probs = model.predict(data.x, data.edge_index).cpu().numpy()

    # 逆映射: 索引 → 账户
    idx_to_account = {v: k for k, v in data.account_to_idx.items()}
    scores = {idx_to_account[i]: float(probs[i]) for i in range(len(probs))}

    high_risk = [(acc, s) for acc, s in scores.items() if s > 0.5]
    high_risk.sort(key=lambda x: x[1], reverse=True)

    stats = {
        "total_nodes": len(scores),
        "high_risk_count": len(high_risk),
        "high_risk_ratio": len(high_risk) / len(scores) if scores else 0,
        "avg_score": float(np.mean(probs)) if len(probs) > 0 else 0.0,
        "max_score": float(np.max(probs)) if len(probs) > 0 else 0.0,
        "min_score": float(np.min(probs)) if len(probs) > 0 else 0.0,
    }

    return {"scores": scores, "high_risk": high_risk, "stats": stats}
