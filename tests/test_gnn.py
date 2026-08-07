"""GNN 图神经网络测试 — FraudGNN + 图构建 + 训练 + 预测 + 持久化"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import numpy as np
import pandas as pd

from gnn_model import (
    is_available, FraudGNN, build_graph, train_and_eval,
    predict_transactions, save_model, load_model, _calc_node_metrics,
)

_NEEDS_TORCH = pytest.mark.skipif(
    not is_available(), reason="PyTorch Geometric 未安装"
)


# ---- 测试用轻量 DataFrame ----
def _make_toy_df(n_txns=30, n_fraud=3):
    """构造小规模 PaySim 格式交易数据"""
    rng = np.random.RandomState(42)
    records = []
    accounts = [f"C{i:06d}" for i in range(20)]
    for i in range(n_txns):
        src = rng.choice(accounts)
        dst = rng.choice([a for a in accounts if a != src])
        is_fraud = 1 if i < n_fraud else 0
        records.append({
            "step": i % 100,
            "type": rng.choice(["TRANSFER", "CASH_IN", "CASH_OUT", "PAYMENT"]),
            "amount": rng.lognormal(8, 2) if is_fraud else rng.lognormal(6, 2),
            "nameOrig": src,
            "nameDest": dst,
            "oldbalanceOrg": 100000.0,
            "newbalanceOrig": 90000.0,
            "oldbalanceDest": 50000.0,
            "newbalanceDest": 60000.0,
            "isFraud": is_fraud,
            "isFlaggedFraud": 0,
        })
    return pd.DataFrame(records)


# ============================================================
# FraudGNN 模型测试
# ============================================================

class TestFraudGNN:
    @_NEEDS_TORCH
    def test_construct_gat(self):
        model = FraudGNN(in_dim=8, model_type="gat")
        assert model.model_type == "gat"
        assert isinstance(model, FraudGNN)

    @_NEEDS_TORCH
    def test_construct_sage(self):
        model = FraudGNN(in_dim=8, model_type="sage")
        assert model.model_type == "sage"

    @_NEEDS_TORCH
    def test_construct_gcn(self):
        model = FraudGNN(in_dim=8, model_type="gcn")
        assert model.model_type == "gcn"

    @_NEEDS_TORCH
    def test_forward_shape(self):
        df = _make_toy_df(50)
        data = build_graph(df)
        model = FraudGNN(in_dim=data.x.size(1), model_type="gat")
        out = model(data)
        assert out.shape == (data.x.size(0), 1), f"期望 ({data.x.size(0)}, 1), 得到 {out.shape}"

    @_NEEDS_TORCH
    def test_forward_shape_sage(self):
        df = _make_toy_df(30)
        data = build_graph(df)
        model = FraudGNN(in_dim=data.x.size(1), model_type="sage")
        out = model(data)
        assert out.shape == (data.x.size(0), 1)

    @_NEEDS_TORCH
    def test_forward_shape_gcn(self):
        df = _make_toy_df(30)
        data = build_graph(df)
        model = FraudGNN(in_dim=data.x.size(1), model_type="gcn")
        out = model(data)
        assert out.shape == (data.x.size(0), 1)

    @_NEEDS_TORCH
    def test_train_eval_mode(self):
        model = FraudGNN(in_dim=8)
        assert model.training is True  # 默认训练模式
        model.eval()
        assert model.training is False
        model.train()
        assert model.training is True

    @_NEEDS_TORCH
    def test_parameters_count(self):
        model = FraudGNN(in_dim=8, hidden=32, model_type="gcn")
        params = list(model.parameters())
        assert len(params) > 2, f"参数过少: {len(params)} 组"

    @_NEEDS_TORCH
    def test_state_dict_roundtrip(self):
        """state_dict 保存/加载往返一致"""
        model1 = FraudGNN(in_dim=8, hidden=16, model_type="gat")
        df = _make_toy_df(30)
        data = build_graph(df)
        model1(data)  # 触发一次前向
        sd = model1.state_dict()

        model2 = FraudGNN(in_dim=8, hidden=16, model_type="gat")
        model2.load_state_dict(sd)
        for p1, p2 in zip(model1.parameters(), model2.parameters()):
            assert (p1 == p2).all(), "state_dict 往返后参数不一致"


# ============================================================
# 图构建测试
# ============================================================

class TestBuildGraph:
    @_NEEDS_TORCH
    def test_build_returns_data(self):
        df = _make_toy_df(50)
        data = build_graph(df)
        assert hasattr(data, "x")
        assert hasattr(data, "edge_index")
        assert hasattr(data, "y")
        assert hasattr(data, "train_mask")
        assert hasattr(data, "test_mask")

    @_NEEDS_TORCH
    def test_node_count(self):
        df = _make_toy_df(50)
        data = build_graph(df)
        accounts = set(df["nameOrig"]) | set(df["nameDest"])
        assert data.x.size(0) == len(accounts)

    @_NEEDS_TORCH
    def test_feature_dim(self):
        df = _make_toy_df(50)
        data = build_graph(df)
        assert data.x.size(1) == 8, f"期望 8 维特征, 得到 {data.x.size(1)}"

    @_NEEDS_TORCH
    def test_edge_count(self):
        df = _make_toy_df(50)
        data = build_graph(df)
        assert data.edge_index.size(1) == len(df), "边数应等于交易数"

    @_NEEDS_TORCH
    def test_labels_exist(self):
        df = _make_toy_df(50, n_fraud=5)
        data = build_graph(df)
        assert data.y.sum() > 0, "应有被标记为欺诈的节点"

    @_NEEDS_TORCH
    def test_train_test_split(self):
        df = _make_toy_df(100)
        data = build_graph(df)
        n = data.x.size(0)
        assert data.train_mask.sum() == int(n * 0.7)
        assert data.test_mask.sum() == n - int(n * 0.7)
        assert not (data.train_mask & data.test_mask).any(), "训练/测试集不应重叠"

    @_NEEDS_TORCH
    def test_empty_fraud(self):
        """无欺诈交易时不应崩溃"""
        df = _make_toy_df(30, n_fraud=0)
        data = build_graph(df)
        assert data.y.sum() == 0


# ============================================================
# 训练 + 评估测试
# ============================================================

class TestTrainAndEval:
    @_NEEDS_TORCH
    def test_train_returns_metrics(self):
        df = _make_toy_df(60, n_fraud=10)
        data = build_graph(df)
        result = train_and_eval(data, epochs=5, lr=0.01, model_type="gcn")
        assert "node_f1" in result
        assert "node_precision" in result
        assert "node_recall" in result
        assert "model" in result
        assert 0.0 <= result["node_f1"] <= 1.0

    @_NEEDS_TORCH
    def test_train_gat(self):
        df = _make_toy_df(60, n_fraud=10)
        data = build_graph(df)
        result = train_and_eval(data, epochs=5, model_type="gat")
        assert result["model"] is not None
        assert "best_f1" in result

    @_NEEDS_TORCH
    def test_train_sage(self):
        df = _make_toy_df(40, n_fraud=5)
        data = build_graph(df)
        result = train_and_eval(data, epochs=3, model_type="sage")
        assert result["node_f1"] >= 0.0

    @_NEEDS_TORCH
    def test_predict_transactions(self):
        df = _make_toy_df(40, n_fraud=6)
        data = build_graph(df)
        result = train_and_eval(data, epochs=5, model_type="gcn")
        preds = predict_transactions(result["model"], data, df)
        assert len(preds) == len(df)
        assert set(np.unique(preds)).issubset({0, 1})

    @_NEEDS_TORCH
    def test_model_save_load(self):
        """save_model / load_model 往返测试"""
        import tempfile
        df = _make_toy_df(40, n_fraud=5)
        data = build_graph(df)
        result = train_and_eval(data, epochs=5, model_type="gcn")

        with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as f:
            tmp_path = f.name
        try:
            save_model(result["model"], tmp_path)
            loaded = load_model(tmp_path, in_dim=8, model_type="gcn")
            assert loaded is not None
            assert not loaded.training  # eval 模式
            # 验证前向传播正常
            out = loaded(data)
            assert out.shape == (data.x.size(0), 1)
        finally:
            os.unlink(tmp_path)


# ============================================================
# 指标计算测试
# ============================================================

class TestMetrics:
    @_NEEDS_TORCH
    def test_perfect_prediction(self):
        import torch
        prob = torch.tensor([0.9, 0.9, 0.1, 0.1])
        labels = torch.tensor([1, 1, 0, 0])
        m = _calc_node_metrics(prob, labels)
        assert m["precision"] == 1.0
        assert m["recall"] == 1.0
        assert m["f1"] == 1.0

    @_NEEDS_TORCH
    def test_all_wrong(self):
        import torch
        prob = torch.tensor([0.1, 0.1])
        labels = torch.tensor([1, 1])
        m = _calc_node_metrics(prob, labels)
        assert m["precision"] == 0.0
        assert m["recall"] == 0.0

    @_NEEDS_TORCH
    def test_no_positive_labels(self):
        import torch
        prob = torch.tensor([0.9, 0.1])
        labels = torch.tensor([0, 0])
        m = _calc_node_metrics(prob, labels)
        assert m["precision"] == 0.0  # 无正样本，precision=0


# ============================================================
# 无 torch 环境测试
# ============================================================

class TestNoTorch:
    def test_is_available_runs(self):
        """is_available() 在任何环境下都应正常运行"""
        result = is_available()
        assert isinstance(result, bool)
