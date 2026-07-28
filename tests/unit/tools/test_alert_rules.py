"""
告警规则测试

覆盖:
- 枚举与数据结构
- 默认规则集完整性
- 规则注册中心（注册/查询/启用/禁用/分类）
"""
import pytest

from tools.alert_rules import (
    AlertSeverity,
    AlertCategory,
    AlertRule,
    AlertRuleRegistry,
    DEFAULT_ALERT_RULES,
    default_registry,
)


# ============================================================
# 枚举与基础结构
# ============================================================
def test_severity_values():
    """严重级别枚举值"""
    assert AlertSeverity.INFO.value == "info"
    assert AlertSeverity.WARNING.value == "warning"
    assert AlertSeverity.CRITICAL.value == "critical"
    assert AlertSeverity.EMERGENCY.value == "emergency"


def test_category_values():
    """类别枚举值"""
    assert AlertCategory.RISK_DETECTION.value == "risk_detection"
    assert AlertCategory.SYSTEM_HEALTH.value == "system_health"
    assert AlertCategory.COMPLIANCE.value == "compliance"
    assert AlertCategory.PERFORMANCE.value == "performance"
    assert AlertCategory.EVALUATION.value == "evaluation"
    assert AlertCategory.WORKFLOW.value == "workflow"


def test_alert_rule_to_dict():
    """规则序列化完整"""
    rule = AlertRule(
        rule_id="r1",
        name="测试",
        description="desc",
        severity=AlertSeverity.WARNING,
        category=AlertCategory.RISK_DETECTION,
        threshold=10.0,
        suppress_window_sec=120,
    )
    d = rule.to_dict()
    assert d["rule_id"] == "r1"
    assert d["severity"] == "warning"
    assert d["category"] == "risk_detection"
    assert d["threshold"] == 10.0
    assert d["suppress_window_sec"] == 120
    assert d["enabled"] is True


def test_alert_rule_default_enabled():
    """新规则默认启用"""
    rule = AlertRule(
        rule_id="r2",
        name="x",
        description="x",
        severity=AlertSeverity.INFO,
        category=AlertCategory.SYSTEM_HEALTH,
    )
    assert rule.enabled is True
    assert rule.threshold is None
    assert rule.suppress_window_sec == 300  # 默认5分钟


# ============================================================
# 默认规则集
# ============================================================
def test_default_rules_cover_all_categories():
    """默认规则覆盖所有关键类别"""
    cats = {r.category for r in DEFAULT_ALERT_RULES}
    # 核心类别必须存在
    assert AlertCategory.RISK_DETECTION in cats
    assert AlertCategory.SYSTEM_HEALTH in cats
    assert AlertCategory.PERFORMANCE in cats
    assert AlertCategory.EVALUATION in cats
    assert AlertCategory.WORKFLOW in cats


def test_default_rules_ids_unique():
    """默认规则 ID 唯一"""
    ids = [r.rule_id for r in DEFAULT_ALERT_RULES]
    assert len(ids) == len(set(ids))


def test_emergency_rule_not_suppressed():
    """紧急告警抑制窗口为 0（不抑制）"""
    rule = next(r for r in DEFAULT_ALERT_RULES if r.severity == AlertSeverity.EMERGENCY)
    assert rule.suppress_window_sec == 0


def test_eval_recall_rule_is_critical():
    """评估 Recall 下降告警为严重（戒律 P1 不遗漏）"""
    rule = default_registry.get("eval_recall_drop")
    assert rule is not None
    assert rule.severity == AlertSeverity.CRITICAL


# ============================================================
# 规则注册中心
# ============================================================
def test_registry_initialized_with_defaults():
    """注册中心初始化时已加载默认规则"""
    reg = AlertRuleRegistry()
    assert len(reg.list_all()) == len(DEFAULT_ALERT_RULES)
    assert len(reg.list_enabled()) == len(DEFAULT_ALERT_RULES)


def test_registry_get_existing():
    """获取已存在规则"""
    reg = AlertRuleRegistry()
    rule = reg.get("risk_critical_transaction")
    assert rule is not None
    assert rule.rule_id == "risk_critical_transaction"


def test_registry_get_missing_returns_none():
    """获取不存在的规则返回 None"""
    reg = AlertRuleRegistry()
    assert reg.get("nonexistent") is None


def test_registry_register_new():
    """注册新规则"""
    reg = AlertRuleRegistry()
    new_rule = AlertRule(
        rule_id="custom_test",
        name="custom",
        description="custom",
        severity=AlertSeverity.INFO,
        category=AlertCategory.WORKFLOW,
    )
    reg.register(new_rule)
    # register 做浅拷贝避免污染原对象，验证值相等
    got = reg.get("custom_test")
    assert got is not None
    assert got.rule_id == "custom_test"
    assert got.threshold == new_rule.threshold
    assert len(reg.list_all()) == len(DEFAULT_ALERT_RULES) + 1


def test_registry_register_overwrite():
    """重复注册覆盖旧规则"""
    reg = AlertRuleRegistry()
    rule_v1 = AlertRule(
        rule_id="dup",
        name="v1",
        description="v1",
        severity=AlertSeverity.INFO,
        category=AlertCategory.WORKFLOW,
        threshold=1.0,
    )
    rule_v2 = AlertRule(
        rule_id="dup",
        name="v2",
        description="v2",
        severity=AlertSeverity.WARNING,
        category=AlertCategory.WORKFLOW,
        threshold=2.0,
    )
    reg.register(rule_v1)
    reg.register(rule_v2)
    # 验证 v2 覆盖了 v1
    got = reg.get("dup")
    assert got.name == "v2"
    assert got.threshold == 2.0
    assert got.severity == AlertSeverity.WARNING


def test_registry_enable_disable():
    """启用/禁用规则"""
    reg = AlertRuleRegistry()
    rid = "risk_critical_transaction"
    assert reg.disable(rid) is True
    assert reg.get(rid).enabled is False
    assert len(reg.list_enabled()) == len(DEFAULT_ALERT_RULES) - 1
    assert reg.enable(rid) is True
    assert reg.get(rid).enabled is True


def test_registry_enable_missing():
    """启用不存在的规则返回 False"""
    reg = AlertRuleRegistry()
    assert reg.enable("nonexistent") is False
    assert reg.disable("nonexistent") is False


def test_registry_by_category():
    """按类别筛选"""
    reg = AlertRuleRegistry()
    risk_rules = reg.by_category(AlertCategory.RISK_DETECTION)
    assert len(risk_rules) >= 1
    assert all(r.category == AlertCategory.RISK_DETECTION for r in risk_rules)


def test_registry_by_category_excludes_disabled():
    """按类别筛选时不包含已禁用的规则"""
    reg = AlertRuleRegistry()
    # 禁用某条风险检测规则
    reg.disable("risk_critical_transaction")
    risk_rules = reg.by_category(AlertCategory.RISK_DETECTION)
    assert all(r.rule_id != "risk_critical_transaction" for r in risk_rules)


def test_registry_to_dict():
    """注册中心整体序列化"""
    reg = AlertRuleRegistry()
    d = reg.to_dict()
    assert isinstance(d, dict)
    assert "risk_critical_transaction" in d
    assert d["risk_critical_transaction"]["severity"] == "emergency"
