"""
GNN 图神经网络模型 — 反洗钱节点分类
支持 GAT / GraphSAGE / GCN 三种架构，延迟导入避免硬依赖

设计: 不在模块级判断依赖可用性，而是在每个函数入口处延迟导入。
      这样无需 if/else stub 块，代码无重复，调用时的 ImportError 自带说明。
"""
import logging
import numpy as np
import pandas as pd

log = logging.getLogger("aml.gnn")

_IMPORT_ERR = "需要安装 PyTorch Geometric: pip install torch torch-geometric"


def is_available() -> bool:
    """检测 torch + torch-geometric 是否可用"""
    try:
        import torch
        import torch_geometric
        return True
    except ImportError:
        return False


def _require_torch():
    """延迟导入 torch，不可用时抛出清晰的 ImportError"""
    try:
        import torch
        import torch.nn.functional as F
        from torch_geometric.nn import GATConv, SAGEConv, GCNConv
        from torch_geometric.data import Data
        return torch, F, GATConv, SAGEConv, GCNConv, Data
    except ImportError as e:
        raise ImportError(f"{_IMPORT_ERR}\n原始错误: {e}")


# ============================================================
# FraudGNN — 图神经网络模型
# ============================================================

class FraudGNN:
    """
    图神经网络 — GAT / GraphSAGE / GCN 三架构

    使用方式:
        model = FraudGNN(in_dim=8, model_type="gat")   # 默认 GAT
        model = FraudGNN(in_dim=8, model_type="sage")  # GraphSAGE
        model = FraudGNN(in_dim=8, model_type="gcn")   # GCN
    """

    def __init__(self, in_dim=8, hidden=64, dropout=0.5, heads=4,
                 model_type="gat"):
        torch, F, GATConv, SAGEConv, GCNConv, _Data = _require_torch()
        self._torch = torch
        self._F = F
        self.model_type = model_type

        if model_type == "sage":
            self.conv1 = SAGEConv(in_dim, hidden)
            self.conv2 = SAGEConv(hidden, hidden)
        elif model_type == "gcn":
            self.conv1 = GCNConv(in_dim, hidden)
            self.conv2 = GCNConv(hidden, hidden)
        else:  # gat (default)
            self.conv1 = GATConv(in_dim, hidden, heads=heads, dropout=dropout)
            self.conv2 = GATConv(hidden * heads, hidden, heads=1,
                                 concat=False, dropout=dropout)
        self.lin = torch.nn.Linear(hidden, 1)
        self.dropout = dropout

    def forward(self, data):
        F = self._F
        x, edge_index = data.x, data.edge_index
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = F.elu(self.conv1(x, edge_index))
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = self.conv2(x, edge_index)
        return self.lin(x)

    def parameters(self):
        """收集所有子模块参数"""
        params = list(self.lin.parameters())
        for name in ['conv1', 'conv2']:
            if hasattr(self, name):
                params.extend(getattr(self, name).parameters())
        return params

    def state_dict(self):
        return {k: v for module in [self.conv1, self.conv2, self.lin]
                for k, v in module.state_dict().items()}

    def load_state_dict(self, sd):
        self.conv1.load_state_dict({k: v for k, v in sd.items() if k.startswith('conv1')})
        self.conv2.load_state_dict({k: v for k, v in sd.items() if k.startswith('conv2')})
        self.lin.load_state_dict({k: v for k, v in sd.items() if k.startswith('lin')})

    def train(self, mode=True):
        torch, *_ = _require_torch()
        for m in [self.conv1, self.conv2, self.lin]:
            m.train(mode)
        return self

    def eval(self):
        return self.train(False)

    @property
    def training(self):
        return self.conv1.training


# ============================================================
# 图构建 + 训练 + 预测
# ============================================================

def build_graph(df: pd.DataFrame):
    """从交易 DataFrame 构建 PyG Data 对象"""
    torch, _F, _GAT, _SAGE, _GCN, Data = _require_torch()

    accounts = set(df["nameOrig"].astype(str)) | set(df["nameDest"].astype(str))
    id2idx = {aid: i for i, aid in enumerate(sorted(accounts))}
    n_nodes = len(id2idx)

    edge_index = [[], []]
    edge_attr = []
    for _, row in df.iterrows():
        src = id2idx[str(row["nameOrig"])]
        dst = id2idx[str(row["nameDest"])]
        edge_index[0].append(src)
        edge_index[1].append(dst)
        edge_attr.append([float(row.get("amount", 0)), float(row.get("step", 0))])

    edge_index = torch.tensor(edge_index, dtype=torch.long)
    edge_attr = torch.tensor(edge_attr, dtype=torch.float)

    # 8维节点特征
    features = np.zeros((n_nodes, 8), dtype=np.float32)
    in_deg, out_deg = np.zeros(n_nodes), np.zeros(n_nodes)
    in_amt, out_amt = np.zeros(n_nodes), np.zeros(n_nodes)
    n_txns = np.zeros(n_nodes)

    for _, row in df.iterrows():
        s, d = id2idx[str(row["nameOrig"])], id2idx[str(row["nameDest"])]
        amt = float(row.get("amount", 0))
        out_deg[s] += 1; in_deg[d] += 1
        out_amt[s] += amt; in_amt[d] += amt
        n_txns[s] += 1; n_txns[d] += 1

    features[:, 0] = np.log1p(out_deg)
    features[:, 1] = np.log1p(in_deg)
    features[:, 2] = np.log1p(out_amt)
    features[:, 3] = np.log1p(in_amt)
    features[:, 4] = np.log1p(n_txns)
    features[:, 5] = np.where(in_deg + out_deg > 0, np.log1p(out_amt / (in_amt + 1)), 0)
    features[:, 6] = 0
    features[:, 7] = np.where(n_txns > 1, in_deg / (out_deg + 1), 0)

    # 节点标签
    labels = np.zeros(n_nodes, dtype=np.int64)
    fraud_txns = df[df["isFraud"] == 1]
    for _, row in fraud_txns.iterrows():
        s, d = str(row["nameOrig"]), str(row["nameDest"])
        if s in id2idx: labels[id2idx[s]] = 1
        if d in id2idx: labels[id2idx[d]] = 1

    # 欺诈邻居特征
    fraud_set = set(np.where(labels == 1)[0])
    in_nb, out_nb = [[] for _ in range(n_nodes)], [[] for _ in range(n_nodes)]
    for s, d in zip(edge_index[0].tolist(), edge_index[1].tolist()):
        out_nb[s].append(d); in_nb[d].append(s)
    for i in range(n_nodes):
        nb = set(out_nb[i]) | set(in_nb[i])
        features[i, 6] = len(nb & fraud_set) / max(len(nb), 1)

    x = torch.tensor(features, dtype=torch.float)
    y = torch.tensor(labels, dtype=torch.long)

    n_train = int(n_nodes * 0.7)
    perm = torch.randperm(n_nodes)
    train_mask = torch.zeros(n_nodes, dtype=torch.bool)
    test_mask = torch.zeros(n_nodes, dtype=torch.bool)
    train_mask[perm[:n_train]] = True
    test_mask[perm[n_train:]] = True

    return Data(x=x, edge_index=edge_index, edge_attr=edge_attr, y=y,
                train_mask=train_mask, test_mask=test_mask, id2idx=id2idx)


def train_and_eval(data, epochs=100, lr=0.01, model_type="gat") -> dict:
    """训练 GNN 模型并返回评估指标"""
    torch, _F, _GAT, _SAGE, _GCN, _Data = _require_torch()

    model = FraudGNN(in_dim=data.x.size(1), model_type=model_type)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=5e-4)
    pos_weight = ((data.y[data.train_mask] == 0).sum().float()
                  / max(data.y[data.train_mask].sum(), 1))
    criterion = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)

    best_f1, best_state = 0.0, None
    final = {"f1": 0, "precision": 0, "recall": 0}

    for epoch in range(epochs):
        model.train()
        optimizer.zero_grad()
        out = model.forward(data).squeeze(-1)
        loss = criterion(out[data.train_mask], data.y[data.train_mask].float())
        loss.backward()
        optimizer.step()

        model.eval()
        with torch.no_grad():
            out = model.forward(data).squeeze(-1)
            prob = torch.sigmoid(out[data.test_mask])
            metrics = _calc_node_metrics(prob, data.y[data.test_mask])
            if metrics["f1"] > best_f1:
                best_f1 = metrics["f1"]
                best_state = {k: v.clone() for k, v in model.state_dict().items()}
            if epoch % 20 == 0:
                log.info(f"Epoch {epoch:3d}: loss={loss.item():.4f}, "
                         f"F1={metrics['f1']:.4f}, P={metrics['precision']:.4f}, "
                         f"R={metrics['recall']:.4f}")

    if best_state:
        model.load_state_dict(best_state)
        model.eval()
        with torch.no_grad():
            prob = torch.sigmoid(model.forward(data).squeeze(-1))
            final = _calc_node_metrics(prob[data.test_mask], data.y[data.test_mask])

    return {"node_f1": final["f1"], "node_precision": final["precision"],
            "node_recall": final["recall"], "best_f1": best_f1, "model": model}


def _calc_node_metrics(prob, labels, threshold=0.5) -> dict:
    pred = (prob > threshold).long()
    tp = (pred & labels).sum().item()
    fp = (pred & (labels == 0)).sum().item()
    fn = ((pred == 0) & labels).sum().item()
    tn = ((pred == 0) & (labels == 0)).sum().item()
    p = tp / (tp + fp) if (tp + fp) > 0 else 0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0
    f = 2 * p * r / (p + r) if (p + r) > 0 else 0
    return {"precision": p, "recall": r, "f1": f, "tp": tp, "fp": fp, "tn": tn, "fn": fn}


def predict_transactions(model, data, df: pd.DataFrame) -> np.ndarray:
    """将节点预测映射回交易级别"""
    torch, _F, _GAT, _SAGE, _GCN, _Data = _require_torch()
    model.eval()
    with torch.no_grad():
        prob = torch.sigmoid(model.forward(data).squeeze()).numpy()
    id2idx = data.id2idx
    preds = np.zeros(len(df), dtype=int)
    for i, (_, row) in enumerate(df.iterrows()):
        s = id2idx.get(str(row["nameOrig"]))
        d = id2idx.get(str(row["nameDest"]))
        ps = prob[s] if s is not None else 0
        pd_ = prob[d] if d is not None else 0
        preds[i] = 1 if (ps > 0.5 or pd_ > 0.5) else 0
    return preds
