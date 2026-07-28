"""
多机构联邦分析协调器测试 (B3-3)

覆盖:
- FedAvg 聚合正确性（数值/列表参数）
- 差分隐私脱敏（账户哈希/风险分加噪）
- 跨机构案件 ID 映射
- 审计日志同步脱敏
- 异常机构参数被拒绝（戒律 P1）
- 单机构失败不影响其他（戒律 P4）
- 最少参与机构校验
"""
import pytest

from tools.federation_coordinator import (
    FederationCoordinator,
    _laplace_noise,
    _hash_identifier,
    create_federation_coordinator,
)


# ============================================================
# 测试夹具
# ============================================================
@pytest.fixture
def coordinator():
    return FederationCoordinator(institution_id="BANK_A")


@pytest.fixture
def local_params_list():
    """两个机构的本地参数"""
    return [
        {
            "institution_id": "BANK_A",
            "params": {
                "layer1_weight": 0.5,
                "layer1_bias": [0.1, 0.2, 0.3],
                "learning_rate": 0.01,
            },
            "sample_count": 100,
            "model_version": "1.0.0",
        },
        {
            "institution_id": "BANK_B",
            "params": {
                "layer1_weight": 0.7,
                "layer1_bias": [0.3, 0.4, 0.5],
                "learning_rate": 0.02,
            },
            "sample_count": 200,
            "model_version": "1.0.0",
        },
    ]


# ============================================================
# 辅助函数测试
# ============================================================
def test_laplace_noise_returns_float():
    """拉普拉斯噪声返回浮点数"""
    noise = _laplace_noise(sensitivity=1.0, epsilon=1.0)
    assert isinstance(noise, float)


def test_laplace_noise_zero_epsilon_returns_zero():
    """epsilon=0 返回 0"""
    assert _laplace_noise(sensitivity=1.0, epsilon=0) == 0.0


def test_laplace_noise_bounded():
    """噪声在合理范围内（敏感度/epsilon 的数倍内）"""
    for _ in range(100):
        noise = _laplace_noise(sensitivity=1.0, epsilon=1.0)
        # 拉普拉斯分布 99% 概率在 ±5 倍 scale 内
        assert abs(noise) < 20


def test_hash_identifier_returns_16_chars():
    """哈希返回 16 字符"""
    h = _hash_identifier("ACC001")
    assert len(h) == 16
    assert isinstance(h, str)


def test_hash_identifier_with_salt_differs():
    """不同 salt 产生不同哈希"""
    h1 = _hash_identifier("ACC001", salt="BANK_A")
    h2 = _hash_identifier("ACC001", salt="BANK_B")
    assert h1 != h2


def test_hash_identifier_same_input_same_output():
    """相同输入产生相同哈希（确定性）"""
    h1 = _hash_identifier("ACC001", salt="X")
    h2 = _hash_identifier("ACC001", salt="X")
    assert h1 == h2


def test_hash_identifier_empty_returns_empty():
    """空输入返回空"""
    assert _hash_identifier("") == ""
    assert _hash_identifier(None) == ""


# ============================================================
# FedAvg 聚合测试
# ============================================================
def test_aggregate_gnn_params_returns_dict(coordinator, local_params_list):
    """聚合返回字典结构"""
    result = coordinator.aggregate_gnn_params(local_params_list)
    assert "global_params" in result
    assert "aggregation_strategy" in result
    assert "participant_count" in result
    assert "total_samples" in result
    assert "aggregated_at" in result
    assert "coordinator_version" in result
    assert "rejected" in result


def test_aggregate_gnn_params_participant_count(coordinator, local_params_list):
    """参与机构数正确"""
    result = coordinator.aggregate_gnn_params(local_params_list)
    assert result["participant_count"] == 2


def test_aggregate_gnn_params_total_samples(coordinator, local_params_list):
    """总样本数正确"""
    result = coordinator.aggregate_gnn_params(local_params_list)
    assert result["total_samples"] == 300  # 100 + 200


def test_aggregate_gnn_params_weighted_average_numeric(coordinator, local_params_list):
    """数值参数加权平均正确"""
    result = coordinator.aggregate_gnn_params(local_params_list)
    # layer1_weight: (0.5*100 + 0.7*200) / 300 = (50+140)/300 = 0.6333...
    assert abs(result["global_params"]["layer1_weight"] - 0.6333) < 0.01


def test_aggregate_gnn_params_weighted_average_list(coordinator, local_params_list):
    """列表参数加权平均正确"""
    result = coordinator.aggregate_gnn_params(local_params_list)
    bias = result["global_params"]["layer1_bias"]
    # bias[0]: (0.1*100 + 0.3*200) / 300 = (10+60)/300 = 0.2333
    assert abs(bias[0] - 0.2333) < 0.01
    assert len(bias) == 3


def test_aggregate_gnn_params_empty_list_returns_empty(coordinator):
    """空列表返回空结果"""
    result = coordinator.aggregate_gnn_params([])
    assert result["participant_count"] == 0
    assert result["global_params"] == {}


def test_aggregate_gnn_params_min_participants(coordinator):
    """少于最小参与机构数被拒绝"""
    coordinator.min_participants = 3
    result = coordinator.aggregate_gnn_params([
        {"institution_id": "A", "params": {"w": 1.0}, "sample_count": 10},
    ])
    assert result["participant_count"] == 0
    assert len(result["rejected"]) > 0


def test_aggregate_gnn_params_rejects_invalid_params(coordinator, local_params_list):
    """异常参数机构被拒绝（戒律 P1）"""
    params_with_invalid = local_params_list + [
        {"institution_id": "BAD_BANK", "params": None, "sample_count": 50},
    ]
    result = coordinator.aggregate_gnn_params(params_with_invalid)
    assert "BAD_BANK" in result["rejected"]
    # 正常机构仍参与
    assert result["participant_count"] == 2


def test_aggregate_gnn_params_skips_non_dict_entries(coordinator, local_params_list):
    """非字典条目被跳过（戒律 P4）"""
    params_with_garbage = local_params_list + ["invalid", 123, None]
    result = coordinator.aggregate_gnn_params(params_with_garbage)
    assert result["participant_count"] == 2


def test_aggregate_gnn_params_records_participants(coordinator, local_params_list):
    """聚合记录参与机构信息（戒律 M4）"""
    result = coordinator.aggregate_gnn_params(local_params_list)
    assert "participants" in result
    assert len(result["participants"]) == 2
    inst_ids = [p["institution_id"] for p in result["participants"]]
    assert "BANK_A" in inst_ids
    assert "BANK_B" in inst_ids


# ============================================================
# 制裁信号脱敏共享测试
# ============================================================
def test_share_sanction_signals_hashes_account(coordinator):
    """账户号被哈希脱敏"""
    signals = [
        {"account": "ACC001", "country": "AE", "risk_score": 85,
         "detected_at": "2026-07-27 10:00:00"},
    ]
    result = coordinator.share_sanction_signals(signals)
    assert result["signal_count"] == 1
    shared = result["shared_signals"][0]
    # 账户号应被哈希，不是原始值
    assert shared["hashed_account"] != "ACC001"
    assert len(shared["hashed_account"]) == 16


def test_share_sanction_signals_adds_noise_to_risk(coordinator):
    """风险分加差分隐私噪声"""
    signals = [
        {"account": "ACC001", "country": "AE", "risk_score": 80,
         "detected_at": "2026-07-27"},
    ]
    # 多次运行，风险分应有波动
    risks = set()
    for _ in range(20):
        result = coordinator.share_sanction_signals(signals)
        risks.add(result["shared_signals"][0]["risk_score"])
    # 至少有 2 个不同值（噪声导致）
    assert len(risks) >= 2


def test_share_sanction_signals_risk_in_range(coordinator):
    """噪声后风险分仍在 0-100 范围"""
    signals = [
        {"account": "ACC001", "risk_score": 99, "country": "AE",
         "detected_at": "2026-07-27"},
    ]
    for _ in range(50):
        result = coordinator.share_sanction_signals(signals)
        risk = result["shared_signals"][0]["risk_score"]
        assert 0 <= risk <= 100


def test_share_sanction_signals_includes_source(coordinator):
    """信号包含来源机构（戒律 M2）"""
    signals = [
        {"account": "ACC001", "risk_score": 85, "country": "AE",
         "detected_at": "2026-07-27"},
    ]
    result = coordinator.share_sanction_signals(signals)
    assert result["shared_signals"][0]["source_institution"] == "BANK_A"


def test_share_sanction_signals_empty_returns_empty(coordinator):
    """空信号列表返回空"""
    result = coordinator.share_sanction_signals([])
    assert result["signal_count"] == 0
    assert result["shared_signals"] == []


def test_share_sanction_signals_skips_invalid(coordinator):
    """非法信号被跳过（戒律 P4）"""
    signals = [
        {"account": "ACC001", "risk_score": 80, "country": "AE",
         "detected_at": "2026-07-27"},
        "invalid",
        None,
        {"account": "ACC002", "risk_score": 90, "country": "IR",
         "detected_at": "2026-07-27"},
    ]
    result = coordinator.share_sanction_signals(signals)
    assert result["signal_count"] == 2


def test_share_sanction_signals_confidence_by_risk(coordinator):
    """置信度按风险分判定"""
    signals = [
        {"account": "ACC001", "risk_score": 90, "country": "AE",
         "detected_at": "2026-07-27"},
    ]
    result = coordinator.share_sanction_signals(signals)
    # risk 90 + 噪声，大概率 ≥80 → high
    # 由于噪声，置信度可能 high 或 medium，但应为两者之一
    assert result["shared_signals"][0]["confidence"] in ("high", "medium")


def test_share_sanction_signals_privacy_budget(coordinator):
    """隐私预算累计"""
    signals = [
        {"account": "ACC001", "risk_score": 80, "country": "AE",
         "detected_at": "2026-07-27"},
        {"account": "ACC002", "risk_score": 70, "country": "IR",
         "detected_at": "2026-07-27"},
    ]
    result = coordinator.share_sanction_signals(signals)
    # 每条消耗 epsilon，2条应消耗 2*epsilon
    assert result["privacy_budget_used"] > 0


# ============================================================
# 案件 ID 映射测试
# ============================================================
def test_map_case_returns_hash(coordinator):
    """案件映射返回哈希"""
    global_id = coordinator.map_cross_institution_case("CASE_001")
    assert len(global_id) == 16
    assert global_id != "CASE_001"


def test_map_case_deterministic(coordinator):
    """相同输入相同输出"""
    g1 = coordinator.map_cross_institution_case("CASE_001")
    g2 = coordinator.map_cross_institution_case("CASE_001")
    assert g1 == g2


def test_map_case_different_institutions_differ():
    """不同机构映射不同"""
    c1 = FederationCoordinator(institution_id="BANK_A")
    c2 = FederationCoordinator(institution_id="BANK_B")
    g1 = c1.map_cross_institution_case("CASE_001")
    g2 = c2.map_cross_institution_case("CASE_001")
    assert g1 != g2


def test_map_case_empty_returns_empty(coordinator):
    """空案件ID返回空"""
    assert coordinator.map_cross_institution_case("") == ""
    assert coordinator.map_cross_institution_case(None) == ""


def test_lookup_case_mapping_returns_mapped(coordinator):
    """查询已建立的映射"""
    global_id = coordinator.map_cross_institution_case("CASE_001")
    looked = coordinator.lookup_case_mapping("CASE_001")
    assert looked == global_id


def test_lookup_case_mapping_not_exists_returns_empty(coordinator):
    """查询不存在的映射返回空"""
    assert coordinator.lookup_case_mapping("NONEXISTENT") == ""


# ============================================================
# 审计日志同步测试
# ============================================================
def test_sync_audit_logs_hashes_account(coordinator):
    """审计日志账户被哈希"""
    logs = [
        {"timestamp": "2026-07-27 10:00:00", "action": "analyze",
         "account": "ACC001", "rule_name": "分拆转账", "risk_score": 75},
    ]
    result = coordinator.sync_audit_logs(logs)
    assert result["synced_count"] == 1
    synced = result["synced_logs"][0]
    assert "hashed_account" in synced
    assert synced["hashed_account"] != "ACC001"
    assert synced["source_institution"] == "BANK_A"


def test_sync_audit_logs_preserves_non_sensitive(coordinator):
    """保留非敏感信息"""
    logs = [
        {"timestamp": "2026-07-27 10:00:00", "action": "analyze",
         "account": "ACC001", "rule_name": "分拆转账",
         "risk_score": 75, "severity": "high",
         "execution_id": "exec_001"},
    ]
    result = coordinator.sync_audit_logs(logs)
    synced = result["synced_logs"][0]
    assert synced["rule_name"] == "分拆转账"
    assert synced["risk_score"] == 75
    assert synced["severity"] == "high"
    assert synced["execution_id"] == "exec_001"


def test_sync_audit_logs_skips_invalid(coordinator):
    """非法日志被跳过"""
    logs = [
        {"timestamp": "2026-07-27", "action": "analyze", "account": "A1"},
        "invalid",
        None,
        123,
        {"timestamp": "2026-07-27", "action": "alert", "account": "A2"},
    ]
    result = coordinator.sync_audit_logs(logs)
    assert result["synced_count"] == 2
    assert result["skipped_count"] == 3


def test_sync_audit_logs_empty_returns_empty(coordinator):
    """空日志返回空"""
    result = coordinator.sync_audit_logs([])
    assert result["synced_count"] == 0
    assert result["synced_logs"] == []


def test_sync_audit_logs_user_field_also_hashed(coordinator):
    """user 字段也被哈希"""
    logs = [
        {"timestamp": "2026-07-27", "action": "login",
         "user": "admin"},
    ]
    result = coordinator.sync_audit_logs(logs)
    synced = result["synced_logs"][0]
    assert "hashed_account" in synced
    assert synced["hashed_account"] != "admin"


# ============================================================
# 联邦状态查询测试
# ============================================================
def test_get_federation_status_returns_dict(coordinator):
    """状态查询返回字典"""
    status = coordinator.get_federation_status()
    assert status["institution_id"] == "BANK_A"
    assert "coordinator_version" in status
    assert "aggregation_strategy" in status
    assert "epsilon" in status
    assert "case_mapping_count" in status
    assert "status_at" in status


def test_get_federation_status_after_operations(coordinator, local_params_list):
    """操作后状态更新"""
    coordinator.aggregate_gnn_params(local_params_list)
    coordinator.map_cross_institution_case("CASE_001")
    status = coordinator.get_federation_status()
    assert status["case_mapping_count"] == 1
    assert status["participants_count"] == 2


# ============================================================
# 便捷函数测试
# ============================================================
def test_create_federation_coordinator():
    """便捷函数创建协调器"""
    coord = create_federation_coordinator("BANK_X")
    assert isinstance(coord, FederationCoordinator)
    assert coord.institution_id == "BANK_X"


# ============================================================
# 异常隔离测试（戒律 P4）
# ============================================================
def test_aggregate_gnn_params_does_not_raise_on_garbage(coordinator):
    """垃圾输入不抛异常"""
    result = coordinator.aggregate_gnn_params("not a list")
    assert isinstance(result, dict)


def test_share_sanction_signals_does_not_raise_on_garbage(coordinator):
    """垃圾输入不抛异常"""
    result = coordinator.share_sanction_signals("not a list")
    assert isinstance(result, dict)
    assert result["signal_count"] == 0


def test_sync_audit_logs_does_not_raise_on_garbage(coordinator):
    """垃圾输入不抛异常"""
    result = coordinator.sync_audit_logs("not a list")
    assert isinstance(result, dict)
    assert result["synced_count"] == 0
