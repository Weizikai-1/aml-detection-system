"""
结果缓存单元测试 (Task 6-4)

覆盖:
- AnalysisCache 类的核心方法（key计算、读写、过期、容量）
- build_config_snapshot 配置快照提取
- rule_engine 集成（缓存命中/未命中/禁用）
"""
import os
import json
import time
import tempfile
import shutil
from unittest.mock import patch, MagicMock

import pytest

from tools.analysis_cache import AnalysisCache, build_config_snapshot
from agents.rule_engine import create_rule_engine_agent


# ============================================================
# 夹具
# ============================================================
@pytest.fixture()
def cache_dir(tmp_path):
    """临时缓存目录"""
    d = tmp_path / "cache"
    d.mkdir()
    return str(d)


@pytest.fixture()
def sample_transactions():
    """简单交易样本"""
    return [
        {
            "transaction_id": "T001",
            "from_account": "A001",
            "to_account": "A002",
            "amount": 45000.0,
            "timestamp": "2025-01-01 10:00:00",
            "remark": "",
        },
        {
            "transaction_id": "T002",
            "from_account": "A003",
            "to_account": "A002",
            "amount": 48000.0,
            "timestamp": "2025-01-01 10:30:00",
            "remark": "",
        },
    ]


@pytest.fixture()
def sample_config_snapshot():
    """配置快照样本"""
    return {
        "smurfing": {"min_count": 5, "amount_low": 40000},
        "large_amount": {"threshold": 100000},
    }


# ============================================================
# AnalysisCache 基础测试
# ============================================================
@pytest.mark.unit
def test_cache_disabled_returns_none(cache_dir, sample_transactions, sample_config_snapshot):
    """缓存禁用时 get 必返回 None"""
    cache = AnalysisCache(cache_dir=cache_dir, enabled=False)
    key = cache._compute_key(sample_transactions, sample_config_snapshot)
    # 即使写入，禁用状态下也不能读
    cache.set(key, {"rule_hit_count": 5})
    assert cache.get(key) is None


@pytest.mark.unit
def test_cache_key_deterministic(cache_dir, sample_transactions, sample_config_snapshot):
    """相同输入应产生相同key"""
    cache = AnalysisCache(cache_dir=cache_dir, enabled=True)
    key1 = cache._compute_key(sample_transactions, sample_config_snapshot)
    key2 = cache._compute_key(sample_transactions, sample_config_snapshot)
    assert key1 == key2


@pytest.mark.unit
def test_cache_key_invariant_to_order(cache_dir, sample_transactions, sample_config_snapshot):
    """交易顺序不同但内容相同应产生相同key（按ID排序）"""
    cache = AnalysisCache(cache_dir=cache_dir, enabled=True)
    reversed_txns = list(reversed(sample_transactions))
    key1 = cache._compute_key(sample_transactions, sample_config_snapshot)
    key2 = cache._compute_key(reversed_txns, sample_config_snapshot)
    assert key1 == key2


@pytest.mark.unit
def test_cache_key_different_for_different_txns(cache_dir, sample_config_snapshot):
    """不同交易应产生不同key"""
    cache = AnalysisCache(cache_dir=cache_dir, enabled=True)
    txns_a = [{"transaction_id": "T001", "from_account": "A", "to_account": "B",
               "amount": 1000.0, "timestamp": "2025-01-01 10:00:00", "remark": ""}]
    txns_b = [{"transaction_id": "T002", "from_account": "A", "to_account": "B",
               "amount": 1000.0, "timestamp": "2025-01-01 10:00:00", "remark": ""}]
    key_a = cache._compute_key(txns_a, sample_config_snapshot)
    key_b = cache._compute_key(txns_b, sample_config_snapshot)
    assert key_a != key_b


@pytest.mark.unit
def test_cache_key_different_for_different_config(cache_dir, sample_transactions):
    """不同配置应产生不同key"""
    cache = AnalysisCache(cache_dir=cache_dir, enabled=True)
    cfg_a = {"smurfing": {"min_count": 5}}
    cfg_b = {"smurfing": {"min_count": 10}}
    key_a = cache._compute_key(sample_transactions, cfg_a)
    key_b = cache._compute_key(sample_transactions, cfg_b)
    assert key_a != key_b


@pytest.mark.unit
def test_cache_set_and_get(cache_dir, sample_transactions, sample_config_snapshot):
    """写入后应能读取（且不污染原结果）"""
    cache = AnalysisCache(cache_dir=cache_dir, enabled=True)
    key = cache._compute_key(sample_transactions, sample_config_snapshot)
    result = {"rule_hit_count": 3, "rule_details": {"smurfing": 3}}
    cache.set(key, result)

    retrieved = cache.get(key)
    assert retrieved is not None
    assert retrieved["rule_hit_count"] == 3
    # 内部标记字段不应被持久化
    assert "_cache_hit" not in result


@pytest.mark.unit
def test_cache_miss_returns_none(cache_dir, sample_transactions, sample_config_snapshot):
    """未写入的key应返回None"""
    cache = AnalysisCache(cache_dir=cache_dir, enabled=True)
    key = cache._compute_key(sample_transactions, sample_config_snapshot)
    assert cache.get(key) is None


@pytest.mark.unit
def test_cache_expiration(cache_dir, sample_transactions, sample_config_snapshot):
    """过期缓存应返回None且删除文件"""
    cache = AnalysisCache(cache_dir=cache_dir, enabled=True, expire_days=1)
    key = cache._compute_key(sample_transactions, sample_config_snapshot)
    cache.set(key, {"rule_hit_count": 1})

    # 篡改写入时间为2天前，模拟过期
    cache_path = os.path.join(cache_dir, f"{key}.json")
    with open(cache_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["_cached_at"] = time.time() - 2 * 24 * 3600  # 2天前
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    assert cache.get(key) is None
    # 过期文件应被删除
    assert not os.path.exists(cache_path)


@pytest.mark.unit
def test_cache_size_limit_clears_oldest(cache_dir, sample_transactions, sample_config_snapshot):
    """超过容量限制应删除最旧的"""
    # 设1KB限制，写入多个缓存必定超限
    cache = AnalysisCache(cache_dir=cache_dir, enabled=True, max_size_mb=0.001)
    # 写入3个不同的缓存
    for i in range(3):
        txns = [{"transaction_id": f"T{i:03d}", "from_account": "A", "to_account": "B",
                 "amount": 1000.0, "timestamp": "2025-01-01 10:00:00", "remark": ""}]
        key = cache._compute_key(txns, sample_config_snapshot)
        cache.set(key, {"rule_hit_count": i, "padding": "x" * 500})
        time.sleep(0.05)  # 确保修改时间不同

    # 应该有缓存被清理
    files = [f for f in os.listdir(cache_dir) if f.endswith(".json")]
    stats = cache.stats()
    assert stats["count"] < 3  # 至少清理了一个
    assert stats["size_mb"] < 0.001


@pytest.mark.unit
def test_cache_stats(cache_dir, sample_transactions, sample_config_snapshot):
    """stats 返回正确统计"""
    cache = AnalysisCache(cache_dir=cache_dir, enabled=True)
    assert cache.stats()["count"] == 0

    key = cache._compute_key(sample_transactions, sample_config_snapshot)
    cache.set(key, {"x": 1})
    stats = cache.stats()
    assert stats["count"] == 1
    # 缓存文件存在即说明大小>0（小文件 round 后可能为 0.0）
    cache_path = os.path.join(cache_dir, f"{key}.json")
    assert os.path.exists(cache_path)
    assert os.path.getsize(cache_path) > 0
    assert stats["enabled"] is True


@pytest.mark.unit
def test_cache_clear(cache_dir, sample_transactions, sample_config_snapshot):
    """clear 应清空所有缓存"""
    cache = AnalysisCache(cache_dir=cache_dir, enabled=True)
    key = cache._compute_key(sample_transactions, sample_config_snapshot)
    cache.set(key, {"x": 1})
    assert cache.stats()["count"] == 1

    cache.clear()
    assert cache.stats()["count"] == 0


# ============================================================
# build_config_snapshot 测试
# ============================================================
@pytest.mark.unit
def test_build_config_snapshot_only_rules():
    """配置快照应只包含 rules 部分"""
    aml_config = {
        "rules": {
            "smurfing": {"min_count": 5, "amount_low": 40000},
            "large_amount": {"threshold": 100000},
        },
        "llm": {"api_key": "secret", "model": "deepseek-chat"},
        "gnn": {"hidden_dim": 64},
        "paths": {"data_dir": "/tmp"},
    }
    snapshot = build_config_snapshot(aml_config)
    assert "smurfing" in snapshot
    assert "large_amount" in snapshot
    # 不应包含非 rules 配置
    assert "llm" not in snapshot
    assert "gnn" not in snapshot
    assert "paths" not in snapshot


@pytest.mark.unit
def test_build_config_snapshot_handles_tuples():
    """配置中的 tuple 应转为 list（保证可JSON序列化）"""
    aml_config = {
        "rules": {
            "smurfing": {"amount_range": (40000, 50000)},
        },
    }
    snapshot = build_config_snapshot(aml_config)
    assert snapshot["smurfing"]["amount_range"] == [40000, 50000]


# ============================================================
# rule_engine 集成测试
# ============================================================
@pytest.mark.unit
def test_rule_engine_cache_hit_on_second_run(cache_dir, sample_transactions, monkeypatch):
    """启用缓存时，相同数据第二次应命中缓存"""
    # 启用缓存且不跳过画像（无画像文件）
    monkeypatch.setattr(
        "agents.rule_engine.CACHE_CONFIG",
        {"enabled": True, "expire_days": 7, "max_size_mb": 100, "skip_when_profile": True},
    )
    # 让缓存目录指向临时目录
    monkeypatch.setattr("config.CACHE_DIR", cache_dir)

    # Mock 画像管理器返回空（避免读真实文件）
    with patch("tools.account_profile.AccountProfileManager") as MockPM:
        mock_inst = MagicMock()
        mock_inst.get_all_profiles.return_value = {}
        MockPM.return_value = mock_inst

        node = create_rule_engine_agent(llm=None)
        state = {
            "cleaned_transactions": sample_transactions,
            "account_baselines": {},
        }

        # 第一次运行：缓存未命中，应执行计算
        result1 = node(state)
        assert result1["rule_engine_stats"]["cache_hit"] is False

        # 第二次运行：应命中缓存
        result2 = node(state)
        assert result2["rule_engine_stats"]["cache_hit"] is True
        # 关键结果应一致
        assert result2["rule_hit_count"] == result1["rule_hit_count"]


@pytest.mark.unit
def test_rule_engine_cache_skipped_when_profile_loaded(sample_transactions, monkeypatch, tmp_path):
    """启用画像加权时（有画像数据），应跳过缓存"""
    fake_profile_dir = tmp_path / "profiles"
    fake_profile_dir.mkdir()
    # 写一个有内容的画像文件
    profile_file = fake_profile_dir / "account_profiles.json"
    profile_file.write_text(json.dumps({"A001": {"total_suspicious_hits": 3}}), encoding="utf-8")

    monkeypatch.setattr(
        "agents.rule_engine.CACHE_CONFIG",
        {"enabled": True, "expire_days": 7, "max_size_mb": 100, "skip_when_profile": True},
    )
    monkeypatch.setattr("agents.rule_engine.PROFILES_DIR", str(fake_profile_dir))

    node = create_rule_engine_agent(llm=None)
    state = {
        "cleaned_transactions": sample_transactions,
        "account_baselines": {},
    }

    # 即使启用缓存，因有画像数据，应跳过缓存
    result = node(state)
    assert result["rule_engine_stats"]["cache_hit"] is False


@pytest.mark.unit
def test_rule_engine_cache_disabled_by_default(sample_transactions, monkeypatch):
    """默认配置下缓存应禁用"""
    # 不修改配置，使用默认（enabled=False）
    node = create_rule_engine_agent(llm=None)
    state = {
        "cleaned_transactions": sample_transactions,
        "account_baselines": {},
    }

    # Mock 画像管理器返回空
    with patch("tools.account_profile.AccountProfileManager") as MockPM:
        mock_inst = MagicMock()
        mock_inst.get_all_profiles.return_value = {}
        MockPM.return_value = mock_inst

        result = node(state)
        # 缓存未启用，不应有 cache_hit=True
        assert result["rule_engine_stats"].get("cache_hit", False) is False


@pytest.mark.unit
def test_rule_engine_empty_transactions_skips_cache(monkeypatch):
    """空交易列表不应触发缓存读写"""
    monkeypatch.setattr(
        "agents.rule_engine.CACHE_CONFIG",
        {"enabled": True, "expire_days": 7, "max_size_mb": 100, "skip_when_profile": True},
    )
    node = create_rule_engine_agent(llm=None)
    state = {"cleaned_transactions": [], "account_baselines": {}}
    result = node(state)
    assert result["rule_hit_count"] == 0
    # 空输入不应有 cache_hit 字段
    assert "cache_hit" not in result.get("rule_engine_stats", {})
