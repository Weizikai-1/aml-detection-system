"""
dataset_builder 模块单元测试

测试覆盖:
1. PaySimDataset: 模拟数据生成、预处理、特征提取
2. AMLGraphBuilder: 同构/异构图构建、统计信息、PyG 转换
3. load_and_build_graph: 便捷函数
"""
import numpy as np
import pandas as pd
import pytest

from tools.dataset_builder import PaySimDataset, AMLGraphBuilder, load_and_build_graph

try:
    import torch
    from torch_geometric.data import Data
    _PYG_AVAILABLE = True
except ImportError:
    _PYG_AVAILABLE = False

N_ROWS = 200


# ============ PaySimDataset ============

class TestPaySimDataset:
    def test_load_generates_mock_data(self):
        """无文件路径时生成模拟数据"""
        ds = PaySimDataset()
        df = ds.load(n_rows=N_ROWS)
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    def test_mock_data_shape(self):
        """模拟数据形状和列名正确"""
        ds = PaySimDataset()
        df = ds.load(n_rows=N_ROWS)
        # 原始列 + 预处理新增列
        for col in ["step", "type", "amount", "nameOrig", "nameDest",
                     "isFraud", "isFlaggedFraud"]:
            assert col in df.columns

    def test_mock_data_fraud_rate(self):
        """模拟数据欺诈率约 1.3%"""
        ds = PaySimDataset()
        df = ds.load(n_rows=N_ROWS)
        rate = df["isFraud"].mean()
        # 允许一定浮动
        assert 0.005 < rate < 0.05

    def test_preprocess_adds_features(self):
        """预处理后添加特征列"""
        ds = PaySimDataset()
        df = ds.load(n_rows=N_ROWS)
        for col in ["type_encoded", "log_amount", "hour", "day",
                     "balance_change_orig", "balance_change_dest",
                     "flow_out", "flow_in", "amount_to_balance_ratio"]:
            assert col in df.columns

    def test_get_feature_matrix(self):
        """获取特征矩阵和标签"""
        ds = PaySimDataset()
        ds.load(n_rows=N_ROWS)
        features, labels = ds.get_feature_matrix()
        assert features.shape[0] == labels.shape[0]
        assert features.dtype == np.float32
        assert labels.dtype == np.int64
        assert set(np.unique(labels)).issubset({0, 1})

    def test_get_account_features(self):
        """获取账户级聚合特征"""
        ds = PaySimDataset()
        ds.load(n_rows=N_ROWS)
        acc_df = ds.get_account_features()
        assert isinstance(acc_df, pd.DataFrame)
        for col in ["account", "avg_amount", "txn_count", "involved_in_fraud"]:
            assert col in acc_df.columns
        assert len(acc_df) > 0

    def test_load_without_calling_load_raises(self):
        """未 load 就 get_feature_matrix 抛 ValueError"""
        ds = PaySimDataset()
        with pytest.raises(ValueError):
            ds.get_feature_matrix()


# ============ AMLGraphBuilder ============

class TestAMLGraphBuilder:
    @pytest.fixture
    def dataset(self):
        ds = PaySimDataset()
        ds.load(n_rows=N_ROWS)
        return ds

    def test_build_homogeneous_graph(self, dataset):
        """构建同构图"""
        builder = AMLGraphBuilder()
        builder.build_from_transactions(dataset.df)
        assert builder._graph_built
        assert builder.edge_index is not None

    def test_build_heterogeneous_graph(self, dataset):
        """构建异构图"""
        builder = AMLGraphBuilder()
        builder.build_from_transactions(
            dataset.df, use_transaction_nodes=True
        )
        assert builder._graph_built
        assert builder.edge_index is not None

    def test_homogeneous_graph_statistics(self, dataset):
        """同构图统计信息正确"""
        builder = AMLGraphBuilder()
        builder.build_from_transactions(dataset.df)
        stats = builder.get_statistics()
        assert stats["num_nodes"] > 0
        assert stats["num_edges"] > 0
        assert stats["num_features"] > 0
        assert stats["num_edge_features"] == 4
        assert stats["fraud_nodes"] + stats["normal_nodes"] == stats["num_nodes"]

    def test_heterogeneous_graph_statistics(self, dataset):
        """异构图统计信息正确"""
        builder = AMLGraphBuilder()
        builder.build_from_transactions(
            dataset.df, use_transaction_nodes=True
        )
        stats = builder.get_statistics()
        # 账户 + 交易节点
        assert stats["num_nodes"] > len(set(dataset.df["nameOrig"]) | set(dataset.df["nameDest"]))
        assert stats["num_edges"] > 0
        assert stats["num_features"] > 0

    def test_node_to_idx_mapping(self, dataset):
        """账户到节点索引映射正确"""
        builder = AMLGraphBuilder()
        builder.build_from_transactions(dataset.df)
        all_accounts = set(dataset.df["nameOrig"]) | set(dataset.df["nameDest"])
        for acc in all_accounts:
            assert acc in builder.node_to_idx
            idx = builder.node_to_idx[acc]
            assert 0 <= idx < builder.node_features.shape[0]

    def test_edge_features_shape(self, dataset):
        """边特征维度正确 [E, 4]"""
        builder = AMLGraphBuilder()
        builder.build_from_transactions(dataset.df)
        assert builder.edge_features.shape[1] == 4
        assert builder.edge_features.shape[0] == builder.edge_index.shape[1]

    def test_node_labels_fraud_marked(self, dataset):
        """涉及欺诈的账户被正确标记"""
        builder = AMLGraphBuilder()
        builder.build_from_transactions(dataset.df)
        fraud_accounts = set(
            dataset.df.loc[dataset.df["isFraud"] == 1, "nameOrig"]
        ) | set(
            dataset.df.loc[dataset.df["isFraud"] == 1, "nameDest"]
        )
        for acc in fraud_accounts:
            idx = builder.node_to_idx[acc]
            assert builder.labels[idx] == 1

    @pytest.mark.skipif(not _PYG_AVAILABLE, reason="PyG 未安装")
    def test_to_pyg_data(self, dataset):
        """转换为 PyG Data 对象"""
        builder = AMLGraphBuilder()
        builder.build_from_transactions(dataset.df)
        data = builder.to_pyg_data()
        assert isinstance(data, Data)
        assert data.x.shape[0] == data.num_nodes
        assert data.edge_index.shape[0] == 2
        assert data.y.shape[0] == data.num_nodes

    def test_to_pyg_data_without_build_raises(self):
        """未构建图就 to_pyg_data 抛 ValueError"""
        builder = AMLGraphBuilder()
        if _PYG_AVAILABLE:
            with pytest.raises(ValueError):
                builder.to_pyg_data()


# ============ load_and_build_graph ============

class TestLoadAndBuildGraph:
    def test_load_and_build_graph(self):
        """一站式加载并构建图"""
        dataset, builder = load_and_build_graph(n_rows=N_ROWS)
        assert isinstance(dataset, PaySimDataset)
        assert isinstance(builder, AMLGraphBuilder)
        assert builder._graph_built
        stats = builder.get_statistics()
        assert stats["num_nodes"] > 0
        assert stats["num_edges"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
