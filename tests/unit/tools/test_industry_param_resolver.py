"""
行业差异化参数解析器测试

覆盖:
- IndustryProfile 数据结构与序列化
- deep_merge_params 深合并正确性
- IndustryParamRegistry 注册/查询/删除/解析
- 行业字段提取
- 持久化与版本递增
- 边界条件（空值、未知行业、非法输入）
- 便利函数 run_rules_with_industry（集成 RuleTuner）
"""
import copy
import json
import os
import tempfile

import pytest

from tools.industry_param_resolver import (
    DEFAULT_INDUSTRY,
    INDUSTRY_FIELD_CANDIDATES,
    IndustryProfile,
    IndustryParamRegistry,
    deep_merge_params,
    extract_industry,
    run_rules_with_industry,
    _safe_industry_name,
)


# ============================================================
# _safe_industry_name
# ============================================================
def test_safe_name_basic():
    """安全名称保留字母数字和连字符"""
    assert _safe_industry_name("real_estate") == "real_estate"
    assert _safe_industry_name("jewelry-1") == "jewelry-1"


def test_safe_name_chinese():
    """安全名称保留中文"""
    assert _safe_industry_name("房地产") == "房地产"


def test_safe_name_strips_path_chars():
    """安全名称剔除路径分隔符等危险字符"""
    safe = _safe_industry_name("../etc/passwd")
    assert "/" not in safe
    assert ".." not in safe or safe == "etcpasswd"


def test_safe_name_empty():
    """空输入返回空字符串"""
    assert _safe_industry_name("") == ""
    assert _safe_industry_name(None) == ""
    assert _safe_industry_name("   ") == ""


# ============================================================
# deep_merge_params
# ============================================================
def test_deep_merge_basic():
    """基本组级合并"""
    base = {"smurfing": {"min_count": 5, "risk_score": 70}}
    override = {"smurfing": {"min_count": 8}}
    merged = deep_merge_params(base, override)
    assert merged["smurfing"]["min_count"] == 8  # 覆盖
    assert merged["smurfing"]["risk_score"] == 70  # 保留


def test_deep_merge_no_mutation():
    """合并不修改 base 和 override"""
    base = {"smurfing": {"min_count": 5}}
    override = {"smurfing": {"min_count": 8}}
    base_copy = copy.deepcopy(base)
    override_copy = copy.deepcopy(override)
    deep_merge_params(base, override)
    assert base == base_copy
    assert override == override_copy


def test_deep_merge_new_group():
    """override 引入新组"""
    base = {"smurfing": {"min_count": 5}}
    override = {"large_amount": {"threshold": 200000}}
    merged = deep_merge_params(base, override)
    assert merged["smurfing"]["min_count"] == 5
    assert merged["large_amount"]["threshold"] == 200000


def test_deep_merge_empty_override():
    """空 override 返回 base 深拷贝"""
    base = {"smurfing": {"min_count": 5}}
    merged = deep_merge_params(base, {})
    assert merged == base
    assert merged is not base
    merged["smurfing"]["min_count"] = 99
    assert base["smurfing"]["min_count"] == 5  # base 不受影响


def test_deep_merge_empty_base():
    """空 base 返回 override 深拷贝"""
    override = {"smurfing": {"min_count": 8}}
    merged = deep_merge_params({}, override)
    assert merged == override
    assert merged is not override


def test_deep_merge_none_base():
    """None base 当作空字典"""
    override = {"smurfing": {"min_count": 8}}
    merged = deep_merge_params(None, override)
    assert merged == override


# ============================================================
# IndustryProfile
# ============================================================
def test_profile_creation():
    """画像创建"""
    p = IndustryProfile(
        industry="real_estate",
        param_overrides={"large_amount": {"threshold": 500000}},
        description="房地产行业",
        reason="房地产单笔金额大，需提高大额阈值",
    )
    assert p.industry == "real_estate"
    assert p.description == "房地产行业"
    assert p.reason == "房地产单笔金额大，需提高大额阈值"
    assert p.version == 1
    assert p.param_overrides == {"large_amount": {"threshold": 500000}}


def test_profile_roundtrip():
    """画像序列化与反序列化"""
    p = IndustryProfile(
        industry="jewelry",
        param_overrides={"large_amount": {"threshold": 300000}},
        reason="珠宝行业单笔金额较高",
        description="珠宝行业",
    )
    d = p.to_dict()
    p2 = IndustryProfile.from_dict(d)
    assert p2.industry == p.industry
    assert p2.reason == p.reason
    assert p2.param_overrides == p.param_overrides
    assert p2.version == p.version


def test_profile_empty_reason_rejected():
    """戒律 M2: 空理由拒绝创建"""
    with pytest.raises(ValueError, match="理由"):
        IndustryProfile(
            industry="real_estate",
            param_overrides={"large_amount": {"threshold": 500000}},
            reason="",
        )
    with pytest.raises(ValueError, match="理由"):
        IndustryProfile(
            industry="real_estate",
            param_overrides={"large_amount": {"threshold": 500000}},
            reason="   ",
        )


def test_profile_empty_overrides_rejected():
    """空参数覆盖拒绝创建"""
    with pytest.raises(ValueError):
        IndustryProfile(
            industry="real_estate",
            param_overrides={},
            reason="测试理由",
        )


def test_profile_invalid_name_rejected():
    """非法行业标识（清理后为空）拒绝创建"""
    with pytest.raises(ValueError, match="无效"):
        IndustryProfile(
            industry="!!!",
            param_overrides={"large_amount": {"threshold": 500000}},
            reason="测试",
        )


def test_profile_param_overrides_deep_copied():
    """画像参数覆盖深拷贝，外部修改不影响内部"""
    overrides = {"large_amount": {"threshold": 500000}}
    p = IndustryProfile(
        industry="real_estate",
        param_overrides=overrides,
        reason="测试",
    )
    overrides["large_amount"]["threshold"] = 999999
    assert p.param_overrides["large_amount"]["threshold"] == 500000


# ============================================================
# IndustryParamRegistry - 注册与查询
# ============================================================
def test_register_and_get_profile():
    """注册并获取画像"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        p = reg.register_profile(
            industry="real_estate",
            param_overrides={"large_amount": {"threshold": 500000}},
            reason="房地产金额大",
            description="房地产行业",
        )
        assert p.version == 1
        fetched = reg.get_profile("real_estate")
        assert fetched is not None
        assert fetched.industry == "real_estate"
        assert fetched.param_overrides == {"large_amount": {"threshold": 500000}}


def test_register_update_increments_version():
    """更新画像时版本递增，保留 created_at"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        p1 = reg.register_profile(
            industry="real_estate",
            param_overrides={"large_amount": {"threshold": 500000}},
            reason="v1理由",
        )
        p2 = reg.register_profile(
            industry="real_estate",
            param_overrides={"large_amount": {"threshold": 600000}},
            reason="v2理由",
            description="更新后",
        )
        assert p2.version == p1.version + 1
        assert p2.created_at == p1.created_at
        assert p2.updated_at != p1.created_at
        fetched = reg.get_profile("real_estate")
        assert fetched.version == 2
        assert fetched.param_overrides["large_amount"]["threshold"] == 600000


def test_get_profile_unknown_returns_none():
    """未知行业返回 None"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        assert reg.get_profile("nonexistent") is None


def test_list_profiles():
    """列出所有画像"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        reg.register_profile(
            industry="jewelry",
            param_overrides={"large_amount": {"threshold": 300000}},
            reason="珠宝",
        )
        reg.register_profile(
            industry="real_estate",
            param_overrides={"large_amount": {"threshold": 500000}},
            reason="房地产",
        )
        profiles = reg.list_profiles()
        assert len(profiles) == 2
        # 按行业名排序
        assert profiles[0]["industry"] == "jewelry"
        assert profiles[1]["industry"] == "real_estate"
        assert profiles[0]["override_group_count"] == 1


def test_delete_profile():
    """删除画像"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        reg.register_profile(
            industry="real_estate",
            param_overrides={"large_amount": {"threshold": 500000}},
            reason="房地产",
        )
        assert reg.delete_profile("real_estate") is True
        assert reg.get_profile("real_estate") is None
        # 再次删除返回 False
        assert reg.delete_profile("real_estate") is False


def test_persistence_across_instances():
    """画像持久化跨实例"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg1 = IndustryParamRegistry(storage_dir=tmpdir)
        reg1.register_profile(
            industry="real_estate",
            param_overrides={"large_amount": {"threshold": 500000}},
            reason="房地产",
        )
        # 新实例加载同一目录
        reg2 = IndustryParamRegistry(storage_dir=tmpdir)
        fetched = reg2.get_profile("real_estate")
        assert fetched is not None
        assert fetched.param_overrides["large_amount"]["threshold"] == 500000


def test_atomic_write_no_tmp_left():
    """原子写入不残留 .tmp 文件"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        reg.register_profile(
            industry="real_estate",
            param_overrides={"large_amount": {"threshold": 500000}},
            reason="房地产",
        )
        files = os.listdir(tmpdir)
        assert not any(f.endswith(".tmp") for f in files)


# ============================================================
# IndustryParamRegistry - 参数解析
# ============================================================
def test_get_effective_params_known_industry():
    """已知行业返回合并后的参数"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        reg.register_profile(
            industry="real_estate",
            param_overrides={"large_amount": {"threshold": 500000}},
            reason="房地产",
        )
        base = {
            "smurfing": {"min_count": 5, "risk_score": 70},
            "large_amount": {"threshold": 100000, "risk_score": 40},
        }
        effective = reg.get_effective_params("real_estate", base)
        assert effective["smurfing"]["min_count"] == 5  # 保留
        assert effective["smurfing"]["risk_score"] == 70  # 保留
        assert effective["large_amount"]["threshold"] == 500000  # 覆盖
        assert effective["large_amount"]["risk_score"] == 40  # 保留


def test_get_effective_params_unknown_industry():
    """未知行业回退到基线参数"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        base = {"smurfing": {"min_count": 5}}
        effective = reg.get_effective_params("nonexistent", base)
        assert effective == base
        assert effective is not base  # 深拷贝


def test_get_effective_params_no_mutation():
    """有效参数不修改 base"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        reg.register_profile(
            industry="real_estate",
            param_overrides={"large_amount": {"threshold": 500000}},
            reason="房地产",
        )
        base = {"large_amount": {"threshold": 100000}}
        base_copy = copy.deepcopy(base)
        effective = reg.get_effective_params("real_estate", base)
        assert base == base_copy
        effective["large_amount"]["threshold"] = 999
        assert base["large_amount"]["threshold"] == 100000


def test_get_effective_params_none_base():
    """None base 当作空字典"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        reg.register_profile(
            industry="real_estate",
            param_overrides={"large_amount": {"threshold": 500000}},
            reason="房地产",
        )
        effective = reg.get_effective_params("real_estate", None)
        assert effective == {"large_amount": {"threshold": 500000}}


# ============================================================
# 按交易分组解析
# ============================================================
def test_resolve_params_for_transactions():
    """按交易行业分组解析参数"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        reg.register_profile(
            industry="real_estate",
            param_overrides={"large_amount": {"threshold": 500000}},
            reason="房地产",
        )
        base = {"large_amount": {"threshold": 100000}}
        txns = [
            {"transaction_id": "T1", "industry": "real_estate"},
            {"transaction_id": "T2", "industry": "jewelry"},
            {"transaction_id": "T3"},  # 无行业字段
        ]
        grouped = reg.resolve_params_for_transactions(txns, base)
        # real_estate 已配置，jewelry 未配置回退基线，default 始终存在
        assert "real_estate" in grouped
        assert "jewelry" in grouped
        assert DEFAULT_INDUSTRY in grouped
        assert grouped["real_estate"]["large_amount"]["threshold"] == 500000
        assert grouped["jewelry"]["large_amount"]["threshold"] == 100000
        assert grouped[DEFAULT_INDUSTRY]["large_amount"]["threshold"] == 100000


def test_group_transactions_by_industry():
    """按行业分组交易"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        txns = [
            {"transaction_id": "T1", "industry": "real_estate"},
            {"transaction_id": "T2", "industry": "real_estate"},
            {"transaction_id": "T3", "industry": "jewelry"},
            {"transaction_id": "T4"},
        ]
        grouped = reg.group_transactions_by_industry(txns)
        assert len(grouped["real_estate"]) == 2
        assert len(grouped["jewelry"]) == 1
        assert len(grouped[DEFAULT_INDUSTRY]) == 1


# ============================================================
# extract_industry
# ============================================================
def test_extract_industry_priority():
    """字段优先级: industry > from_account_industry"""
    txn = {
        "industry": "real_estate",
        "from_account_industry": "jewelry",
    }
    assert extract_industry(txn) == "real_estate"


def test_extract_industry_fallback_fields():
    """缺失 industry 时回退到 from_account_industry"""
    txn = {"from_account_industry": "jewelry"}
    assert extract_industry(txn) == "jewelry"


def test_extract_industry_to_account():
    """回退到 to_account_industry"""
    txn = {"to_account_industry": "retail"}
    assert extract_industry(txn) == "retail"


def test_extract_industry_business_type():
    """回退到 business_type"""
    txn = {"business_type": "wholesale"}
    assert extract_industry(txn) == "wholesale"


def test_extract_industry_missing():
    """缺失所有行业字段返回 None"""
    txn = {"transaction_id": "T1", "amount": 100}
    assert extract_industry(txn) is None


def test_extract_industry_empty_string():
    """空字符串行业字段跳过"""
    txn = {"industry": "", "from_account_industry": "jewelry"}
    assert extract_industry(txn) == "jewelry"


def test_extract_industry_none_input():
    """None 或非字典输入返回 None"""
    assert extract_industry(None) is None
    assert extract_industry("not a dict") is None
    assert extract_industry({}) is None


def test_extract_industry_safe_name():
    """行业字段经过安全名称处理"""
    txn = {"industry": "real estate!!!"}
    result = extract_industry(txn)
    assert result is not None
    assert "!" not in result


def test_extract_industry_no_mutation():
    """不修改原交易"""
    txn = {"industry": "real_estate", "amount": 100}
    txn_copy = copy.deepcopy(txn)
    extract_industry(txn)
    assert txn == txn_copy


# ============================================================
# validate_overrides
# ============================================================
def test_validate_overrides_valid():
    """合法覆盖参数通过校验"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        is_valid, errors = reg.validate_overrides(
            {"large_amount": {"threshold": 200000}}
        )
        assert is_valid is True
        assert errors == []


def test_validate_overrides_invalid():
    """非法覆盖参数（超范围）被检出"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        is_valid, errors = reg.validate_overrides(
            {"large_amount": {"threshold": 999999999}}
        )
        assert is_valid is False
        assert any("large_amount" in e for e in errors)


# ============================================================
# 便利函数 run_rules_with_industry（集成 RuleTuner）
# ============================================================
def test_run_rules_with_industry_integration():
    """便利函数能正常运行规则引擎并返回命中结果"""
    from tools.rule_tuner import RuleTuner

    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        # 注册行业画像：提高大额阈值，使大额交易规则不命中
        reg.register_profile(
            industry="real_estate",
            param_overrides={"large_amount": {"threshold": 1000000}},
            reason="房地产单笔金额大",
        )
        tuner = RuleTuner()
        # 构造一笔大额交易（默认阈值 100000 会命中，行业阈值 1000000 不命中）
        txns = [
            {
                "transaction_id": "TXN001",
                "from_account": "A",
                "to_account": "B",
                "amount": 200000,
                "timestamp": "2026-07-28T10:00:00",
                "transaction_type": "transfer",
                "remark": "",
            }
        ]
        base_params = tuner.get_tunable_params()
        # 默认参数下应命中大额交易
        hits_default = run_rules_with_industry(reg, tuner, txns, DEFAULT_INDUSTRY, base_params)
        assert len(hits_default.get("大额交易", [])) == 1
        # 房地产行业参数下不应命中大额交易
        hits_re = run_rules_with_industry(reg, tuner, txns, "real_estate", base_params)
        assert len(hits_re.get("大额交易", [])) == 0


def test_run_rules_with_industry_unknown_industry_uses_base():
    """未知行业使用基线参数（等价于默认行为）"""
    from tools.rule_tuner import RuleTuner

    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        tuner = RuleTuner()
        txns = [
            {
                "transaction_id": "TXN001",
                "from_account": "A",
                "to_account": "B",
                "amount": 200000,
                "timestamp": "2026-07-28T10:00:00",
                "transaction_type": "transfer",
                "remark": "",
            }
        ]
        base_params = tuner.get_tunable_params()
        hits = run_rules_with_industry(reg, tuner, txns, "nonexistent", base_params)
        assert len(hits.get("大额交易", [])) == 1


# ============================================================
# 损坏文件恢复
# ============================================================
def test_get_profile_corrupted_file_returns_none():
    """损坏的画像文件返回 None 而非抛异常"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = IndustryParamRegistry(storage_dir=tmpdir)
        path = reg._profile_path("corrupted")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{invalid json")
        assert reg.get_profile("corrupted") is None
