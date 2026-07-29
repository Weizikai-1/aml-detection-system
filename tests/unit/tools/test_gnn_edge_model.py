"""
边特征增强 GNN 模型单元测试

测试覆盖:
1. EdgeFeatureEncoder: 形状 + 非零输出
2. GatedEdgeFilter: 形状 + 门控范围
3. EdgeAwareGCN: 前向形状 + 概率范围 + 边重要性
4. EdgeAwareGAT: 前向形状 + 注意力权重 + 概率范围
5. create_edge_gnn 工厂函数
"""
import pytest
import torch

pg = pytest.importorskip("torch_geometric")

from tools.gnn_edge_model import (
    EdgeFeatureEncoder,
    GatedEdgeFilter,
    EdgeAwareGCN,
    EdgeAwareGAT,
    create_edge_gnn,
    is_edge_gnn_available,
)

# ============ 测试数据常量 ============
N, E, F, EDGE_DIM = 20, 50, 6, 4
HIDDEN_DIM = 16


def _make_graph():
    """生成小规模模拟图数据"""
    x = torch.randn(N, F)
    edge_index = torch.randint(0, N, (2, E))
    edge_attr = torch.randn(E, EDGE_DIM)
    return x, edge_index, edge_attr


# ============ 1. EdgeFeatureEncoder ============


class TestEdgeFeatureEncoder:
    def test_forward_shape(self):
        """输入 [E, edge_dim] 输出 [E, hidden_dim]"""
        enc = EdgeFeatureEncoder(EDGE_DIM, HIDDEN_DIM)
        edge_attr = torch.randn(E, EDGE_DIM)
        with torch.no_grad():
            out = enc(edge_attr)
        assert out.shape == (E, HIDDEN_DIM)

    def test_forward_values(self):
        """输出非全零"""
        enc = EdgeFeatureEncoder(EDGE_DIM, HIDDEN_DIM)
        edge_attr = torch.randn(E, EDGE_DIM)
        with torch.no_grad():
            out = enc(edge_attr)
        assert not torch.all(out == 0)


# ============ 2. GatedEdgeFilter ============


class TestGatedEdgeFilter:
    def test_forward_shape(self):
        """输出形状与 node_msg 一致"""
        filt = GatedEdgeFilter(EDGE_DIM, HIDDEN_DIM)
        node_msg = torch.randn(E, HIDDEN_DIM)
        edge_feat = torch.randn(E, EDGE_DIM)
        with torch.no_grad():
            out = filt(node_msg, edge_feat)
        assert out.shape == node_msg.shape

    def test_gate_range(self):
        """门控值在 (0, 1) 之间 (sigmoid)"""
        filt = GatedEdgeFilter(EDGE_DIM, HIDDEN_DIM)
        node_msg = torch.randn(E, HIDDEN_DIM)
        edge_feat = torch.randn(E, EDGE_DIM)
        with torch.no_grad():
            combined = torch.cat([node_msg, edge_feat], dim=-1)
            gate = filt.gate(combined)
        assert torch.all(gate > 0) and torch.all(gate < 1)


# ============ 3. EdgeAwareGCN ============


class TestEdgeAwareGCN:
    def test_forward_shape(self):
        """输出 [N, num_classes]"""
        model = EdgeAwareGCN(
            in_channels=F, hidden_channels=HIDDEN_DIM,
            edge_dim=EDGE_DIM, num_classes=2,
        )
        x, edge_index, edge_attr = _make_graph()
        with torch.no_grad():
            out = model(x, edge_index, edge_attr)
        assert out.shape == (N, 2)

    def test_predict_prob_range(self):
        """predict 返回 [0, 1] 概率"""
        model = EdgeAwareGCN(
            in_channels=F, hidden_channels=HIDDEN_DIM,
            edge_dim=EDGE_DIM, num_classes=2,
        )
        x, edge_index, edge_attr = _make_graph()
        probs = model.predict(x, edge_index, edge_attr)
        assert probs.shape == (N,)
        assert torch.all(probs >= 0) and torch.all(probs <= 1)

    def test_predict_with_edge_importance(self):
        """返回 probabilities 和 edge_importance"""
        model = EdgeAwareGCN(
            in_channels=F, hidden_channels=HIDDEN_DIM,
            edge_dim=EDGE_DIM, num_classes=2,
        )
        x, edge_index, edge_attr = _make_graph()
        result = model.predict_with_edge_importance(x, edge_index, edge_attr)
        assert "probabilities" in result
        assert "edge_importance" in result
        assert result["probabilities"].shape == (N,)
        assert result["edge_importance"].shape == (E,)


# ============ 4. EdgeAwareGAT ============


class TestEdgeAwareGAT:
    def test_forward_shape(self):
        """输出 [N, num_classes]"""
        model = EdgeAwareGAT(
            in_channels=F, hidden_channels=HIDDEN_DIM,
            edge_dim=EDGE_DIM, num_classes=2, heads=2,
        )
        x, edge_index, edge_attr = _make_graph()
        with torch.no_grad():
            out = model(x, edge_index, edge_attr)
        assert out.shape == (N, 2)

    def test_forward_with_attention(self):
        """return_attention=True 时返回注意力权重"""
        model = EdgeAwareGAT(
            in_channels=F, hidden_channels=HIDDEN_DIM,
            edge_dim=EDGE_DIM, num_classes=2, heads=2,
        )
        x, edge_index, edge_attr = _make_graph()
        with torch.no_grad():
            logits, attn = model(x, edge_index, edge_attr, return_attention=True)
        assert "layer1" in attn
        assert "layer2" in attn
        assert attn["layer1"].shape[0] == E

    def test_predict_prob_range(self):
        """predict 返回 [0, 1] 概率"""
        model = EdgeAwareGAT(
            in_channels=F, hidden_channels=HIDDEN_DIM,
            edge_dim=EDGE_DIM, num_classes=2, heads=2,
        )
        x, edge_index, edge_attr = _make_graph()
        probs = model.predict(x, edge_index, edge_attr)
        assert probs.shape == (N,)
        assert torch.all(probs >= 0) and torch.all(probs <= 1)

    def test_predict_with_attention(self):
        """返回概率和注意力权重"""
        model = EdgeAwareGAT(
            in_channels=F, hidden_channels=HIDDEN_DIM,
            edge_dim=EDGE_DIM, num_classes=2, heads=2,
        )
        x, edge_index, edge_attr = _make_graph()
        result = model.predict_with_attention(x, edge_index, edge_attr)
        assert "probabilities" in result
        assert "attention" in result
        assert "layer1" in result["attention"]
        assert "layer2" in result["attention"]

    def test_attention_weights_saved(self):
        """conv 层保存注意力权重"""
        model = EdgeAwareGAT(
            in_channels=F, hidden_channels=HIDDEN_DIM,
            edge_dim=EDGE_DIM, num_classes=2, heads=2,
        )
        x, edge_index, edge_attr = _make_graph()
        with torch.no_grad():
            model(x, edge_index, edge_attr)
        assert model.conv1.get_attention_weights() is not None
        assert model.conv2.get_attention_weights() is not None


# ============ 5. create_edge_gnn 工厂函数 ============


class TestCreateEdgeGNN:
    def test_create_gcn(self):
        """创建 edge_aware_gcn"""
        model = create_edge_gnn(
            "edge_aware_gcn",
            in_channels=F, hidden_channels=HIDDEN_DIM,
            edge_dim=EDGE_DIM,
        )
        assert isinstance(model, EdgeAwareGCN)

    def test_create_gat(self):
        """创建 edge_aware_gat"""
        model = create_edge_gnn(
            "edge_aware_gat",
            in_channels=F, hidden_channels=HIDDEN_DIM,
            edge_dim=EDGE_DIM,
        )
        assert isinstance(model, EdgeAwareGAT)

    def test_create_invalid_type(self):
        """无效模型类型抛 ValueError"""
        with pytest.raises(ValueError, match="不支持的模型类型"):
            create_edge_gnn("invalid_type")

    def test_is_edge_gnn_available(self):
        """is_edge_gnn_available 返回布尔值"""
        result = is_edge_gnn_available()
        assert isinstance(result, bool)
