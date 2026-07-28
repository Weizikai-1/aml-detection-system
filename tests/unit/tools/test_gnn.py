"""
GNN 模块单元测试

测试覆盖:
1. GCN 模型结构
2. 模型前向传播
3. 推理/预测
4. GNN 数据准备
5. GNN 训练
6. GNN 推理
"""
import torch
import numpy as np
from torch_geometric.data import Data

from tools.gnn_model import MoneyLaunderingGCN, create_model
from tools.gnn_trainer import (
    _build_node_features,
    _build_labels,
    prepare_gnn_data,
    train_gnn,
    infer_gnn,
)


# ============ 测试数据工厂 ============

def _make_txn(tid, frm, to, amount):
    return {
        "transaction_id": tid,
        "from_account": frm,
        "to_account": to,
        "amount": amount,
        "timestamp": "2026-07-26T10:00:00",
        "transaction_type": "transfer",
        "remark": "",
    }


def _make_rule_hit(txn, rules, risk=0.8):
    return {
        "transaction": txn,
        "rule_hits": rules,
        "risk_score": risk,
        "evidence": [f"命中{rules[0]}"],
    }


# ============ 测试: 模型结构 ============

class TestMoneyLaunderingGCN:
    def test_create_model_default(self):
        """默认参数创建模型"""
        model = create_model()
        assert isinstance(model, MoneyLaunderingGCN)
        assert model.conv1.in_channels == 6
        assert model.conv2.out_channels == 2

    def test_create_model_custom_channels(self):
        """自定义输入维度"""
        model = create_model(in_channels=8)
        assert model.conv1.in_channels == 8

    def test_model_params_count(self):
        """模型参数数合理(不是空模型)"""
        model = create_model()
        total = sum(p.numel() for p in model.parameters())
        assert total > 100  # 至少有一些参数

    def test_forward_shape(self):
        """前向传播输出维度正确"""
        model = create_model()
        x = torch.randn(10, 6)
        edge_index = torch.tensor([[0, 1, 2, 3], [1, 2, 3, 4]], dtype=torch.long)
        out = model(x, edge_index)
        assert out.shape == (10, 2)

    def test_predict_range(self):
        """predict 输出在 [0, 1] 范围"""
        model = create_model()
        x = torch.randn(10, 6)
        edge_index = torch.tensor([[0, 1, 2, 3], [1, 2, 3, 4]], dtype=torch.long)
        probs = model.predict(x, edge_index)
        assert probs.shape == (10,)
        assert torch.all(probs >= 0)
        assert torch.all(probs <= 1)


# ============ 测试: 节点特征构建 ============

class TestNodeFeatures:
    def test_feature_shape(self):
        """特征矩阵维度正确"""
        account_stats = {
            "A": {"in_degree": 2, "out_degree": 1, "in_amount": 50000.0, "out_amount": 30000.0, "total_txns": 3},
            "B": {"in_degree": 1, "out_degree": 2, "in_amount": 30000.0, "out_amount": 50000.0, "total_txns": 3},
        }
        rule_risk_scores = {"A": 0.8, "B": 0.0}
        account_to_idx = {"A": 0, "B": 1}

        x = _build_node_features(account_stats, rule_risk_scores, account_to_idx)
        assert x.shape == (2, 6)
        assert isinstance(x, torch.Tensor)

    def test_feature_normalization(self):
        """特征归一化到 [0, 1]"""
        account_stats = {
            "A": {"in_degree": 10, "out_degree": 20, "in_amount": 100000, "out_amount": 200000, "total_txns": 30},
            "B": {"in_degree": 0, "out_degree": 0, "in_amount": 0, "out_amount": 0, "total_txns": 0},
        }
        rule_risk_scores = {"A": 1.0, "B": 0.0}
        account_to_idx = {"A": 0, "B": 1}

        x = _build_node_features(account_stats, rule_risk_scores, account_to_idx)
        assert torch.all(x >= 0)
        assert torch.all(x <= 1)
        # A 应该全为 1 (最大值)，B 全为 0 (最小值)
        assert torch.allclose(x[0], torch.ones(6))
        assert torch.allclose(x[1], torch.zeros(6))


# ============ 测试: 标签构建 ============

class TestLabels:
    def test_label_binary(self):
        """标签是 0/1 二分类"""
        account_to_idx = {"A": 0, "B": 1, "C": 2}
        rule_risk_scores = {"A": 0.8, "B": 0.0, "C": 0.5}

        y = _build_labels(account_to_idx, rule_risk_scores)
        assert y.tolist() == [1, 0, 1]

    def test_label_all_normal(self):
        """无规则命中时全为 0"""
        account_to_idx = {"A": 0, "B": 1}
        rule_risk_scores = {}

        y = _build_labels(account_to_idx, rule_risk_scores)
        assert torch.all(y == 0)


# ============ 测试: 数据准备 ============

class TestPrepareGNNData:
    def test_basic_graph(self):
        """3 账户 2 笔交易构建 PyG Data"""
        txns = [
            _make_txn("T1", "A", "B", 10000),
            _make_txn("T2", "B", "C", 5000),
        ]
        rule_hits = [_make_rule_hit(txns[0], ["large_amount"], 0.8)]

        data = prepare_gnn_data(txns, rule_hits)

        assert data.num_nodes == 3
        assert data.edge_index.shape[1] == 2  # 2 条边
        assert data.x.shape == (3, 6)
        assert data.y.shape == (3,)
        assert data.account_to_idx is not None

    def test_edge_direction(self):
        """边方向正确: A→B 对应 edge_index[:, i] = [idx(A), idx(B)]"""
        txns = [_make_txn("T1", "A", "B", 10000)]
        rule_hits = []

        data = prepare_gnn_data(txns, rule_hits)
        idx_a = data.account_to_idx["A"]
        idx_b = data.account_to_idx["B"]

        assert (data.edge_index[:, 0] == torch.tensor([idx_a, idx_b])).all()

    def test_label_from_rule_hits(self):
        """规则命中账户标签=1"""
        txns = [
            _make_txn("T1", "A", "B", 10000),
            _make_txn("T2", "C", "D", 1000),
        ]
        rule_hits = [_make_rule_hit(txns[0], ["large_amount"], 0.8)]

        data = prepare_gnn_data(txns, rule_hits)
        idx_a = data.account_to_idx["A"]
        idx_b = data.account_to_idx["B"]
        idx_c = data.account_to_idx["C"]

        assert data.y[idx_a].item() == 1
        assert data.y[idx_b].item() == 1
        assert data.y[idx_c].item() == 0


# ============ 测试: GNN 训练 ============

class TestTrainGNN:
    @classmethod
    def setup_class(cls):
        """构建一个标准测试图: 5 个正常 + 3 个可疑"""
        txns = []
        # 正常交易链
        for i in range(4):
            txns.append(_make_txn(f"T{i}", f"N{i}", f"N{i+1}", 1000 + i * 100))
        # 可疑闭环
        txns += [
            _make_txn("TS1", "S0", "S1", 50000),
            _make_txn("TS2", "S1", "S2", 50000),
            _make_txn("TS3", "S2", "S0", 50000),
        ]
        rule_hits = [
            _make_rule_hit(txns[4], ["large_amount"], 0.9),
            _make_rule_hit(txns[5], ["large_amount"], 0.9),
            _make_rule_hit(txns[6], ["large_amount"], 0.9),
        ]

        cls.data = prepare_gnn_data(txns, rule_hits)

    def test_training_runs(self):
        """训练能正常运行并返回模型"""
        model, metrics = train_gnn(self.data, epochs=100, verbose=False)
        assert isinstance(model, MoneyLaunderingGCN)
        assert "final_train_acc" in metrics
        assert "final_val_acc" in metrics
        assert len(metrics["train_losses"]) == 100
        assert metrics["final_train_acc"] >= 0.0

    def test_loss_decreases(self):
        """训练 loss 大致下降"""
        model, metrics = train_gnn(self.data, epochs=200, verbose=False)
        early_loss = np.mean(metrics["train_losses"][:20])
        late_loss = np.mean(metrics["train_losses"][-20:])
        # 后期 loss 应该低于前期
        assert late_loss <= early_loss * 1.1  # 允许小幅波动


# ============ 测试: GNN 推理 ============

class TestInferGNN:
    @classmethod
    def setup_class(cls):
        txns = []
        for i in range(4):
            txns.append(_make_txn(f"T{i}", f"N{i}", f"N{i+1}", 1000 + i * 100))
        txns += [
            _make_txn("TS1", "S0", "S1", 50000),
            _make_txn("TS2", "S1", "S2", 50000),
            _make_txn("TS3", "S2", "S0", 50000),
        ]
        rule_hits = [
            _make_rule_hit(txns[4], ["large_amount"], 0.9),
            _make_rule_hit(txns[5], ["large_amount"], 0.9),
            _make_rule_hit(txns[6], ["large_amount"], 0.9),
        ]
        cls.data = prepare_gnn_data(txns, rule_hits)
        cls.model, _ = train_gnn(cls.data, epochs=150, verbose=False)

    def test_infer_returns_dict(self):
        """推理返回完整字典结构"""
        result = infer_gnn(self.model, self.data)
        assert "scores" in result
        assert "high_risk" in result
        assert "stats" in result

    def test_scores_match_nodes(self):
        """每个节点都有评分"""
        result = infer_gnn(self.model, self.data)
        assert len(result["scores"]) == self.data.num_nodes

    def test_high_risk_threshold(self):
        """高分账户概率 > 0.5"""
        result = infer_gnn(self.model, self.data)
        for acc, prob in result["high_risk"]:
            assert prob > 0.5

    def test_stats_reasonable(self):
        """统计值在合理范围"""
        result = infer_gnn(self.model, self.data)
        stats = result["stats"]
        assert 0 <= stats["avg_score"] <= 1
        assert 0 <= stats["high_risk_ratio"] <= 1
        assert stats["total_nodes"] == self.data.num_nodes


# ============ 测试: 图分析 Agent 中的 GNN 集成 ============

class TestGraphAnalystWithGNN:
    def test_gnn_result_in_graph_data(self):
        """完整图分析后 graph_data 应包含 gnn_result"""
        from agents.graph_analyst import create_graph_analyst_agent
        from graph.state import AMLState, SuspiciousTransaction

        txns = []
        for i in range(8):
            txns.append(_make_txn(f"T{i}", f"A{i % 5}", f"B{i % 5 + 1}", 10000 + i * 5000))

        rule_hits: list[SuspiciousTransaction] = [
            _make_rule_hit(txns[0], ["large_amount"], 0.8),
        ]

        agent = create_graph_analyst_agent()
        state: AMLState = {
            "cleaned_transactions": txns,
            "rule_hits": rule_hits,
        }
        result = agent(state)

        gd = result["graph_data"]
        assert "gnn_result" in gd
        # >5 个节点应该触发了 GNN
        assert gd["gnn_result"] is not None
        assert "scores" in gd["gnn_result"]
        assert "stats" in gd["gnn_result"]

    def test_small_graph_skips_gnn(self):
        """节点不足时跳过 GNN"""
        from agents.graph_analyst import create_graph_analyst_agent
        from graph.state import AMLState

        # 只 3 个账户，< 6 阈值，应跳过
        txns = [
            _make_txn("T1", "A", "B", 10000),
            _make_txn("T2", "B", "A", 5000),
        ]

        agent = create_graph_analyst_agent()
        state: AMLState = {
            "cleaned_transactions": txns,
            "rule_hits": [],
        }
        result = agent(state)

        gd = result["graph_data"]
        assert gd["gnn_result"] is None


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
