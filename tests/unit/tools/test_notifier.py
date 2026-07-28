"""
告警通知器测试

覆盖:
- 各类通知器启用/禁用
- 控制台通知严重级别过滤
- 文件通知日志写入
- Webhook 通知（URL 未配置时不发送）
- 邮件通知（未启用时不发送）
- NotificationManager 多渠道分发
- 关键告警多渠道兜底
- 通知失败不抛异常（错误隔离）
"""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from tools.alert_history import Alert
from tools.notifier import (
    ConsoleNotifier,
    FileNotifier,
    WebhookNotifier,
    EmailNotifier,
    NotificationManager,
    create_default_manager,
)


# ============================================================
# 测试夹具
# ============================================================
def _make_alert(severity="warning", rule_id="r1", **kwargs) -> Alert:
    defaults = dict(
        alert_id="alert_001",
        rule_id=rule_id,
        rule_name="测试规则",
        severity=severity,
        category="system_health",
        message="测试告警",
        triggered_at="2026-01-01T00:00:00",
        context={"key": "value", "count": 3},
    )
    defaults.update(kwargs)
    return Alert(**defaults)


# ============================================================
# ConsoleNotifier
# ============================================================
def test_console_notifier_disabled_returns_false(capsys):
    """禁用时不发送"""
    n = ConsoleNotifier(enabled=False)
    a = _make_alert()
    assert n.send(a) is False
    # 控制台不应有输出
    out = capsys.readouterr().out
    assert "测试告警" not in out


def test_console_notifier_send_warning(capsys):
    """warning 级别发送"""
    n = ConsoleNotifier(enabled=True)
    a = _make_alert(severity="warning")
    assert n.send(a) is True
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "测试规则" in out


def test_console_notifier_severity_filter(capsys):
    """min_severity 过滤低于阈值的告警"""
    n = ConsoleNotifier(enabled=True, min_severity="critical")
    # info 被过滤
    a_info = _make_alert(severity="info")
    assert n.send(a_info) is False
    # warning 被过滤
    a_warn = _make_alert(severity="warning")
    assert n.send(a_warn) is False
    # critical 通过
    a_crit = _make_alert(severity="critical")
    assert n.send(a_crit) is True


def test_console_notifier_emergency_icon(capsys):
    """emergency 级别使用紧急图标"""
    n = ConsoleNotifier(enabled=True, min_severity="info")
    a = _make_alert(severity="emergency")
    n.send(a)
    out = capsys.readouterr().out
    assert "🚨" in out
    assert "EMERGENCY" in out


def test_console_notifier_name():
    """通知器名称"""
    n = ConsoleNotifier()
    assert n.name() == "console"


def test_console_notifier_context_printed(capsys):
    """上下文基本信息会打印"""
    n = ConsoleNotifier(enabled=True)
    a = _make_alert(context={"count": 5, "name": "abc"})
    n.send(a)
    out = capsys.readouterr().out
    assert "count: 5" in out
    assert "name: abc" in out


# ============================================================
# FileNotifier
# ============================================================
def test_file_notifier_writes_log():
    """写入日志文件"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "alerts.log")
        n = FileNotifier(log_path=log_path, enabled=True)
        a = _make_alert()
        assert n.send(a) is True
        assert os.path.exists(log_path)
        content = open(log_path, "r", encoding="utf-8").read()
        assert "WARNING" in content
        assert "r1" in content
        assert "测试规则" in content
        assert "测试告警" in content


def test_file_notifier_disabled_returns_false():
    """禁用时不写文件"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "alerts.log")
        n = FileNotifier(log_path=log_path, enabled=False)
        a = _make_alert()
        assert n.send(a) is False
        assert not os.path.exists(log_path)


def test_file_notifier_appends():
    """多次发送追加写入"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "alerts.log")
        n = FileNotifier(log_path=log_path, enabled=True)
        n.send(_make_alert(alert_id="a1", rule_id="r1"))
        n.send(_make_alert(alert_id="a2", rule_id="r2"))
        content = open(log_path, "r", encoding="utf-8").read()
        assert "r1" in content
        assert "r2" in content


def test_file_notifier_context_written():
    """上下文以 JSON 形式写入"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "alerts.log")
        n = FileNotifier(log_path=log_path, enabled=True)
        a = _make_alert(context={"count": 7, "type": "test"})
        n.send(a)
        content = open(log_path, "r", encoding="utf-8").read()
        assert "context" in content
        assert '"count": 7' in content
        assert '"type": "test"' in content


def test_file_notifier_handles_write_error(capsys):
    """写入失败时返回 False 不抛异常"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "alerts.log")
        n = FileNotifier(log_path=log_path, enabled=True)
        a = _make_alert()
        # mock open 触发写入异常
        with patch("builtins.open", side_effect=OSError("disk full")):
            result = n.send(a)
        # 异常被吞掉，返回 False，不抛异常
        assert result is False
        out = capsys.readouterr().out
        assert "写入失败" in out or "FileNotifier" in out or "文件通知器" in out


def test_file_notifier_name():
    """通知器名称"""
    with tempfile.TemporaryDirectory() as tmpdir:
        n = FileNotifier(log_path=os.path.join(tmpdir, "x.log"))
        assert n.name() == "file"


# ============================================================
# WebhookNotifier
# ============================================================
def test_webhook_notifier_disabled():
    """未启用时返回 False"""
    n = WebhookNotifier(url="http://example.com", enabled=False)
    assert n.send(_make_alert()) is False


def test_webhook_notifier_no_url():
    """无 URL 时返回 False"""
    n = WebhookNotifier(url=None, enabled=True)
    assert n.send(_make_alert()) is False


def test_webhook_notifier_name():
    """通知器名称"""
    n = WebhookNotifier()
    assert n.name() == "webhook"


def test_webhook_notifier_handles_connection_error(capsys):
    """连接失败时不抛异常"""
    n = WebhookNotifier(url="http://127.0.0.1:1/nonexistent", enabled=True, timeout=1)
    # 实际不会真去连
    # 但我们mock 避免网络依赖
    with patch("urllib.request.urlopen", side_effect=OSError("conn refused")):
        result = n.send(_make_alert())
    assert result is False
    out = capsys.readouterr().out
    assert "Webhook" in out or "发送失败" in out


# ============================================================
# EmailNotifier
# ============================================================
def test_email_notifier_disabled():
    """未启用时返回 False"""
    n = EmailNotifier(smtp_host="smtp.example.com", enabled=False)
    assert n.send(_make_alert()) is False


def test_email_notifier_no_host():
    """无 SMTP host 时返回 False"""
    n = EmailNotifier(smtp_host=None, enabled=True)
    assert n.send(_make_alert()) is False


def test_email_notifier_no_recipients():
    """无收件人时返回 False"""
    n = EmailNotifier(smtp_host="smtp.example.com", to_addrs=[], enabled=True)
    assert n.send(_make_alert()) is False


def test_email_notifier_name():
    """通知器名称"""
    n = EmailNotifier()
    assert n.name() == "email"


# ============================================================
# NotificationManager
# ============================================================
def test_manager_no_notifiers():
    """无通知器时返回空 dict"""
    mgr = NotificationManager()
    a = _make_alert()
    assert mgr.notify(a) == {}


def test_manager_distribute_to_multiple():
    """分发到多个通知器"""
    mgr = NotificationManager()
    n1 = MagicMock()
    n1.name.return_value = "mock1"
    n1.send.return_value = True
    n2 = MagicMock()
    n2.name.return_value = "mock2"
    n2.send.return_value = False
    mgr.add_notifier(n1)
    mgr.add_notifier(n2)
    a = _make_alert()
    results = mgr.notify(a)
    assert results == {"mock1": True, "mock2": False}
    n1.send.assert_called_once_with(a)
    n2.send.assert_called_once_with(a)


def test_manager_isolates_exceptions(capsys):
    """单通知器异常不影响其他通知器"""
    mgr = NotificationManager()
    bad = MagicMock()
    bad.name.return_value = "bad"
    bad.send.side_effect = RuntimeError("boom")
    good = MagicMock()
    good.name.return_value = "good"
    good.send.return_value = True
    mgr.add_notifier(bad)
    mgr.add_notifier(good)
    results = mgr.notify(_make_alert())
    # 异常被捕获，bad 返回 False
    assert results["bad"] is False
    assert results["good"] is True


def test_manager_notify_critical_critical_severity():
    """关键告警走 notify_critical 不抛异常"""
    mgr = NotificationManager()
    n = MagicMock()
    n.name.return_value = "m"
    n.send.return_value = True
    mgr.add_notifier(n)
    a = _make_alert(severity="critical")
    results = mgr.notify_critical(a)
    assert results == {"m": True}


def test_manager_notify_critical_emergency(capsys):
    """emergency 级别全部渠道失败时触发控制台兜底"""
    mgr = NotificationManager()
    n = MagicMock()
    n.name.return_value = "m"
    n.send.return_value = False
    mgr.add_notifier(n)
    a = _make_alert(severity="emergency")
    results = mgr.notify_critical(a)
    # 原渠道失败
    assert results["m"] is False
    # 触发控制台兜底
    assert "fallback_console" in results
    out = capsys.readouterr().out
    assert "兜底" in out


def test_manager_notify_critical_emergency_partial_success_no_fallback(capsys):
    """emergency 级别有渠道成功时不触发兜底"""
    mgr = NotificationManager()
    n1 = MagicMock()
    n1.name.return_value = "m1"
    n1.send.return_value = True
    n2 = MagicMock()
    n2.name.return_value = "m2"
    n2.send.return_value = False
    mgr.add_notifier(n1)
    mgr.add_notifier(n2)
    a = _make_alert(severity="emergency")
    results = mgr.notify_critical(a)
    # 有渠道成功，不触发兜底
    assert "fallback_console" not in results
    assert results["m1"] is True
    assert results["m2"] is False


def test_manager_notify_critical_warning():
    """warning 级别也走 notify_critical（不强制）"""
    mgr = NotificationManager()
    n = MagicMock()
    n.name.return_value = "m"
    n.send.return_value = True
    mgr.add_notifier(n)
    a = _make_alert(severity="warning")
    results = mgr.notify_critical(a)
    assert results == {"m": True}


# ============================================================
# create_default_manager
# ============================================================
def test_create_default_manager_includes_console_and_file():
    """默认管理器包含控制台+文件"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import config as cfg
        orig_logs = cfg.LOGS_DIR
        cfg.LOGS_DIR = tmpdir
        try:
            mgr = create_default_manager()
            names = [n.name() for n in mgr.notifiers]
            assert "console" in names
            assert "file" in names
        finally:
            cfg.LOGS_DIR = orig_logs
