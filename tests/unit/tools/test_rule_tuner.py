"""
规则调参管理器单元测试

覆盖:
- 参数读取（get_tunable_params / get_defaults）
- 参数校验（validate_params / 戒律守护）
- 效果对比（compare_effect / 真实交易数据 / 戒律警告）
- 配置持久化（save / load / list / delete）
- 应用与重置（apply_config / reset_to_defaults）
- 临时配置隔离（不影响全局 AML_CONFIG）
"""
import os
import sys
import json
import copy
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.rule_tuner import RuleTuner, HIGH_RISK_SCORE_THRESHOLD
from config import AML_CONFIG
from graph.state import Transaction


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def tuner(tmp_path):
    """使用临时目录的 RuleTuner"""
    return RuleTuner(storage_dir=str(tmp_path))


@pytest.fixture
def original_config():
    """保留并恢复 AML_CONFIG 全局状态"""
    import config as cfg_module
    original = copy.deepcopy(cfg_module.AML_CONFIG["rules"])
    yield original
    cfg_module.AML_CONFIG["rules"] = original


def _make_txn(
    tid: str,
    from_acc: str,
    to_acc: str,
    amount: float,
    timestamp: str,
    remark: str = "",
) -> Transaction:
    return {
        "transaction_id": tid,
        "from_account": from_acc,
        "to_account": to_acc,
        "amount": amount,
        "timestamp": timestamp,
        "transaction_type": "transfer",
        "remark": remark,
    }


@pytest.fixture
def sample_transactions():
    """真实模式交易数据：包含分拆、快进快出、对敲、大额"""
    txns = []
    # 分拆转账：5 笔 4.5 万转入同一账户
    for i in range(5):
        txns.append(_make_txn(
            f"SMURF_{i}", f"PAYER_{i}", "RECV_A", 45000.0,
            f"2026-07-01T10:{i:02d}:00"
        ))
    # 快进快出：10 万入账 + 9.6 万出账
    txns.append(_make_txn("FIFO_IN", "PAYER_X", "ACC_Y", 100000.0, "2026-07-01T11:00:00"))
    txns.append(_make_txn("FIFO_OUT", "ACC_Y", "PAYER_Z", 96000.0, "2026-07-01T11:05:00"))
    # 大额交易
    txns.append(_make_txn("LARGE_1", "ACC_A", "ACC_B", 150000.0, "2026-07-01T12:00:00"))
    return txns


# ============================================================
# 参数读取
# ============================================================
class TestGetParams:
    def test_get_tunable_params_returns_all_groups(self, tuner):
        """获取当前参数应包含所有可调组"""
        params = tuner.get_tunable_params()
        for group in RuleTuner.TUNABLE_SCHEMA:
            assert group in params

    def test_get_tunable_params_values_match_config(self, tuner):
        """当前参数值应与 AML_CONFIG 一致"""
        params = tuner.get_tunable_params()
        assert params["smurfing"]["hour_window"] == AML_CONFIG["rules"]["smurfing"]["hour_window"]
        assert params["large_amount"]["threshold"] == AML_CONFIG["rules"]["large_amount"]["threshold"]

    def test_get_defaults_returns_schema_defaults(self, tuner):
        """默认值应来自 schema"""
        defaults = tuner.get_defaults()
        assert defaults["smurfing"]["hour_window"] == 1
        assert defaults["smurfing"]["min_count"] == 5
        assert defaults["large_amount"]["threshold"] == 100000

    def test_get_param_metadata_includes_label_and_specs(self, tuner):
        """元数据包含 label 和 params 规格"""
        meta = tuner.get_param_metadata()
        assert meta["smurfing"]["label"] == "分拆转账"
        assert "hour_window" in meta["smurfing"]["params"]
        assert meta["smurfing"]["params"]["hour_window"]["type"] == "int"


# ============================================================
# 参数校验
# ============================================================
class TestValidateParams:
    def test_valid_params_pass(self, tuner):
        """正常参数校验通过"""
        params = tuner.get_defaults()
        is_valid, errors, warnings = tuner.validate_params(params)
        assert is_valid is True
        assert errors == []
        assert warnings == []

    def test_unknown_group_rejected(self, tuner):
        """未知参数组应报错"""
        params = {"unknown_group": {"foo": 1}}
        is_valid, errors, _ = tuner.validate_params(params)
        assert is_valid is False
        assert any("未知参数组" in e for e in errors)

    def test_unknown_param_rejected(self, tuner):
        """未知参数应报错"""
        params = {"smurfing": {"unknown_field": 1}}
        is_valid, errors, _ = tuner.validate_params(params)
        assert is_valid is False
        assert any("未知参数" in e for e in errors)

    def test_below_min_rejected(self, tuner):
        """低于最小值应报错"""
        params = {"smurfing": {"min_count": 1}}  # min=2
        is_valid, errors, _ = tuner.validate_params(params)
        assert is_valid is False
        assert any("低于最小值" in e for e in errors)

    def test_above_max_rejected(self, tuner):
        """超过最大值应报错"""
        params = {"smurfing": {"min_count": 100}}  # max=20
        is_valid, errors, _ = tuner.validate_params(params)
        assert is_valid is False
        assert any("超过最大值" in e for e in errors)

    def test_wrong_type_rejected(self, tuner):
        """类型错误应报错"""
        params = {"smurfing": {"min_count": "five"}}
        is_valid, errors, _ = tuner.validate_params(params)
        assert is_valid is False
        assert any("必须是整数" in e for e in errors)

    def test_bool_rejected_as_int(self, tuner):
        """布尔值不能作为整数"""
        params = {"smurfing": {"min_count": True}}
        is_valid, errors, _ = tuner.validate_params(params)
        assert is_valid is False

    def test_smurfing_amount_low_gt_high_rejected(self, tuner):
        """分拆转账 amount_low > amount_high 应报错"""
        params = {"smurfing": {"amount_low": 50000, "amount_high": 40000}}
        is_valid, errors, _ = tuner.validate_params(params)
        assert is_valid is False
        assert any("amount_low" in e for e in errors)

    def test_high_large_amount_threshold_warns(self, tuner):
        """大额阈值翻倍以上应警告（戒律 P1）"""
        params = {"large_amount": {"threshold": 300000}}  # 默认 100000，3倍
        is_valid, errors, warnings = tuner.validate_params(params)
        assert is_valid is True
        assert any("P1" in w for w in warnings)

    def test_low_fifo_ratio_warns(self, tuner):
        """快进快出占比低于 0.7 应警告（戒律 P2）"""
        params = {"fast_in_fast_out": {"min_ratio": 0.6}}
        is_valid, _, warnings = tuner.validate_params(params)
        assert is_valid is True
        assert any("P2" in w for w in warnings)

    def test_high_smurfing_min_count_warns(self, tuner):
        """分拆转账最小笔数翻倍以上应警告"""
        params = {"smurfing": {"min_count": 10}}  # 默认 5
        is_valid, _, warnings = tuner.validate_params(params)
        assert is_valid is True
        assert any("P1" in w for w in warnings)

    def test_low_risk_score_warns(self, tuner):
        """风险分过低应警告（戒律 M3）"""
        params = {"smurfing": {"risk_score": 20}}
        is_valid, _, warnings = tuner.validate_params(params)
        assert is_valid is True
        assert any("M3" in w for w in warnings)


# ============================================================
# 效果对比（基于真实交易数据）
# ============================================================
class TestCompareEffect:
    def test_compare_returns_before_after_diff(self, tuner, sample_transactions):
        """对比返回 before/after/diff 三段"""
        params = tuner.get_defaults()
        result = tuner.compare_effect(sample_transactions, params)
        assert "before" in result
        assert "after" in result
        assert "diff" in result
        assert "warnings" in result

    def test_compare_same_params_no_diff(self, tuner, sample_transactions):
        """相同参数对比，diff 应为 0"""
        params = tuner.get_tunable_params()
        result = tuner.compare_effect(sample_transactions, params)
        assert result["diff"]["total_hits_delta"] == 0
        assert result["diff"]["high_risk_hits_delta"] == 0
        assert result["warnings"] == []

    def test_compare_strict_threshold_reduces_hits(self, tuner, sample_transactions):
        """提高大额阈值应减少大额交易命中数"""
        # 先记录默认效果
        default_params = tuner.get_defaults()
        before_result = tuner.compare_effect(sample_transactions, default_params)

        # 大额阈值提高到 200000，应该减少大额交易命中
        strict_params = copy.deepcopy(default_params)
        strict_params["large_amount"]["threshold"] = 200000

        result = tuner.compare_effect(sample_transactions, strict_params)
        # 总命中数应下降
        assert result["diff"]["total_hits_delta"] <= 0
        # 大额交易命中数应下降或不变
        assert result["after"]["rule_counts"]["大额交易"] <= result["before"]["rule_counts"]["大额交易"]

    def test_compare_invalid_params_raises(self, tuner, sample_transactions):
        """无效参数应抛 ValueError"""
        params = {"smurfing": {"min_count": 1000}}  # 超出范围
        with pytest.raises(ValueError, match="参数校验失败"):
            tuner.compare_effect(sample_transactions, params)

    def test_high_risk_drop_warning(self, tuner, sample_transactions):
        """调参导致高风险命中数大幅下降时应警告（戒律 P1）"""
        # 严重放宽阈值（让原本命中的高风险交易不再命中）
        params = tuner.get_defaults()
        # 大幅提高大额阈值 + 大幅放宽快进快出
        params["large_amount"]["threshold"] = 10000000  # 几乎不会命中
        params["fast_in_fast_out"]["min_ratio"] = 1.0   # 几乎不会命中
        params["fast_in_fast_out"]["min_amount"] = 1000000  # 几乎不会命中
        # 同时调整风险分到 30 以下，使原本高风险的变为低风险
        params["smurfing"]["risk_score"] = 30  # 默认 70 → 30，不再是高风险

        result = tuner.compare_effect(sample_transactions, params)
        # 应该有警告（高风险命中数下降）
        # 注意：根据实际命中情况，可能有 P1 警告或规则失效警告
        assert len(result["warnings"]) > 0

    def test_rule_invalidation_warning(self, tuner, sample_transactions):
        """调参使原本命中的规则完全失效时应警告"""
        params = tuner.get_defaults()
        # 把大额阈值提到极高，让大额交易规则完全失效
        params["large_amount"]["threshold"] = 10000000

        result = tuner.compare_effect(sample_transactions, params)
        # 如果默认有大额交易命中，应出现"不再命中"警告
        if result["before"]["rule_counts"]["大额交易"] > 0:
            assert any("不再命中" in w for w in result["warnings"])

    def test_temporary_config_does_not_pollute_global(self, tuner, sample_transactions):
        """效果对比不应污染全局 AML_CONFIG"""
        original_threshold = AML_CONFIG["rules"]["large_amount"]["threshold"]
        params = tuner.get_defaults()
        params["large_amount"]["threshold"] = 500000  # 临时改

        tuner.compare_effect(sample_transactions, params)

        # 全局应保持不变
        assert AML_CONFIG["rules"]["large_amount"]["threshold"] == original_threshold


# ============================================================
# 配置持久化
# ============================================================
class TestPersistence:
    def test_save_and_load_config(self, tuner):
        """保存后能加载"""
        params = tuner.get_defaults()
        params["smurfing"]["min_count"] = 8
        path = tuner.save_config("strict_v1", params, description="严格版")

        assert os.path.exists(path)
        loaded = tuner.load_config("strict_v1")
        assert loaded["name"] == "strict_v1"
        assert loaded["description"] == "严格版"
        assert loaded["params"]["smurfing"]["min_count"] == 8

    def test_save_invalid_params_raises(self, tuner):
        """校验失败的参数不应保存"""
        params = {"smurfing": {"min_count": 1000}}
        with pytest.raises(ValueError):
            tuner.save_config("bad", params)

    def test_save_invalid_name_raises(self, tuner):
        """名称无效应报错"""
        params = tuner.get_defaults()
        with pytest.raises(ValueError, match="配置名称无效"):
            tuner.save_config("!!!", params)

    def test_load_nonexistent_raises(self, tuner):
        """加载不存在的配置应报错"""
        with pytest.raises(FileNotFoundError):
            tuner.load_config("nonexistent")

    def test_list_configs_returns_sorted(self, tuner):
        """列表按创建时间倒序"""
        params = tuner.get_defaults()
        tuner.save_config("config_a", params, description="A")
        tuner.save_config("config_b", params, description="B")

        configs = tuner.list_configs()
        assert len(configs) == 2
        # 后保存的在前
        assert configs[0]["name"] == "config_b"

    def test_delete_config(self, tuner):
        """删除配置后无法加载"""
        params = tuner.get_defaults()
        tuner.save_config("to_delete", params)
        assert tuner.delete_config("to_delete") is True
        with pytest.raises(FileNotFoundError):
            tuner.load_config("to_delete")

    def test_delete_nonexistent_returns_false(self, tuner):
        """删除不存在的配置返回 False"""
        assert tuner.delete_config("nonexistent") is False

    def test_save_overwrites_existing(self, tuner):
        """同名配置应覆盖"""
        params = tuner.get_defaults()
        params["smurfing"]["min_count"] = 6
        tuner.save_config("versioned", params)

        params2 = tuner.get_defaults()
        params2["smurfing"]["min_count"] = 10
        tuner.save_config("versioned", params2)

        loaded = tuner.load_config("versioned")
        assert loaded["params"]["smurfing"]["min_count"] == 10


# ============================================================
# 应用与重置
# ============================================================
class TestApplyAndReset:
    def test_apply_config_changes_runtime(self, tuner, original_config):
        """应用配置后 AML_CONFIG 应更新"""
        params = tuner.get_defaults()
        params["smurfing"]["min_count"] = 15

        tuner.apply_config(params)
        assert AML_CONFIG["rules"]["smurfing"]["min_count"] == 15

    def test_apply_invalid_config_raises(self, tuner, original_config):
        """应用无效配置应报错且不修改全局"""
        original = AML_CONFIG["rules"]["smurfing"]["min_count"]
        params = {"smurfing": {"min_count": 1000}}
        with pytest.raises(ValueError):
            tuner.apply_config(params)
        # 应保持原值
        assert AML_CONFIG["rules"]["smurfing"]["min_count"] == original

    def test_reset_to_defaults(self, tuner, original_config):
        """重置后参数应回到默认值"""
        # 先乱改
        params = tuner.get_defaults()
        params["smurfing"]["min_count"] = 20
        tuner.apply_config(params)

        # 重置
        tuner.reset_to_defaults()
        assert AML_CONFIG["rules"]["smurfing"]["min_count"] == 5
        assert AML_CONFIG["rules"]["large_amount"]["threshold"] == 100000


# ============================================================
# 流程集成
# ============================================================
class TestEndToEnd:
    def test_full_workflow(self, tuner, sample_transactions, original_config):
        """完整流程：获取 → 修改 → 校验 → 对比 → 保存 → 加载 → 应用"""
        # 1. 获取当前参数
        params = tuner.get_tunable_params()
        # 2. 修改：放宽大额阈值（让命中减少）
        params["large_amount"]["threshold"] = 200000
        # 3. 校验
        is_valid, errors, warnings = tuner.validate_params(params)
        assert is_valid, f"校验失败: {errors}"
        # 4. 对比效果
        comparison = tuner.compare_effect(sample_transactions, params)
        assert comparison["diff"]["total_hits_delta"] <= 0
        # 5. 保存
        tuner.save_config("e2e_test", params, description="端到端测试")
        # 6. 加载
        loaded = tuner.load_config("e2e_test")
        assert loaded["params"]["large_amount"]["threshold"] == 200000
        # 7. 应用
        tuner.apply_config(loaded["params"])
        assert AML_CONFIG["rules"]["large_amount"]["threshold"] == 200000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
