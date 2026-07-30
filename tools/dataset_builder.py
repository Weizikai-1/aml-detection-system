"""
PaySim 数据集加载器 & 图构建器

功能:
- 加载 PaySim 数据集 (Kaggle: Financial Fraud Detection)
- 特征工程: 时间、金额、账户行为特征
- 构建异配图: 账户节点 + 交易边
- 转换为 PyTorch Geometric Data 对象

数据集说明:
PaySim 基于非洲某国移动货币服务的真实金融日志，
模拟了 30 天的交易活动，包含多种交易类型。

关键列:
- step: 时间步(1步=1小时)
- type: 交易类型 (CASH-IN/CASH-OUT/DEBIT/PAYMENT/TRANSFER)
- amount: 交易金额
- nameOrig/nameDest: 交易双方账户
- isFraud: 欺诈标签 (0/1)
- isFlaggedFraud: 大额标记
"""
import os
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Tuple, Optional

# PyG 依赖（戒律 M1: 不编造，不可用时明确标记）
try:
    import torch
    from torch_geometric.data import Data, HeteroData
    _PYG_AVAILABLE = True
except (ImportError, OSError):
    _PYG_AVAILABLE = False
    torch = None  # type: ignore
    Data = None   # type: ignore
    HeteroData = None  # type: ignore


# ============================================================
# 1. 数据集加载与预处理
# ============================================================

class PaySimDataset:
    """PaySim 反洗钱数据集加载器"""

    # 特征列定义
    NUMERIC_FEATURES = [
        "step", "amount", "oldbalanceOrg", "newbalanceOrg",
        "oldbalanceDest", "newbalanceDest", "isFlaggedFraud"
    ]

    # 交易类型映射
    TYPE_MAP = {
        "CASH-IN": 0,
        "CASH-OUT": 1,
        "DEBIT": 2,
        "PAYMENT": 3,
        "TRANSFER": 4,
    }

    def __init__(self, data_path: Optional[str] = None):
        """
        初始化数据集加载器

        Args:
            data_path: PaySim CSV 文件路径 (可从 Kaggle 下载)
        """
        self.data_path = data_path
        self.df: Optional[pd.DataFrame] = None
        self._is_loaded = False

    def load(self, n_rows: Optional[int] = None) -> pd.DataFrame:
        """
        加载数据集

        Args:
            n_rows: 加载前 N 行 (用于快速测试)

        Returns:
            DataFrame
        """
        if self.data_path and os.path.exists(self.data_path):
            print(f"加载 PaySim 数据集: {self.data_path}")
            if n_rows:
                self.df = pd.read_csv(self.data_path, nrows=n_rows)
            else:
                self.df = pd.read_csv(self.data_path)
            self._is_loaded = True
        else:
            print("未指定 PaySim 数据集路径，将生成模拟数据")
            self.df = self._generate_mock_data(n_rows=n_rows or 10000)
            self._is_loaded = True

        self._preprocess()
        return self.df

    def _generate_mock_data(self, n_rows: int = 10000) -> pd.DataFrame:
        """
        生成符合 PaySim 格式的模拟数据 (当真实数据不可用时)

        Args:
            n_rows: 生成行数

        Returns:
            模拟 DataFrame
        """
        np.random.seed(42)
        n_fraud = int(n_rows * 0.013)  # 约 1.3% 欺诈率，接近真实数据分布

        # 生成正常交易
        n_normal = n_rows - n_fraud
        steps_normal = np.random.randint(1, 745, n_normal)
        types_normal = np.random.choice(
            list(self.TYPE_MAP.keys()), n_normal,
            p=[0.35, 0.35, 0.05, 0.15, 0.10]  # 交易类型分布
        )
        amounts_normal = np.random.exponential(5000, n_normal)
        amounts_normal = np.clip(amounts_normal, 1, 1000000)

        # 生成欺诈交易 (大额 + 快速转移模式)
        steps_fraud = np.random.randint(1, 745, n_fraud)
        types_fraud = np.random.choice(["TRANSFER", "CASH-OUT"], n_fraud, p=[0.6, 0.4])
        amounts_fraud = np.random.uniform(200000, 10000000, n_fraud)

        # 合并
        steps = np.concatenate([steps_normal, steps_fraud])
        types = np.concatenate([types_normal, types_fraud])
        amounts = np.concatenate([amounts_normal, amounts_fraud])
        is_fraud = np.concatenate([np.zeros(n_normal), np.ones(n_fraud)])

        # 账户 ID (欺诈账户聚集在少数几个)
        all_accounts = [f"CUSTOMER_{i:07d}" for i in range(max(n_rows // 5, 500))]
        name_orig = np.random.choice(all_accounts, n_rows)

        # 欺诈交易使用不同的目标账户
        name_dest = np.random.choice(all_accounts, n_rows)
        fraud_indices = np.where(is_fraud == 1)[0]
        if len(fraud_indices) > 0:
            fraud_dest = np.random.choice(all_accounts[-50:], len(fraud_indices))
            name_dest[fraud_indices] = fraud_dest

        # 余额特征
        old_balance_org = np.random.exponential(10000, n_rows)
        new_balance_org = old_balance_org - amounts * (types != "CASH-IN") + amounts * (types == "CASH-IN")
        old_balance_dest = np.random.exponential(10000, n_rows)
        new_balance_dest = old_balance_dest + amounts * (types == "CASH-IN") - amounts * (types == "CASH-OUT")

        # 大额标记 (>200k)
        is_flagged_fraud = (amounts > 200000).astype(int)

        df = pd.DataFrame({
            "step": steps,
            "type": types,
            "amount": amounts,
            "nameOrig": name_orig,
            "oldbalanceOrg": np.clip(old_balance_org, 0, None),
            "newbalanceOrg": np.clip(new_balance_org, 0, None),
            "nameDest": name_dest,
            "oldbalanceDest": np.clip(old_balance_dest, 0, None),
            "newbalanceDest": np.clip(new_balance_dest, 0, None),
            "isFraud": is_fraud.astype(int),
            "isFlaggedFraud": is_flagged_fraud,
        })

        # 打乱顺序
        df = df.sample(frac=1, random_state=42).reset_index(drop=True)
        return df

    def _preprocess(self):
        """数据预处理：特征工程"""
        if self.df is None:
            raise ValueError("请先调用 load() 加载数据")

        # 1. 交易类型编码
        self.df["type_encoded"] = self.df["type"].map(self.TYPE_MAP)

        # 2. 金额对数变换 (处理长尾分布)
        self.df["log_amount"] = np.log1p(self.df["amount"])

        # 3. 时间特征
        self.df["hour"] = self.df["step"] % 24
        self.df["day"] = self.df["step"] // 24

        # 4. 余额变化特征
        self.df["balance_change_orig"] = self.df["newbalanceOrg"] - self.df["oldbalanceOrg"]
        self.df["balance_change_dest"] = self.df["newbalanceDest"] - self.df["oldbalanceDest"]

        # 5. 资金流动方向
        self.df["flow_out"] = (self.df["type"].isin(["CASH-OUT", "DEBIT", "TRANSFER"])).astype(int)
        self.df["flow_in"] = (self.df["type"] == "CASH-IN").astype(int)

        # 6. 异常比率 (金额 vs 余额)
        self.df["amount_to_balance_ratio"] = self.df["amount"] / (self.df["oldbalanceOrg"] + 1)

        print(f"预处理完成: {len(self.df)} 条交易")
        print(f"  欺诈交易: {self.df['isFraud'].sum()} ({self.df['isFraud'].mean()*100:.2f}%)")
        print(f"  正常交易: {(self.df['isFraud'] == 0).sum()}")

    def get_feature_matrix(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        获取特征矩阵和标签

        Returns:
            (features, labels) - 数值特征矩阵和二分类标签
        """
        if self.df is None:
            raise ValueError("请先调用 load() 加载数据")

        feature_cols = [
            "step", "type_encoded", "log_amount",
            "oldbalanceOrg", "newbalanceOrg",
            "oldbalanceDest", "newbalanceDest",
            "balance_change_orig", "balance_change_dest",
            "flow_out", "flow_in", "amount_to_balance_ratio",
            "isFlaggedFraud"
        ]

        features = self.df[feature_cols].values.astype(np.float32)
        labels = self.df["isFraud"].values.astype(np.int64)

        return features, labels

    def get_account_features(self) -> pd.DataFrame:
        """
        获取账户级聚合特征

        Returns:
            每个账户的统计特征 DataFrame
        """
        if self.df is None:
            raise ValueError("请先调用 load() 加载数据")

        # 按发起账户聚合
        orig_stats = self.df.groupby("nameOrig").agg({
            "amount": ["mean", "std", "max", "count"],
            "balance_change_orig": "sum",
            "isFraud": "max",  # 是否涉及欺诈
        }).reset_index()

        orig_stats.columns = [
            "account", "avg_amount", "std_amount", "max_amount",
            "txn_count", "net_flow", "involved_in_fraud"
        ]

        # 归一化
        for col in ["avg_amount", "std_amount", "max_amount", "net_flow"]:
            orig_stats[col] = np.log1p(orig_stats[col].clip(lower=0))

        return orig_stats


# ============================================================
# 2. 异配图构建器 (账户节点 + 交易边)
# ============================================================

class AMLGraphBuilder:
    """
    反洗钱异配图构建器

    图结构:
    - 节点类型: account (账户)
    - 边类型: transfer (转账关系)
    - 边属性: 金额、时间、交易类型

    支持:
    - 同构图: 账户-账户 (Account-2-Account)
    - 异构图: 账户-交易-账户 (通过交易中间节点)
    """

    def __init__(self):
        self.node_to_idx: Dict[str, int] = {}  # account_id -> node_index
        self.idx_to_node: Dict[int, str] = {}  # node_index -> account_id
        self.edge_index: Optional[np.ndarray] = None
        self.edge_features: Optional[np.ndarray] = None
        self.node_features: Optional[np.ndarray] = None
        self.labels: Optional[np.ndarray] = None
        self._graph_built = False

    def build_from_transactions(
        self,
        df: pd.DataFrame,
        account_features: Optional[pd.DataFrame] = None,
        use_transaction_nodes: bool = False
    ) -> 'AMLGraphBuilder':
        """
        从交易数据构建图

        Args:
            df: 交易数据 DataFrame (需要 nameOrig, nameDest, amount, step, type 列)
            account_features: 账户级特征 (可选)
            use_transaction_nodes: 是否使用交易中间节点模式

        Returns:
            self (支持链式调用)
        """
        if use_transaction_nodes:
            return self._build_heterogeneous_graph(df, account_features)
        else:
            return self._build_homogeneous_graph(df, account_features)

    def _build_homogeneous_graph(
        self,
        df: pd.DataFrame,
        account_features: Optional[pd.DataFrame]
    ) -> 'AMLGraphBuilder':
        """
        构建同构图: 账户 -> 账户

        Args:
            df: 交易数据
            account_features: 账户特征
        """
        print("构建同构图 (Account-2-Account)...")

        # 1. 收集所有账户
        all_accounts = set(df["nameOrig"].unique()) | set(df["nameDest"].unique())
        all_accounts = sorted(all_accounts)

        self.node_to_idx = {acc: idx for idx, acc in enumerate(all_accounts)}
        self.idx_to_node = {idx: acc for idx, acc in enumerate(all_accounts)}

        num_nodes = len(all_accounts)
        print(f"  节点数: {num_nodes}")

        # 2. 构建边 (转账关系)
        sources = df["nameOrig"].map(self.node_to_idx).values
        targets = df["nameDest"].map(self.node_to_idx).values

        # 去除自环
        mask = sources != targets
        sources = sources[mask]
        targets = targets[mask]

        self.edge_index = np.vstack([sources, targets])  # [2, num_edges]
        print(f"  边数: {self.edge_index.shape[1]}")

        # 3. 构建边特征
        df_filtered = df[mask].copy() if len(mask) == len(df) else df.iloc[mask].copy()
        self.edge_features = self._build_edge_features(df_filtered)

        # 4. 构建节点特征
        self.node_features = self._build_node_features(account_features, num_nodes)

        # 5. 构建标签 (节点是否涉及欺诈)
        self.labels = self._build_node_labels(df, num_nodes)

        self._graph_built = True
        return self

    def _build_heterogeneous_graph(
        self,
        df: pd.DataFrame,
        account_features: Optional[pd.DataFrame]
    ) -> 'AMLGraphBuilder':
        """
        构建异构图: 账户节点 + 交易节点

        节点类型:
        - account (ID 0..N-1)
        - transaction (ID N..N+M-1)

        边类型:
        - account -> transaction (发起交易)
        - transaction -> account (接收交易)
        """
        print("构建异构图 (Account + Transaction)...")

        # 1. 账户节点
        all_accounts = sorted(set(df["nameOrig"].unique()) | set(df["nameDest"].unique()))
        account_node_offset = 0
        self.node_to_idx = {acc: idx for idx, acc in enumerate(all_accounts)}
        self.idx_to_node = {idx: f"account:{acc}" for idx, acc in enumerate(all_accounts)}

        num_accounts = len(all_accounts)
        print(f"  账户节点数: {num_accounts}")

        # 2. 交易节点
        num_transactions = len(df)
        txn_node_offset = num_accounts
        for i in range(num_transactions):
            self.node_to_idx[f"txn:{i}"] = txn_node_offset + i
            self.idx_to_node[txn_node_offset + i] = f"txn:{i}"

        num_total_nodes = num_accounts + num_transactions
        print(f"  交易节点数: {num_transactions}")
        print(f"  总节点数: {num_total_nodes}")

        # 3. 构建边
        # Account -> Transaction (outgoing)
        acc_out_idx = df["nameOrig"].map(self.node_to_idx).values
        txn_out_idx = np.arange(num_transactions) + txn_node_offset

        # Transaction -> Account (incoming)
        txn_in_idx = np.arange(num_transactions) + txn_node_offset
        acc_in_idx = df["nameDest"].map(self.node_to_idx).values

        edge_index = np.hstack([
            np.vstack([acc_out_idx, txn_out_idx]),   # account -> transaction
            np.vstack([txn_in_idx, acc_in_idx])      # transaction -> account
        ])

        self.edge_index = edge_index
        print(f"  边数: {self.edge_index.shape[1]} (双向)")

        # 4. 构建节点特征
        # 账户节点特征 (只计算账户范围内的度)
        account_node_feats = self._build_node_features(
            account_features, num_accounts, max_node_idx=num_accounts
        )
        # 交易节点特征
        txn_node_feats = self._build_txn_node_features(df)

        # 统一特征维度 (账户节点用零填充到交易特征维度)
        target_dim = txn_node_feats.shape[1]
        if account_node_feats.shape[1] < target_dim:
            padding = np.zeros(
                (account_node_feats.shape[0], target_dim - account_node_feats.shape[1]),
                dtype=np.float32
            )
            account_node_feats = np.hstack([account_node_feats, padding])

        self.node_features = np.vstack([account_node_feats, txn_node_feats])

        # 5. 边特征
        self.edge_features = self._build_edge_features(df)
        # 复制一份用于反向边
        self.edge_features = np.vstack([self.edge_features, self.edge_features])

        # 6. 标签
        account_labels = self._build_node_labels(df, num_accounts)
        txn_labels = df["isFraud"].values.astype(np.int64)
        self.labels = np.concatenate([account_labels, txn_labels])

        self._graph_built = True
        return self

    def _build_edge_features(self, df: pd.DataFrame) -> np.ndarray:
        """
        构建边特征矩阵

        边特征:
        - log_amount: 对数金额
        - type_encoded: 交易类型
        - time_step: 时间步
        - is_flagged: 大额标记
        """
        features = np.column_stack([
            np.log1p(df["amount"].values),
            df["type"].map(PaySimDataset.TYPE_MAP).fillna(0).values,
            df["step"].values / 744.0,  # 归一化到 [0, 1]
            df["isFlaggedFraud"].values,
        ]).astype(np.float32)

        return features

    def _build_node_features(
        self,
        account_features: Optional[pd.DataFrame],
        num_nodes: int,
        max_node_idx: Optional[int] = None,
    ) -> np.ndarray:
        """
        构建节点特征矩阵

        节点特征:
        - 度 (入度/出度)
        - 涉及金额统计 (如果有 account_features)

        Args:
            account_features: 账户特征
            num_nodes: 节点数量
            max_node_idx: 边索引的最大节点ID (用于异构图中只计算账户节点的度)
        """
        # 基础度特征
        in_degree = np.zeros(num_nodes, dtype=np.float32)
        out_degree = np.zeros(num_nodes, dtype=np.float32)

        if self.edge_index is not None:
            for src, dst in self.edge_index.T:
                # 在异构图模式下，只统计指定范围内的节点
                if max_node_idx is not None:
                    if src >= max_node_idx or dst >= max_node_idx:
                        continue
                out_degree[src] += 1
                in_degree[dst] += 1

        # 归一化
        in_degree_norm = np.log1p(in_degree)
        out_degree_norm = np.log1p(out_degree)

        if account_features is not None:
            # 合并外部账户特征
            feat_map = account_features.set_index("account").to_dict("index")
            extra_features = np.zeros((num_nodes, 4), dtype=np.float32)

            for idx, account in self.idx_to_node.items():
                # 只处理账户节点范围内的索引
                if idx >= num_nodes:
                    continue
                # 异构图模式下，account 可能带有 "account:" 前缀
                account_key = account.replace("account:", "") if account.startswith("account:") else account
                if account_key in feat_map:
                    row = feat_map[account_key]
                    extra_features[idx] = [
                        row.get("avg_amount", 0),
                        row.get("max_amount", 0),
                        row.get("txn_count", 0),
                        row.get("net_flow", 0),
                    ]

            node_features = np.column_stack([
                in_degree_norm, out_degree_norm, extra_features
            ])
        else:
            # 仅使用度特征
            node_features = np.column_stack([
                in_degree_norm, out_degree_norm,
                np.zeros(num_nodes, dtype=np.float32),  # 占位
                np.zeros(num_nodes, dtype=np.float32),
            ])

        return node_features.astype(np.float32)

    def _build_txn_node_features(self, df: pd.DataFrame) -> np.ndarray:
        """
        构建交易节点特征 (异构图模式)
        """
        features = np.column_stack([
            np.log1p(df["amount"].values),
            df["type"].map(PaySimDataset.TYPE_MAP).fillna(0).values.astype(np.float32),
            df["step"].values.astype(np.float32) / 744.0,
            df["oldbalanceOrg"].values.astype(np.float32),
            df["newbalanceOrg"].values.astype(np.float32),
            df["oldbalanceDest"].values.astype(np.float32),
            df["newbalanceDest"].values.astype(np.float32),
        ]).astype(np.float32)

        return features

    def _build_node_labels(self, df: pd.DataFrame, num_nodes: int) -> np.ndarray:
        """
        构建节点标签 (0=正常, 1=涉及欺诈)

        规则: 如果账户作为发起方或接收方涉及任何欺诈交易，则标记为欺诈
        """
        labels = np.zeros(num_nodes, dtype=np.int64)

        fraud_mask = df["isFraud"] == 1
        fraud_orig = df.loc[fraud_mask, "nameOrig"].unique()
        fraud_dest = df.loc[fraud_mask, "nameDest"].unique()
        fraud_accounts = set(fraud_orig) | set(fraud_dest)

        for account in fraud_accounts:
            if account in self.node_to_idx:
                labels[self.node_to_idx[account]] = 1

        return labels

    def to_pyg_data(self) -> 'Data':
        """
        转换为 PyTorch Geometric Data 对象

        Returns:
            torch_geometric.data.Data
        """
        if not _PYG_AVAILABLE:
            raise ImportError("PyTorch Geometric 未安装")
        if not self._graph_built:
            raise ValueError("请先调用 build_from_transactions() 构建图")

        data = Data(
            x=torch.tensor(self.node_features, dtype=torch.float),
            edge_index=torch.tensor(self.edge_index, dtype=torch.long),
            edge_attr=torch.tensor(self.edge_features, dtype=torch.float),
            y=torch.tensor(self.labels, dtype=torch.long),
            num_nodes=self.node_features.shape[0],
        )

        print(f"PyG Data 对象创建完成:")
        print(f"  x: {data.x.shape}")
        print(f"  edge_index: {data.edge_index.shape}")
        print(f"  edge_attr: {data.edge_attr.shape}")
        print(f"  y: {data.y.shape} (正样本: {data.y.sum().item()})")

        return data

    def get_statistics(self) -> Dict:
        """获取图统计信息"""
        if not self._graph_built:
            return {"error": "图未构建"}

        return {
            "num_nodes": self.node_features.shape[0],
            "num_edges": self.edge_index.shape[1],
            "num_features": self.node_features.shape[1],
            "num_edge_features": self.edge_features.shape[1],
            "fraud_nodes": int(self.labels.sum()),
            "normal_nodes": int((self.labels == 0).sum()),
            "fraud_ratio": float(self.labels.mean()),
        }


# ============================================================
# 3. 数据集生成便捷函数
# ============================================================

def load_and_build_graph(
    data_path: Optional[str] = None,
    n_rows: Optional[int] = None,
    use_transaction_nodes: bool = False
) -> Tuple[PaySimDataset, AMLGraphBuilder]:
    """
    一站式加载数据并构建图

    Args:
        data_path: PaySim CSV 文件路径
        n_rows: 加载行数限制
        use_transaction_nodes: 是否使用交易中间节点

    Returns:
        (dataset, graph_builder)
    """
    # 1. 加载数据
    dataset = PaySimDataset(data_path)
    df = dataset.load(n_rows=n_rows)
    account_features = dataset.get_account_features()

    # 2. 构建图
    builder = AMLGraphBuilder()
    builder.build_from_transactions(
        df, account_features,
        use_transaction_nodes=use_transaction_nodes
    )

    return dataset, builder


if __name__ == "__main__":
    # 测试：使用模拟数据
    print("=" * 60)
    print("PaySim 数据集加载器 & 图构建器 - 测试")
    print("=" * 60)

    dataset, builder = load_and_build_graph(n_rows=5000, use_transaction_nodes=False)
    stats = builder.get_statistics()

    print("\n图统计:")
    for key, val in stats.items():
        print(f"  {key}: {val}")

    if _PYG_AVAILABLE:
        data = builder.to_pyg_data()
        print(f"\nPyG Data: {data}")
