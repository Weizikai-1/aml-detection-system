"""
GNN 深化模块单元测试

覆盖:
- 多模型支持（GCN/GAT/GraphSAGE）
- 模型工厂和自动选型
- GAT 注意力权重
- GNN 可解释性（特征重要性、邻居重要性、综合解释）
"""
import pytest
import torch

# 只有 PyG 可用时才跑 GNN 测试
pytest.importorskip("torch_geometric", reason="PyTorch Geometric 未安装")


# ============================================================
# 测试数据构造
# ============================================================
def _make_test_graph(num_nodes=20, num_edges=50):
    """构造测试用的小型资金图谱"""
    torch.manual_seed(42)

    # 节点特征: [入度, 出度, 入金, 出金, 频次, 规则分]
    x = torch.rand(num_nodes, 6)
    x[:, 0] = (x[:, 0] * 10).int().float()  # 入度
    x[:, 1] = (x[:, 1] * 10).int().float()  # 出度
    x[:, 2] = x[:, 2] * 100000  # 入金金额
    x[:, 3] = x[:, 3] * 100000  # 出金金额
    x[:, 4] = (x[:, 4] * 50).int().float()  # 频次
    x[:, 5] = x[:, 5] * 100  # 规则风险分 0-100

    # 边索引（随机生成双向边）
    src = torch.randint(0, num_nodes, (num_edges,))
    dst = torch.randint(0, num_nodes, (num_edges,))
    # 确保没有自环
    mask = src != dst
    src = src[mask]
    dst = dst[mask]
    edge_index = torch.stack([src, dst], dim=0)

    return x, edge_index


# ============================================================
# 1. 多模型测试
# ============================================================
class TestGNNModels:
    """GNN 多模型测试"""

    def test_gcn_model(self):
        """GCN 模型前向传播"""
        from tools.gnn_model import MoneyLaunderingGCN

        x, edge_index = _make_test_graph()
        model = MoneyLaunderingGCN(in_channels=6, hidden_channels=32)

        logits = model(x, edge_index)
        assert logits.shape == (x.shape[0], 2)

        probs = model.predict(x, edge_index)
        assert probs.shape == (x.shape[0],)
        assert torch.all(probs >= 0) and torch.all(probs <= 1)
        assert model.model_type == "gcn"

    def test_gat_model(self):
        """GAT 模型前向传播"""
        from tools.gnn_model import MoneyLaunderingGAT

        x, edge_index = _make_test_graph()
        model = MoneyLaunderingGAT(in_channels=6, hidden_channels=32, heads=2)

        logits = model(x, edge_index)
        assert logits.shape == (x.shape[0], 2)
        assert model.model_type == "gat"

    def test_gat_attention_weights(self):
        """GAT 返回注意力权重"""
        from tools.gnn_model import MoneyLaunderingGAT

        x, edge_index = _make_test_graph(num_nodes=10, num_edges=20)
        model = MoneyLaunderingGAT(in_channels=6, hidden_channels=32, heads=2)

        logits, attn = model(x, edge_index, return_attention_weights=True)

        assert logits.shape == (x.shape[0], 2)
        assert "layer1" in attn
        assert "layer2" in attn
        assert "edge_index" in attn["layer1"]
        assert "attention" in attn["layer1"]

        # 注意力权重应该是正数
        assert torch.all(attn["layer1"]["attention"] >= 0)

    def test_gat_predict_with_attention(self):
        """GAT 预测并返回注意力"""
        from tools.gnn_model import MoneyLaunderingGAT

        x, edge_index = _make_test_graph(num_nodes=10, num_edges=20)
        model = MoneyLaunderingGAT(in_channels=6, hidden_channels=32, heads=2)

        result = model.predict_with_attention(x, edge_index)
        assert "probabilities" in result
        assert "attention" in result
        assert result["probabilities"].shape == (x.shape[0],)

    def test_graphsage_model(self):
        """GraphSAGE 模型前向传播"""
        from tools.gnn_model import MoneyLaunderingSAGE

        x, edge_index = _make_test_graph()
        model = MoneyLaunderingSAGE(in_channels=6, hidden_channels=32)

        logits = model(x, edge_index)
        assert logits.shape == (x.shape[0], 2)
        assert model.model_type == "graphsage"

        probs = model.predict(x, edge_index)
        assert probs.shape == (x.shape[0],)
        assert torch.all(probs >= 0) and torch.all(probs <= 1)

    def test_graphsage_residual(self):
        """GraphSAGE 残差连接验证"""
        from tools.gnn_model import MoneyLaunderingSAGE

        x, edge_index = _make_test_graph()
        model = MoneyLaunderingSAGE(in_channels=6, hidden_channels=32)

        # 线性层存在
        assert hasattr(model, "lin")

    def test_model_different_hidden_size(self):
        """不同隐藏层大小"""
        from tools.gnn_model import create_model

        x, edge_index = _make_test_graph()

        for hidden in [16, 32, 128]:
            model = create_model("gcn", in_channels=6, hidden_channels=hidden)
            probs = model.predict(x, edge_index)
            assert probs.shape == (x.shape[0],)

    def test_all_predict_in_range(self):
        """所有模型输出都在 [0, 1]"""
        from tools.gnn_model import create_model

        x, edge_index = _make_test_graph()

        for mtype in ["gcn", "gat", "graphsage"]:
            model = create_model(mtype, in_channels=6, hidden_channels=32)
            probs = model.predict(x, edge_index)
            assert torch.all(probs >= 0) and torch.all(probs <= 1), f"{mtype} 输出越界"


# ============================================================
# 2. 模型工厂测试
# ============================================================
class TestModelFactory:
    """模型工厂测试"""

    def test_create_gcn(self):
        """创建 GCN 模型"""
        from tools.gnn_model import create_model

        model = create_model("gcn", in_channels=6, hidden_channels=64)
        assert model.model_type == "gcn"

    def test_create_gat(self):
        """创建 GAT 模型"""
        from tools.gnn_model import create_model

        model = create_model("gat", in_channels=6, hidden_channels=64, heads=4)
        assert model.model_type == "gat"

    def test_create_graphsage(self):
        """创建 GraphSAGE 模型"""
        from tools.gnn_model import create_model

        model = create_model("graphsage", in_channels=6, hidden_channels=64, aggr="max")
        assert model.model_type == "graphsage"

    def test_create_invalid_model(self):
        """无效模型类型抛出异常"""
        from tools.gnn_model import create_model

        with pytest.raises(ValueError):
            create_model("invalid_model")

    def test_pyg_available(self):
        """PyG 可用性检查"""
        from tools.gnn_model import is_pyg_available

        assert is_pyg_available() is True

    def test_select_model_by_size_small(self):
        """小图选 GAT"""
        from tools.gnn_model import select_model_by_size

        assert select_model_by_size(100, 200) == "gat"

    def test_select_model_by_size_medium(self):
        """中图选 GCN"""
        from tools.gnn_model import select_model_by_size

        assert select_model_by_size(5000, 10000) == "gcn"

    def test_select_model_by_size_large(self):
        """大图选 GraphSAGE"""
        from tools.gnn_model import select_model_by_size

        assert select_model_by_size(50000, 100000) == "graphsage"

    def test_model_registry(self):
        """模型注册表"""
        from tools.gnn_model import MODEL_REGISTRY

        assert "gcn" in MODEL_REGISTRY
        assert "gat" in MODEL_REGISTRY
        assert "graphsage" in MODEL_REGISTRY


# ============================================================
# 3. 可解释性测试
# ============================================================
class TestGNNExplainer:
    """GNN 可解释性测试"""

    @pytest.fixture
    def setup(self):
        from tools.gnn_model import create_model
        from tools.gnn_explainer import GNNExplainer

        x, edge_index = _make_test_graph(num_nodes=20, num_edges=50)
        model = create_model("gcn", in_channels=6, hidden_channels=32)
        model.eval()

        explainer = GNNExplainer(model)
        return explainer, x, edge_index

    def test_feature_importance(self, setup):
        """特征重要性分析"""
        explainer, x, edge_index = setup

        importances = explainer.feature_importance(
            x, edge_index, node_index=0,
            feature_names=["入度", "出度", "入金", "出金", "频次", "规则分"]
        )

        assert len(importances) == 6
        assert "feature_name" in importances[0]
        assert "importance" in importances[0]
        assert "importance_ratio" in importances[0]

        # 按重要性排序（降序）
        for i in range(len(importances) - 1):
            assert importances[i]["importance"] >= importances[i + 1]["importance"]

        # 重要性比例之和 ≈ 1
        total = sum(i["importance_ratio"] for i in importances)
        assert abs(total - 1.0) < 0.01 or total == 0

    def test_neighbor_importance(self, setup):
        """邻居节点重要性"""
        explainer, x, edge_index = setup

        neighbors = explainer.neighbor_importance(x, edge_index, node_index=0, top_k=3)

        assert len(neighbors) <= 3
        if neighbors:
            assert "node_index" in neighbors[0]
            assert "importance" in neighbors[0]
            assert "impact" in neighbors[0]

    def test_explain_with_attention(self):
        """注意力权重解释（GAT 专用）"""
        from tools.gnn_model import MoneyLaunderingGAT
        from tools.gnn_explainer import GNNExplainer

        x, edge_index = _make_test_graph(num_nodes=10, num_edges=20)
        model = MoneyLaunderingGAT(in_channels=6, hidden_channels=32, heads=2)
        model.eval()

        # 获取注意力数据
        _, attn = model(x, edge_index, return_attention_weights=True)

        explainer = GNNExplainer(model)
        result = explainer.explain_with_attention(attn, node_index=0, top_k=3)

        assert "node_index" in result
        assert "top_neighbors" in result
        assert len(result["top_neighbors"]) <= 3

        if result["top_neighbors"]:
            assert "node_index" in result["top_neighbors"][0]
            assert "attention_score" in result["top_neighbors"][0]

    def test_explain_prediction(self, setup):
        """综合预测解释"""
        explainer, x, edge_index = setup

        result = explainer.explain_prediction(
            x, edge_index,
            node_index=0,
            feature_names=["入度", "出度", "入金", "出金", "频次", "规则分"],
        )

        assert "node_index" in result
        assert "predicted_probability" in result
        assert "risk_level" in result
        assert "top_neighbors" in result
        assert "top_features" in result
        assert "explanation_text" in result

        # 风险等级有效
        assert result["risk_level"] in ["high", "medium", "low"]

        # 概率在 [0, 1]
        assert 0 <= result["predicted_probability"] <= 1

        # 有解释文本
        assert len(result["explanation_text"]) > 0

    def test_explain_prediction_with_account_names(self, setup):
        """带账户名称的解释"""
        explainer, x, edge_index = setup

        account_names = {0: "账户A", 1: "账户B", 2: "账户C"}
        result = explainer.explain_prediction(
            x, edge_index,
            node_index=0,
            account_names=account_names,
        )

        assert "账户A" in result["explanation_text"]

    def test_explainer_no_model(self):
        """没有模型时的降级处理"""
        from tools.gnn_explainer import GNNExplainer

        explainer = GNNExplainer(model=None)
        x, edge_index = _make_test_graph()

        # 没有模型时，特征重要性返回空
        result = explainer.feature_importance(x, edge_index, node_index=0)
        assert result == []

        # 综合解释返回错误
        exp_result = explainer.explain_prediction(x, edge_index, node_index=0)
        assert "error" in exp_result

    def test_aml_feature_names(self):
        """预设的 AML 特征名称"""
        from tools.gnn_explainer import AML_FEATURE_NAMES

        assert len(AML_FEATURE_NAMES) == 6
        assert "入度" in AML_FEATURE_NAMES
        assert "规则引擎风险分" in AML_FEATURE_NAMES


# ============================================================
# 4. 训练器兼容测试
# ============================================================
class TestTrainerCompatibility:
    """训练器兼容性测试"""

    def test_trainer_with_gcn(self):
        """训练器与 GCN 模型兼容"""
        from tools.gnn_trainer import GNNPredictor

        x, edge_index = _make_test_graph(num_nodes=50, num_edges=100)
        predictor = GNNPredictor(model_type="gcn")

        labels = (x[:, 5] > 70).long()  # 用规则分 70 以上当标签

        predictor.train(x, edge_index, labels, epochs=2)

        # 预测
        predictions = predictor.predict(x, edge_index)
        assert len(predictions) == x.shape[0]
        assert all(0 <= p <= 1 for p in predictions)

    def test_trainer_with_gat(self):
        """训练器与 GAT 模型兼容"""
        from tools.gnn_trainer import GNNPredictor

        x, edge_index = _make_test_graph(num_nodes=50, num_edges=100)
        predictor = GNNPredictor(model_type="gat")

        labels = (x[:, 5] > 70).long()
        predictor.train(x, edge_index, labels, epochs=2)

        predictions = predictor.predict(x, edge_index)
        assert len(predictions) == x.shape[0]

    def test_trainer_with_graphsage(self):
        """训练器与 GraphSAGE 模型兼容"""
        from tools.gnn_trainer import GNNPredictor

        x, edge_index = _make_test_graph(num_nodes=50, num_edges=100)
        predictor = GNNPredictor(model_type="graphsage")

        labels = (x[:, 5] > 70).long()
        predictor.train(x, edge_index, labels, epochs=2)

        predictions = predictor.predict(x, edge_index)
        assert len(predictions) == x.shape[0]
