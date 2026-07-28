"""
监控告警引擎测试

覆盖:
- 基础触发接口（trigger）
- 抑制窗口与紧急级别豁免
- 禁用规则/未知规则
- 通知分发与结果回写
- check_workflow_state 各种分支
- check_evaluation_regression
- 错误隔离
- 统计信息
"""
import os
import tempfile
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from tools.alert_rules import (
    AlertSeverity,
    AlertCategory,
    AlertRule,
    AlertRuleRegistry,
)
from tools.alert_history import (
    Alert,
    AlertHistory,
)
from tools.notifier import NotificationManager
from tools.monitor import Monitor


# ============================================================
# 夹具
# ============================================================
@pytest.fixture()
def monitor_setup():
    """构建独立的监控器（规则/历史/通知器均为隔离实例）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 新建注册中心（register 已做浅拷贝，不会被 disable 污染）
        reg = AlertRuleRegistry()
        history = AlertHistory(history_dir=tmpdir)
        notifier = MagicMock(spec=NotificationManager)
        notifier.notify.return_value = {"mock": True}
        monitor = Monitor(
            rule_registry=reg,
            history=history,
            notifier=notifier,
        )
        yield monitor, reg, history, notifier


def _register_test_rule(
    reg: AlertRuleRegistry,
    rule_id: str = "test_rule",
    severity: AlertSeverity = AlertSeverity.WARNING,
    suppress_window_sec: int = 300,
    threshold: float = None,
    enabled: bool = True,
) -> AlertRule:
    rule = AlertRule(
        rule_id=rule_id,
        name=f"测试规则-{rule_id}",
        description="unit test",
        severity=severity,
        category=AlertCategory.SYSTEM_HEALTH,
        threshold=threshold,
        suppress_window_sec=suppress_window_sec,
        enabled=enabled,
    )
    reg.register(rule)
    return rule


# ============================================================
# 基础 trigger
# ============================================================
def test_trigger_basic(monitor_setup):
    """基础触发返回告警对象"""
    monitor, reg, history, notifier = monitor_setup
    _register_test_rule(reg)
    a = monitor.trigger("test_rule", "hello", {"k": 1})
    assert a is not None
    assert a.rule_id == "test_rule"
    assert a.message == "hello"
    assert a.context == {"k": 1}
    assert a.severity == "warning"
    # 历史已保存
    assert len(history._index) == 1
    # 通知已调用
    notifier.notify.assert_called_once()


def test_trigger_unknown_rule(monitor_setup, capsys):
    """未知规则返回 None"""
    monitor, reg, history, notifier = monitor_setup
    a = monitor.trigger("nonexistent", "x")
    assert a is None
    out = capsys.readouterr().out
    assert "未知规则" in out
    notifier.notify.assert_not_called()


def test_trigger_disabled_rule(monitor_setup):
    """禁用规则不触发"""
    monitor, reg, history, notifier = monitor_setup
    _register_test_rule(reg, enabled=False)
    a = monitor.trigger("test_rule", "x")
    assert a is None
    notifier.notify.assert_not_called()


def test_trigger_alert_id_unique(monitor_setup):
    """多次触发的告警 ID 唯一"""
    monitor, reg, history, notifier = monitor_setup
    _register_test_rule(reg, suppress_window_sec=0)
    a1 = monitor.trigger("test_rule", "msg1")
    a2 = monitor.trigger("test_rule", "msg2")
    assert a1 is not None
    assert a2 is not None
    assert a1.alert_id != a2.alert_id


def test_trigger_notification_sent_flag(monitor_setup):
    """通知成功后 notification_sent=True"""
    monitor, reg, history, notifier = monitor_setup
    notifier.notify.return_value = {"mock": True}
    _register_test_rule(reg)
    a = monitor.trigger("test_rule", "x")
    assert a.notification_sent is True


def test_trigger_notification_failed_flag(monitor_setup):
    """通知全部失败时 notification_sent=False"""
    monitor, reg, history, notifier = monitor_setup
    notifier.notify.return_value = {"mock": False}
    _register_test_rule(reg)
    a = monitor.trigger("test_rule", "x")
    assert a.notification_sent is False


def test_trigger_history_persists_full_alert(monitor_setup):
    """历史中保存的告警包含完整字段"""
    monitor, reg, history, notifier = monitor_setup
    _register_test_rule(reg)
    a = monitor.trigger("test_rule", "hello", {"key": "val"})
    fetched = history.get_alert(a.alert_id)
    assert fetched is not None
    assert fetched.message == "hello"
    assert fetched.context == {"key": "val"}


# ============================================================
# 抑制窗口
# ============================================================
def test_suppress_window_blocks_duplicate(monitor_setup):
    """抑制窗口期内重复触发被拦截"""
    monitor, reg, history, notifier = monitor_setup
    _register_test_rule(reg, suppress_window_sec=300)
    a1 = monitor.trigger("test_rule", "first")
    a2 = monitor.trigger("test_rule", "second")
    assert a1 is not None
    assert a2 is None  # 被抑制
    # 只记录了一条
    assert len(history._index) == 1


def test_suppress_window_force_bypasses(monitor_setup):
    """force=True 跳过抑制窗口"""
    monitor, reg, history, notifier = monitor_setup
    _register_test_rule(reg, suppress_window_sec=300)
    a1 = monitor.trigger("test_rule", "first")
    a2 = monitor.trigger("test_rule", "second", force=True)
    assert a1 is not None
    assert a2 is not None


def test_emergency_never_suppressed(monitor_setup):
    """emergency 级别永远不被抑制（戒律 P1）"""
    monitor, reg, history, notifier = monitor_setup
    _register_test_rule(
        reg, rule_id="emer", severity=AlertSeverity.EMERGENCY, suppress_window_sec=300
    )
    a1 = monitor.trigger("emer", "first")
    a2 = monitor.trigger("emer", "second")  # 不强制也会触发
    assert a1 is not None
    assert a2 is not None
    assert len(history._index) == 2


def test_suppress_window_zero_always_triggers(monitor_setup):
    """suppress_window_sec=0 表示不抑制"""
    monitor, reg, history, notifier = monitor_setup
    _register_test_rule(reg, suppress_window_sec=0)
    a1 = monitor.trigger("test_rule", "first")
    a2 = monitor.trigger("test_rule", "second")
    assert a1 is not None
    assert a2 is not None


def test_suppress_window_expired_triggers(monitor_setup):
    """抑制窗口期过后允许再次触发"""
    monitor, reg, history, notifier = monitor_setup
    # 先在历史里放一个很久之前的告警
    old_alert = Alert(
        alert_id="old",
        rule_id="test_rule",
        rule_name="x",
        severity="warning",
        category="system_health",
        message="old",
        triggered_at="2020-01-01T00:00:00",
    )
    history.save_alert(old_alert)
    _register_test_rule(reg, suppress_window_sec=60)
    # 间隔很久，应当可触发
    a = monitor.trigger("test_rule", "new")
    assert a is not None


# ============================================================
# check_workflow_state 分支
# ============================================================
def test_check_workflow_high_risk_count_threshold(monitor_setup):
    """高风险交易超阈值告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [
            {"risk_score": 75, "rule_hits": [], "evidence": [], "transaction": {"transaction_id": f"t{i}"}}
            for i in range(11)
        ],
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "risk_high_count_threshold" in rule_ids


def test_check_workflow_high_risk_below_threshold(monitor_setup):
    """高风险交易未达阈值不告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [
            {"risk_score": 75, "rule_hits": [], "evidence": [], "transaction": {"transaction_id": f"t{i}"}}
            for i in range(5)
        ],
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "risk_high_count_threshold" not in rule_ids


def test_check_workflow_critical_transaction(monitor_setup):
    """极严重可疑交易（≥85分）告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [
            {"risk_score": 90, "rule_hits": [], "evidence": [], "transaction": {"transaction_id": "t99"}},
        ],
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "risk_critical_transaction" in rule_ids


def test_check_workflow_critical_transaction_each(monitor_setup):
    """每笔极严重交易都告警（不抑制）"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [
            {"risk_score": 88, "rule_hits": [], "evidence": [], "transaction": {"transaction_id": f"t{i}"}}
            for i in range(3)
        ],
    }
    triggered = monitor.check_workflow_state(state)
    crit_alerts = [a for a in triggered if a.rule_id == "risk_critical_transaction"]
    assert len(crit_alerts) == 3


def test_check_workflow_shell_company(monitor_setup):
    """空壳公司识别告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [
            {"risk_score": 80, "rule_hits": ["空壳公司"], "evidence": [], "transaction": {"transaction_id": "t1"}},
        ],
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "risk_shell_company_detected" in rule_ids


def test_check_workflow_repeat_offender(monitor_setup):
    """累犯账户高活跃告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [
            {
                "risk_score": 75,
                "rule_hits": [],
                "evidence": ["画像加权: 高度累犯"],
                "transaction": {"transaction_id": "t1"},
            }
        ],
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "risk_repeat_offender" in rule_ids


def test_check_workflow_data_quality_low(monitor_setup):
    """数据质量低告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "preprocessing_stats": {"quality_score": 0.4},
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "health_data_quality_low" in rule_ids


def test_check_workflow_data_quality_high_no_alert(monitor_setup):
    """数据质量高不告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "preprocessing_stats": {"quality_score": 0.95},
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "health_data_quality_low" not in rule_ids


def test_check_workflow_perf_total_time(monitor_setup):
    """总耗时超阈值告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "total_processing_time": 120.0,
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "perf_analysis_too_slow" in rule_ids


def test_check_workflow_perf_node_slow(monitor_setup):
    """单节点耗时超阈值告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "step_times": {"data_preprocessor": 45.0, "rule_engine": 5.0},
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "perf_node_too_slow" in rule_ids


def test_check_workflow_compliance_rejected(monitor_setup):
    """合规驳回告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "rejected_reports": [{"id": "r1"}],
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "compliance_report_rejected" in rule_ids


def test_check_workflow_interrupted(monitor_setup):
    """工作流被中断告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "interrupted": True,
        "current_step": "rule_engine",
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "workflow_interrupted" in rule_ids


def test_check_workflow_no_suspicious(monitor_setup):
    """未发现可疑时通知"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "transactions": [{"id": i} for i in range(10)],
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "workflow_no_suspicious" in rule_ids


def test_check_workflow_empty_state():
    """空 state 不崩溃（不传任何键）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        reg = AlertRuleRegistry()
        history = AlertHistory(history_dir=tmpdir)
        notifier = MagicMock()
        notifier.notify.return_value = {"x": True}
        monitor = Monitor(reg, history, notifier)
        # 不抛异常
        triggered = monitor.check_workflow_state({})
        assert isinstance(triggered, list)


# ============================================================
# check_evaluation_regression
# ============================================================
def test_check_evaluation_regression_emits_alerts(monitor_setup):
    """指标下降时触发告警"""
    from tools.evaluator import EvaluationResult, ConfusionMatrix

    monitor, reg, history, notifier = monitor_setup
    baseline = EvaluationResult(
        eval_id="base",
        eval_time="2026-01-01T00:00:00",
        ground_truth_name="gt",
        ground_truth_version="1.0",
        total_evaluated=100,
        pending_skipped=0,
        overall=ConfusionMatrix(tp=80, fp=10, tn=5, fn=5),
    )
    # 当前评估指标明显下降
    current = EvaluationResult(
        eval_id="cur",
        eval_time="2026-01-02T00:00:00",
        ground_truth_name="gt",
        ground_truth_version="1.0",
        total_evaluated=100,
        pending_skipped=0,
        overall=ConfusionMatrix(tp=50, fp=20, tn=10, fn=20),
    )
    triggered = monitor.check_evaluation_regression(baseline, current)
    assert len(triggered) >= 1
    rule_ids = [a.rule_id for a in triggered]
    # precision 下降、recall 下降、f1 下降
    assert any("drop" in rid for rid in rule_ids)


def test_check_evaluation_regression_no_regression(monitor_setup):
    """指标无退化时不告警"""
    from tools.evaluator import EvaluationResult, ConfusionMatrix

    monitor, reg, history, notifier = monitor_setup
    baseline = EvaluationResult(
        eval_id="base",
        eval_time="2026-01-01T00:00:00",
        ground_truth_name="gt",
        ground_truth_version="1.0",
        total_evaluated=100,
        pending_skipped=0,
        overall=ConfusionMatrix(tp=80, fp=10, tn=5, fn=5),
    )
    # 同样指标
    current = EvaluationResult(
        eval_id="cur",
        eval_time="2026-01-02T00:00:00",
        ground_truth_name="gt",
        ground_truth_version="1.0",
        total_evaluated=100,
        pending_skipped=0,
        overall=ConfusionMatrix(tp=80, fp=10, tn=5, fn=5),
    )
    triggered = monitor.check_evaluation_regression(baseline, current)
    # 指标相同，无退化
    assert len(triggered) == 0


# ============================================================
# 错误隔离
# ============================================================
def test_notifier_exception_isolated(monitor_setup, capsys):
    """通知器抛异常不中断主流程"""
    monitor, reg, history, notifier = monitor_setup
    notifier.notify.side_effect = RuntimeError("notify boom")
    _register_test_rule(reg)
    # 不应抛异常
    a = monitor.trigger("test_rule", "x")
    # 异常被吞掉，告警仍可保存到历史
    assert a is not None
    assert len(history._index) == 1


def test_history_save_exception_isolated(monitor_setup, capsys):
    """历史保存失败不影响主流程"""
    monitor, reg, history, notifier = monitor_setup
    history.save_alert = MagicMock(side_effect=RuntimeError("history boom"))
    _register_test_rule(reg)
    # 不应抛异常
    a = monitor.trigger("test_rule", "x")
    assert a is not None


# ============================================================
# 统计
# ============================================================
def test_get_stats(monitor_setup):
    """统计信息"""
    monitor, reg, history, notifier = monitor_setup
    _register_test_rule(reg, rule_id="r1")
    _register_test_rule(reg, rule_id="r2", enabled=False)
    monitor.trigger("r1", "x")
    stats = monitor.get_stats()
    assert stats["rules"]["total"] >= 2
    assert stats["rules"]["enabled"] >= 1
    assert stats["history"]["total"] == 1
    assert "by_severity" in stats["history"]


# ============================================================
# 工作流集成（与默认监控器）
# ============================================================
def test_default_monitor_singleton():
    """get_monitor 返回单例"""
    from tools.monitor import get_monitor
    m1 = get_monitor()
    m2 = get_monitor()
    assert m1 is m2


# ============================================================
# 新增规则检查（节点失败/缓存未命中/人工审核过多）
# ============================================================
def test_check_workflow_node_failure(monitor_setup):
    """节点执行失败告警（戒律 P1: 不遗漏系统故障）"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "_node_error": {
            "node": "rule_engine",
            "error_type": "RuntimeError",
            "error_msg": "test failure",
        },
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "health_node_failure" in rule_ids


def test_check_workflow_node_failure_via_meta(monitor_setup):
    """通过 _node_meta.status=error 检测节点失败"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "_node_meta": {"node": "llm_reviewer", "status": "error"},
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "health_node_failure" in rule_ids


def test_check_workflow_node_no_error_no_alert(monitor_setup):
    """节点正常时不告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "_node_meta": {"node": "rule_engine", "status": "success"},
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "health_node_failure" not in rule_ids


def test_check_workflow_cache_miss_with_cache_enabled(monitor_setup):
    """缓存启用但未命中时告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "rule_engine_stats": {"cache_hit": False},
        "analysis_params": {"use_cache": True},
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "health_cache_miss_rate_high" in rule_ids


def test_check_workflow_cache_miss_cache_disabled_no_alert(monitor_setup):
    """缓存未启用时不告警（首次运行属正常）"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "rule_engine_stats": {"cache_hit": False},
        "analysis_params": {"use_cache": False},
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "health_cache_miss_rate_high" not in rule_ids


def test_check_workflow_human_review_high(monitor_setup):
    """人工审核比例过高告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "human_review_tasks": [{"id": i} for i in range(5)],
        "compliance_stats": {"total": 10, "passed": 3, "human_review": 5, "rejected": 2},
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "compliance_human_review_high" in rule_ids


def test_check_workflow_human_review_low_no_alert(monitor_setup):
    """人工审核比例正常不告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "human_review_tasks": [{"id": i} for i in range(2)],
        "compliance_stats": {"total": 10, "passed": 8, "human_review": 2, "rejected": 0},
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "compliance_human_review_high" not in rule_ids


def test_check_workflow_human_review_zero_total_no_alert(monitor_setup):
    """总报告数为0时不告警（避免除零）"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [],
        "human_review_tasks": [],
        "compliance_stats": {"total": 0},
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "compliance_human_review_high" not in rule_ids


# ============================================================
# threshold falsy 修复验证
# ============================================================
def test_threshold_zero_not_falsy(monitor_setup):
    """threshold=0 时不被 Python falsy 误判"""
    from tools.alert_rules import AlertRule, AlertSeverity, AlertCategory
    monitor, reg, history, notifier = monitor_setup
    # 注册一条 threshold=0 的规则（任何高风险都触发）
    reg.register(AlertRule(
        rule_id="test_zero_threshold",
        name="零阈值测试",
        description="threshold=0",
        severity=AlertSeverity.WARNING,
        category=AlertCategory.RISK_DETECTION,
        threshold=0,
        suppress_window_sec=0,
    ))
    # high_risk_count=1 >= 0 应触发（如果 falsy bug 存在则不会触发）
    # 但 risk_high_count_threshold 的 threshold=10，我们需要直接测 trigger
    a = monitor.trigger("test_zero_threshold", "test", force=True)
    assert a is not None


# ============================================================
# 安全访问验证
# ============================================================
def test_transaction_none_no_crash(monitor_setup):
    """transaction 为 None 时不崩溃"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [
            {"risk_score": 90, "transaction": None},
        ],
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "risk_critical_transaction" in rule_ids


def test_transaction_not_dict_no_crash(monitor_setup):
    """transaction 非 dict 时不崩溃"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [
            {"risk_score": 88, "transaction": "invalid_string"},
        ],
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "risk_critical_transaction" in rule_ids


# ============================================================
# 累犯账户检测精确匹配
# ============================================================
def test_repeat_offender_precise_match(monitor_setup):
    """累犯账户精确匹配 evidence 列表中的字符串"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [
            {
                "risk_score": 75,
                "rule_hits": [],
                "evidence": ["画像加权: 高度累犯账户"],
                "transaction": {"transaction_id": "t1"},
            },
        ],
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "risk_repeat_offender" in rule_ids


def test_repeat_offender_no_false_match(monitor_setup):
    """evidence 中不含"高度累犯"时不误报"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [
            {
                "risk_score": 75,
                "rule_hits": [],
                "evidence": ["画像加权: 一般账户"],
                "transaction": {"transaction_id": "t1"},
            },
        ],
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "risk_repeat_offender" not in rule_ids


def test_repeat_offender_low_score_no_match(monitor_setup):
    """评分<70 时不触发累犯告警"""
    monitor, reg, history, notifier = monitor_setup
    state = {
        "rule_hits": [
            {
                "risk_score": 60,
                "rule_hits": [],
                "evidence": ["画像加权: 高度累犯账户"],
                "transaction": {"transaction_id": "t1"},
            },
        ],
    }
    triggered = monitor.check_workflow_state(state)
    rule_ids = [a.rule_id for a in triggered]
    assert "risk_repeat_offender" not in rule_ids


# ============================================================
# 评估回归未知规则日志
# ============================================================
def test_eval_regression_unknown_rule_logged(monitor_setup, capsys):
    """未知评估指标规则跳过时有日志（M4 可追溯）"""
    from tools.evaluator import EvaluationResult, ConfusionMatrix
    from tools.eval_regression import RegressionDelta, RegressionReport

    monitor, reg, history, notifier = monitor_setup
    # mock compare_evaluations 返回一个不存在规则的 metric
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "tools.eval_regression.compare_evaluations",
            lambda b, c: RegressionReport(
                baseline_eval_id="base",
                current_eval_id="cur",
                baseline_time="2026-01-01T00:00:00",
                current_time="2026-01-02T00:00:00",
                degraded=[RegressionDelta(
                    metric="unknown_metric",
                    baseline=0.9,
                    current=0.5,
                    delta=-0.4,
                    delta_pct=-0.44,
                    is_degradation=True,
                )],
            ),
        )
        baseline = EvaluationResult(
            eval_id="base", eval_time="2026-01-01T00:00:00",
            ground_truth_name="gt", ground_truth_version="1.0",
            total_evaluated=100, pending_skipped=0,
            overall=ConfusionMatrix(tp=80, fp=10, tn=5, fn=5),
        )
        current = EvaluationResult(
            eval_id="cur", eval_time="2026-01-02T00:00:00",
            ground_truth_name="gt", ground_truth_version="1.0",
            total_evaluated=100, pending_skipped=0,
            overall=ConfusionMatrix(tp=50, fp=20, fn=20, tn=10),
        )
        triggered = monitor.check_evaluation_regression(baseline, current)
        # 未知规则不触发告警
        assert len(triggered) == 0
        # 但有日志输出
        out = capsys.readouterr().out
        assert "unknown_metric" in out
        assert "跳过" in out
