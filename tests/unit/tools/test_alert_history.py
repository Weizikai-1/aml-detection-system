"""
告警历史测试

覆盖:
- Alert 数据结构序列化
- AlertHistory 索引管理
- 告警保存/查询/筛选
- 抑制窗口支持（最后触发时间）
- 统计功能
- 索引稳定排序
"""
import os
import tempfile

import pytest

from tools.alert_history import (
    Alert,
    AlertHistory,
    ALERT_HISTORY_DIR,
)


# ============================================================
# Alert 数据结构
# ============================================================
def _make_alert(rule_id="r1", rule_name="name1", severity="warning", **kwargs) -> Alert:
    defaults = dict(
        alert_id="alert_001",
        rule_id=rule_id,
        rule_name=rule_name,
        severity=severity,
        category="system_health",
        message="msg",
        triggered_at="2026-01-01T00:00:00",
    )
    defaults.update(kwargs)
    return Alert(**defaults)


def test_alert_to_dict():
    """告警对象序列化"""
    a = _make_alert(context={"k": 1})
    d = a.to_dict()
    assert d["alert_id"] == "alert_001"
    assert d["rule_id"] == "r1"
    assert d["severity"] == "warning"
    assert d["context"] == {"k": 1}


def test_alert_from_dict_minimal():
    """最小字段构造告警"""
    a = Alert.from_dict({})
    assert a.alert_id == ""
    assert a.severity == "info"
    assert a.context == {}
    assert a.suppressed is False


def test_alert_from_dict_roundtrip():
    """序列化往返一致"""
    a = _make_alert(context={"x": "y", "n": 2}, notification_sent=True)
    b = Alert.from_dict(a.to_dict())
    assert b.alert_id == a.alert_id
    assert b.context == a.context
    assert b.notification_sent is True


# ============================================================
# AlertHistory - 使用临时目录隔离
# ============================================================
@pytest.fixture()
def tmp_history():
    """创建使用临时目录的告警历史管理器"""
    with tempfile.TemporaryDirectory() as tmpdir:
        h = AlertHistory(history_dir=tmpdir)
        yield h, tmpdir


def test_history_creates_dir(tmp_history):
    """初始化时创建目录"""
    h, tmpdir = tmp_history
    assert os.path.isdir(tmpdir)


def test_save_alert_creates_file(tmp_history):
    """保存告警时同时创建文件+索引"""
    h, tmpdir = tmp_history
    a = _make_alert(alert_id="a001")
    filepath = h.save_alert(a)
    assert os.path.exists(filepath)
    assert os.path.basename(filepath) == "a001.json"
    assert len(h._index) == 1
    assert h._index[0]["alert_id"] == "a001"


def test_save_alert_persists_index(tmp_history):
    """保存告警后索引文件可被重新加载"""
    h, tmpdir = tmp_history
    a = _make_alert(alert_id="a002")
    h.save_alert(a)
    # 重新构造实例，验证索引持久化
    h2 = AlertHistory(history_dir=tmpdir)
    assert len(h2._index) == 1
    assert h2._index[0]["alert_id"] == "a002"


def test_save_multiple_alerts_index_grows(tmp_history):
    """保存多条告警时索引稳定增长"""
    h, _ = tmp_history
    for i in range(5):
        h.save_alert(_make_alert(alert_id=f"a{i:03d}"))
    assert len(h._index) == 5


def test_save_alert_index_has_seq(tmp_history):
    """索引包含 _seq 字段（稳定排序）"""
    h, _ = tmp_history
    h.save_alert(_make_alert(alert_id="a1"))
    h.save_alert(_make_alert(alert_id="a2"))
    seqs = [e["_seq"] for e in h._index]
    assert seqs == [1, 2]


# ============================================================
# 列表查询
# ============================================================
def test_list_alerts_all(tmp_history):
    """列出全部告警"""
    h, _ = tmp_history
    h.save_alert(_make_alert(alert_id="a1", severity="warning"))
    h.save_alert(_make_alert(alert_id="a2", severity="critical"))
    alerts = h.list_alerts()
    assert len(alerts) == 2


def test_list_alerts_filter_severity(tmp_history):
    """按严重级别筛选"""
    h, _ = tmp_history
    h.save_alert(_make_alert(alert_id="a1", severity="warning"))
    h.save_alert(_make_alert(alert_id="a2", severity="critical"))
    h.save_alert(_make_alert(alert_id="a3", severity="warning"))
    alerts = h.list_alerts(severity="warning")
    assert len(alerts) == 2
    assert all(a["severity"] == "warning" for a in alerts)


def test_list_alerts_filter_category(tmp_history):
    """按类别筛选"""
    h, _ = tmp_history
    h.save_alert(_make_alert(alert_id="a1", severity="warning"))
    a2 = _make_alert(alert_id="a2", severity="warning")
    a2.category = "performance"
    h.save_alert(a2)
    alerts = h.list_alerts(category="performance")
    assert len(alerts) == 1
    assert alerts[0]["category"] == "performance"


def test_list_alerts_filter_rule_id(tmp_history):
    """按规则 ID 筛选"""
    h, _ = tmp_history
    h.save_alert(_make_alert(alert_id="a1", rule_id="r1"))
    h.save_alert(_make_alert(alert_id="a2", rule_id="r2"))
    alerts = h.list_alerts(rule_id="r1")
    assert len(alerts) == 1
    assert alerts[0]["rule_id"] == "r1"


def test_list_alerts_limit(tmp_history):
    """limit 生效"""
    h, _ = tmp_history
    for i in range(10):
        h.save_alert(_make_alert(alert_id=f"a{i:03d}"))
    assert len(h.list_alerts(limit=3)) == 3


def test_list_alerts_reverse_chrono(tmp_history):
    """按时间倒序"""
    h, _ = tmp_history
    h.save_alert(_make_alert(alert_id="a1", triggered_at="2026-01-01T10:00:00"))
    h.save_alert(_make_alert(alert_id="a2", triggered_at="2026-01-02T10:00:00"))
    h.save_alert(_make_alert(alert_id="a3", triggered_at="2026-01-03T10:00:00"))
    alerts = h.list_alerts()
    assert alerts[0]["alert_id"] == "a3"
    assert alerts[-1]["alert_id"] == "a1"


# ============================================================
# 单条查询
# ============================================================
def test_get_alert_existing(tmp_history):
    """根据 ID 获取告警详情"""
    h, _ = tmp_history
    h.save_alert(_make_alert(alert_id="a1", message="hello", context={"k": 1}))
    a = h.get_alert("a1")
    assert a is not None
    assert a.message == "hello"
    assert a.context == {"k": 1}


def test_get_alert_missing(tmp_history):
    """获取不存在的告警返回 None"""
    h, _ = tmp_history
    assert h.get_alert("nope") is None


# ============================================================
# 抑制窗口支持
# ============================================================
def test_get_last_trigger_time_empty(tmp_history):
    """空历史时返回 None"""
    h, _ = tmp_history
    assert h.get_last_trigger_time("any") is None


def test_get_last_trigger_time_returns_latest(tmp_history):
    """返回指定规则的最后触发时间"""
    h, _ = tmp_history
    h.save_alert(_make_alert(alert_id="a1", rule_id="r1", triggered_at="2026-01-01T00:00:00"))
    h.save_alert(_make_alert(alert_id="a2", rule_id="r1", triggered_at="2026-01-03T00:00:00"))
    h.save_alert(_make_alert(alert_id="a3", rule_id="r2", triggered_at="2026-01-05T00:00:00"))
    last = h.get_last_trigger_time("r1")
    assert last == "2026-01-03T00:00:00"


def test_get_last_trigger_time_rule_not_in_history(tmp_history):
    """未触发过的规则返回 None"""
    h, _ = tmp_history
    h.save_alert(_make_alert(alert_id="a1", rule_id="r1"))
    assert h.get_last_trigger_time("other_rule") is None


# ============================================================
# 统计
# ============================================================
def test_stats_empty(tmp_history):
    """空统计"""
    h, _ = tmp_history
    s = h.stats()
    assert s["total"] == 0
    assert s["by_severity"] == {}
    assert s["by_category"] == {}


def test_stats_aggregations(tmp_history):
    """按严重级别/类别/规则聚合"""
    h, _ = tmp_history
    h.save_alert(_make_alert(alert_id="a1", rule_id="r1", severity="warning", category="system_health"))
    h.save_alert(_make_alert(alert_id="a2", rule_id="r1", severity="critical", category="risk_detection"))
    h.save_alert(_make_alert(alert_id="a3", rule_id="r2", severity="warning", category="system_health"))
    s = h.stats()
    assert s["total"] == 3
    assert s["by_severity"]["warning"] == 2
    assert s["by_severity"]["critical"] == 1
    assert s["by_category"]["system_health"] == 2
    assert s["by_rule"]["r1"] == 2


# ============================================================
# 清空
# ============================================================
def test_clear_clears_index_contents(tmp_history):
    """清空后索引为空，文件也清空（除 index.json 自身）"""
    h, tmpdir = tmp_history
    h.save_alert(_make_alert(alert_id="a1"))
    h.save_alert(_make_alert(alert_id="a2"))
    h.clear()
    # 索引清空
    assert h._index == []
    # 单条告警文件被删除
    remaining = [f for f in os.listdir(tmpdir) if f.endswith(".json") and f != "index.json"]
    assert remaining == []


# ============================================================
# 索引排序稳定性
# ============================================================
def test_list_alerts_stable_when_same_timestamp(tmp_history):
    """同一时间戳内，_seq 保证稳定排序"""
    h, _ = tmp_history
    # 同一时间戳批量保存
    for i in range(5):
        h.save_alert(_make_alert(alert_id=f"a{i}", triggered_at="2026-01-01T00:00:00"))
    # 多次调用结果一致
    first = [a["alert_id"] for a in h.list_alerts()]
    second = [a["alert_id"] for a in h.list_alerts()]
    assert first == second
    # 顺序应是按 _seq 倒序（最新保存的在前）
    assert first == ["a4", "a3", "a2", "a1", "a0"]
