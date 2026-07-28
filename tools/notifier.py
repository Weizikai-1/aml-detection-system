"""
告警通知分发 (Notifier)

职责:
- 多渠道告警分发：控制台/日志文件/Webhook/邮件
- 渠道独立可配置（启用/禁用）
- 通知失败不影响告警本身

设计原则:
- M1: 通知消息基于真实告警数据，不编造
- P1: 关键告警多渠道同时发送（确保不遗漏）
- P2: 通知失败被捕获并记录到告警上下文，不抛出异常
"""
import json
import os
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from tools.alert_history import Alert


# ============================================================
# 通知器抽象基类
# ============================================================
class Notifier(ABC):
    """通知器抽象基类"""

    @abstractmethod
    def send(self, alert: Alert) -> bool:
        """
        发送通知

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    def name(self) -> str:
        pass


# ============================================================
# 控制台通知
# ============================================================
class ConsoleNotifier(Notifier):
    """控制台输出通知"""

    def __init__(self, enabled: bool = True, min_severity: str = "info"):
        self.enabled = enabled
        self.min_severity = min_severity
        self._severity_order = {
            "info": 0, "warning": 1, "critical": 2, "emergency": 3,
        }

    def name(self) -> str:
        return "console"

    def send(self, alert: Alert) -> bool:
        if not self.enabled:
            return False
        if self._severity_order.get(alert.severity, 0) < self._severity_order.get(self.min_severity, 0):
            return False

        # 选择图标
        icon = {
            "info": "ℹ️",
            "warning": "⚠️",
            "critical": "🔴",
            "emergency": "🚨",
        }.get(alert.severity, "•")

        print(f"\n{icon} [{alert.severity.upper()}] {alert.rule_name}")
        print(f"  消息: {alert.message}")
        print(f"  时间: {alert.triggered_at}")
        if alert.context:
            for k, v in alert.context.items():
                if isinstance(v, (str, int, float, bool)):
                    print(f"  {k}: {v}")
        return True


# ============================================================
# 文件通知
# ============================================================
class FileNotifier(Notifier):
    """文件日志通知器"""

    def __init__(self, log_path: str = None, enabled: bool = True):
        self.log_path = log_path
        self.enabled = enabled
        if self.log_path is None:
            from config import LOGS_DIR
            self.log_path = os.path.join(LOGS_DIR, "alerts.log")
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

    def name(self) -> str:
        return "file"

    def send(self, alert: Alert) -> bool:
        if not self.enabled:
            return False
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(
                    f"[{alert.triggered_at}] {alert.severity.upper()} | "
                    f"{alert.rule_id} | {alert.rule_name} | {alert.message}\n"
                )
                if alert.context:
                    f.write(f"  context: {json.dumps(alert.context, ensure_ascii=False)}\n")
            return True
        except Exception as e:
            print(f"  [文件通知器] 写入失败: {e}")
            return False


# ============================================================
# Webhook 通知
# ============================================================
class WebhookNotifier(Notifier):
    """Webhook 通知器（POST JSON）"""

    # 戒律 P1: 关键告警重试次数（应对网络抖动）
    _CRITICAL_RETRIES = 2

    def __init__(self, url: str = None, enabled: bool = False, timeout: int = 10):
        self.url = url
        self.enabled = enabled
        self.timeout = timeout

    def name(self) -> str:
        return "webhook"

    def send(self, alert: Alert) -> bool:
        if not self.enabled or not self.url:
            return False
        # 戒律 P1: 关键告警重试 1-2 次，避免网络抖动导致遗漏
        max_attempts = self._CRITICAL_RETRIES + 1 if alert.severity in ("critical", "emergency") else 1
        last_err = None
        for attempt in range(max_attempts):
            try:
                import urllib.request
                payload = alert.to_dict()
                req = urllib.request.Request(
                    self.url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if 200 <= resp.status < 300:
                        return True
            except Exception as e:
                last_err = e
                if attempt < max_attempts - 1:
                    import time as _time
                    _time.sleep(0.5)
                continue
        if last_err is not None:
            print(f"  [Webhook通知器] 发送失败（共尝试{max_attempts}次）: {last_err}")
        return False


# ============================================================
# 邮件通知
# ============================================================
class EmailNotifier(Notifier):
    """邮件通知器（需要配置SMTP）"""

    def __init__(
        self,
        smtp_host: str = None,
        smtp_port: int = 587,
        username: str = None,
        password: str = None,
        from_addr: str = None,
        to_addrs: List[str] = None,
        enabled: bool = False,
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self.to_addrs = to_addrs or []
        self.enabled = enabled

    def name(self) -> str:
        return "email"

    def send(self, alert: Alert) -> bool:
        if not self.enabled or not self.smtp_host or not self.to_addrs:
            return False
        try:
            msg = MIMEMultipart()
            msg["From"] = self.from_addr or self.username
            msg["To"] = ", ".join(self.to_addrs)
            msg["Subject"] = f"[{alert.severity.upper()}] 反洗钱告警: {alert.rule_name}"

            body = (
                f"告警时间: {alert.triggered_at}\n"
                f"严重级别: {alert.severity}\n"
                f"规则: {alert.rule_name} ({alert.rule_id})\n"
                f"消息: {alert.message}\n"
                f"\n上下文:\n{json.dumps(alert.context, ensure_ascii=False, indent=2)}"
            )
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                server.starttls()
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.send_message(msg)
            return True
        except Exception as e:
            print(f"  [邮件通知器] 发送失败: {e}")
            return False


# ============================================================
# 飞书通知（B2-2: IM 渠道扩展）
# ============================================================
class FeishuNotifier(Notifier):
    """飞书机器人通知器

    戒律:
    - M1: 通知内容来自真实 Alert，不编造
    - M2: 卡片含 rule_name + message + context + triggered_at
    - P1: critical/emergency 重试 2 次（复用 Webhook 重试机制）
    - P2: 低于 min_severity 不推送（避免噪音）
    - P4: 发送失败不抛异常
    """

    _CRITICAL_RETRIES = 2

    def __init__(
        self,
        webhook_url: str = None,
        secret: str = None,
        enabled: bool = False,
        min_severity: str = "critical",
        timeout: int = 10,
    ):
        self.webhook_url = webhook_url or ""
        self.secret = secret or ""
        self.enabled = enabled and bool(self.webhook_url)
        self.min_severity = min_severity
        self.timeout = timeout
        self._severity_order = {
            "info": 0, "warning": 1, "critical": 2, "emergency": 3,
        }

    def name(self) -> str:
        return "feishu"

    def _build_card(self, alert: Alert) -> Dict[str, Any]:
        """构建飞书 Interactive Card（戒律 M2: 含完整告警信息）"""
        # 严重级别对应颜色和图标
        severity_style = {
            "emergency": {"color": "red", "icon": "🚨"},
            "critical":  {"color": "red", "icon": "🔴"},
            "warning":   {"color": "yellow", "icon": "⚠️"},
            "info":      {"color": "blue", "icon": "ℹ️"},
        }.get(alert.severity, {"color": "blue", "icon": "•"})

        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{severity_style['icon']} {alert.severity.upper()}** | {alert.rule_name}",
                },
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**消息**: {alert.message}",
                },
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**时间**: {alert.triggered_at}",
                },
            },
        ]

        # 添加上下文信息（戒律 M2: 含 context）
        if alert.context:
            context_lines = []
            for k, v in alert.context.items():
                if isinstance(v, (str, int, float, bool)):
                    context_lines.append(f"- {k}: {v}")
            if context_lines:
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "**上下文**:\n" + "\n".join(context_lines),
                    },
                })

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"[反洗钱告警] {alert.rule_id}",
                    },
                    "template": severity_style["color"],
                },
                "elements": elements,
            },
        }

    def _build_sign(self, timestamp: int) -> str:
        """飞书签名算法: HmacSHA256(timestamp + "\\n" + secret)"""
        if not self.secret:
            return ""
        import hmac
        import hashlib
        import base64
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.b64encode(hmac_code).decode("utf-8")

    def send(self, alert: Alert) -> bool:
        if not self.enabled:
            return False
        # 严重级别过滤（戒律 P2: 避免噪音）
        if self._severity_order.get(alert.severity, 0) < self._severity_order.get(self.min_severity, 0):
            return False

        payload = self._build_card(alert)
        # 签名（如果配置了 secret）
        if self.secret:
            import time as _time
            timestamp = int(_time.time())
            payload["timestamp"] = str(timestamp)
            payload["sign"] = self._build_sign(timestamp)

        # 戒律 P1: critical/emergency 重试
        max_attempts = self._CRITICAL_RETRIES + 1 if alert.severity in ("critical", "emergency") else 1
        last_err = None
        for attempt in range(max_attempts):
            try:
                import urllib.request
                req = urllib.request.Request(
                    self.webhook_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if 200 <= resp.status < 300:
                        return True
            except Exception as e:
                last_err = e
                if attempt < max_attempts - 1:
                    import time as _time
                    _time.sleep(0.5)
                continue
        if last_err is not None:
            print(f"  [飞书通知器] 发送失败（共尝试{max_attempts}次）: {last_err}")
        return False


# ============================================================
# 钉钉通知（B2-2: IM 渠道扩展）
# ============================================================
class DingTalkNotifier(Notifier):
    """钉钉机器人通知器

    戒律同 FeishuNotifier
    """

    _CRITICAL_RETRIES = 2

    def __init__(
        self,
        webhook_url: str = None,
        secret: str = None,
        enabled: bool = False,
        min_severity: str = "critical",
        timeout: int = 10,
    ):
        self.webhook_url = webhook_url or ""
        self.secret = secret or ""
        self.enabled = enabled and bool(self.webhook_url)
        self.min_severity = min_severity
        self.timeout = timeout
        self._severity_order = {
            "info": 0, "warning": 1, "critical": 2, "emergency": 3,
        }

    def name(self) -> str:
        return "dingtalk"

    def _build_card(self, alert: Alert) -> Dict[str, Any]:
        """构建钉钉 ActionCard（戒律 M2: 含完整告警信息）"""
        severity_icon = {
            "emergency": "🚨", "critical": "🔴",
            "warning": "⚠️", "info": "ℹ️",
        }.get(alert.severity, "•")

        # 构建 Markdown 正文
        lines = [
            f"### {severity_icon} {alert.severity.upper()} - {alert.rule_name}",
            "",
            f"**消息**: {alert.message}",
            "",
            f"**时间**: {alert.triggered_at}",
            "",
            f"**规则ID**: {alert.rule_id}",
        ]
        if alert.context:
            lines.append("")
            lines.append("**上下文**:")
            for k, v in alert.context.items():
                if isinstance(v, (str, int, float, bool)):
                    lines.append(f"- {k}: {v}")

        return {
            "msgtype": "action_card",
            "action_card": {
                "title": f"[反洗钱告警] {alert.rule_id}",
                "text": "\n".join(lines),
            },
        }

    def _build_signed_url(self) -> str:
        """钉钉签名: 拼接 &timestamp=X&sign=Y 到 URL"""
        if not self.secret:
            return self.webhook_url
        import time as _time
        import hmac
        import hashlib
        import base64
        import urllib.parse
        timestamp = str(round(_time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            self.secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return f"{self.webhook_url}&timestamp={timestamp}&sign={sign}"

    def send(self, alert: Alert) -> bool:
        if not self.enabled:
            return False
        if self._severity_order.get(alert.severity, 0) < self._severity_order.get(self.min_severity, 0):
            return False

        payload = self._build_card(alert)
        url = self._build_signed_url()

        max_attempts = self._CRITICAL_RETRIES + 1 if alert.severity in ("critical", "emergency") else 1
        last_err = None
        for attempt in range(max_attempts):
            try:
                import urllib.request
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if 200 <= resp.status < 300:
                        return True
            except Exception as e:
                last_err = e
                if attempt < max_attempts - 1:
                    import time as _time
                    _time.sleep(0.5)
                continue
        if last_err is not None:
            print(f"  [钉钉通知器] 发送失败（共尝试{max_attempts}次）: {last_err}")
        return False


# ============================================================
# 企业微信通知（B2-2: IM 渠道扩展）
# ============================================================
class WeComNotifier(Notifier):
    """企业微信机器人通知器

    戒律同 FeishuNotifier
    """

    _CRITICAL_RETRIES = 2

    def __init__(
        self,
        webhook_url: str = None,
        enabled: bool = False,
        min_severity: str = "warning",
        timeout: int = 10,
    ):
        self.webhook_url = webhook_url or ""
        self.enabled = enabled and bool(self.webhook_url)
        self.min_severity = min_severity
        self.timeout = timeout
        self._severity_order = {
            "info": 0, "warning": 1, "critical": 2, "emergency": 3,
        }

    def name(self) -> str:
        return "wecom"

    def _build_markdown(self, alert: Alert) -> str:
        """构建企业微信 Markdown 消息（戒律 M2: 含完整告警信息）"""
        severity_icon = {
            "emergency": "🚨", "critical": "🔴",
            "warning": "⚠️", "info": "ℹ️",
        }.get(alert.severity, "•")

        lines = [
            f"### {severity_icon} {alert.severity.upper()} - {alert.rule_name}",
            f"**消息**: {alert.message}",
            f"**时间**: {alert.triggered_at}",
            f"**规则ID**: {alert.rule_id}",
        ]
        if alert.context:
            lines.append("**上下文**:")
            for k, v in alert.context.items():
                if isinstance(v, (str, int, float, bool)):
                    lines.append(f"- {k}: {v}")

        return {
            "msgtype": "markdown",
            "markdown": {"content": "\n".join(lines)},
        }

    def send(self, alert: Alert) -> bool:
        if not self.enabled:
            return False
        if self._severity_order.get(alert.severity, 0) < self._severity_order.get(self.min_severity, 0):
            return False

        payload = self._build_markdown(alert)

        max_attempts = self._CRITICAL_RETRIES + 1 if alert.severity in ("critical", "emergency") else 1
        last_err = None
        for attempt in range(max_attempts):
            try:
                import urllib.request
                req = urllib.request.Request(
                    self.webhook_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if 200 <= resp.status < 300:
                        return True
            except Exception as e:
                last_err = e
                if attempt < max_attempts - 1:
                    import time as _time
                    _time.sleep(0.5)
                continue
        if last_err is not None:
            print(f"  [企业微信通知器] 发送失败（共尝试{max_attempts}次）: {last_err}")
        return False


# ============================================================
# 通知管理器
# ============================================================
class NotificationManager:
    """通知管理器 - 协调多个通知渠道

    支持严重级别路由（B2-2）:
    - emergency/critical → 多渠道兜底
    - warning → IM + 文件
    - info → 文件 + 控制台
    """

    # 默认严重级别路由（戒律 P1: 关键告警多渠道兜底）
    DEFAULT_ROUTING: Dict[str, List[str]] = {
        "emergency": ["feishu", "dingtalk", "wecom", "email", "console"],
        "critical":  ["feishu", "dingtalk", "wecom", "email", "console"],
        "warning":   ["feishu", "wecom", "file"],
        "info":      ["file", "console"],
    }

    def __init__(self, routing: Dict[str, List[str]] = None):
        self.notifiers: List[Notifier] = []
        # 关键级别多渠道兜底
        self.critical_severities = {"critical", "emergency"}
        self.routing = routing or self.DEFAULT_ROUTING
        # 路由表中已知渠道名（仅这些受路由约束，其他自定义通知器默认放行）
        self._known_channels = set()
        for channels in self.routing.values():
            self._known_channels.update(channels)

    def add_notifier(self, notifier: Notifier):
        self.notifiers.append(notifier)

    def _should_route_to(self, notifier_name: str, severity: str) -> bool:
        """
        判断该严重级别是否应路由到该通知器（戒律 P2: 避免噪音）

        规则:
        - 已知渠道名（feishu/dingtalk/wecom/email/console/file 等）：按路由表过滤
        - 未知渠道名（自定义/mock 通知器）：默认放行（向后兼容）
        """
        # 未知渠道名不参与路由过滤，保留向后兼容
        if notifier_name not in self._known_channels:
            return True
        allowed = self.routing.get(severity, [])
        return notifier_name in allowed

    def notify(self, alert: Alert) -> Dict[str, bool]:
        """
        分发告警到所有通知器（按严重级别路由）

        戒律 P2: 低级别告警不推送到高级别渠道
        戒律 P4: 单渠道失败不影响其他

        Returns:
            {notifier_name: success}
        """
        results: Dict[str, bool] = {}
        for n in self.notifiers:
            # 严重级别路由检查
            if not self._should_route_to(n.name(), alert.severity):
                continue
            try:
                ok = n.send(alert)
                results[n.name()] = ok
            except Exception as e:
                print(f"  [通知器 {n.name()}] 异常: {e}")
                results[n.name()] = False
        return results

    def notify_critical(self, alert: Alert) -> Dict[str, bool]:
        """
        关键告警多渠道兜底

        戒律 P1: 关键告警必须尽可能送达
        - 第一轮：所有已配置渠道并发发送
        - 若有通知器且全部失败：再次尝试控制台渠道（最低保障），确保关键事件不丢失
        - 若无任何通知器：也尝试控制台兜底
        """
        results = self.notify(alert)
        # 关键告警全部渠道失败时，强制控制台重试（最低保障）
        if alert.severity in self.critical_severities:
            # 仅当有通知器参与发送时才判断"全部失败"
            # 无通知器时也触发兜底
            all_failed = len(results) == 0 or not any(results.values())
            if all_failed:
                print(f"  [告警] 关键告警所有渠道失败，尝试控制台兜底: {alert.rule_id}")
                try:
                    fallback = ConsoleNotifier(enabled=True, min_severity="info")
                    ok = fallback.send(alert)
                    results["fallback_console"] = ok
                except Exception as e:
                    print(f"  [告警] 控制台兜底也失败: {e}")
                    results["fallback_console"] = False
        return results


# ============================================================
# 便捷配置
# ============================================================

def create_default_manager() -> NotificationManager:
    """创建默认通知管理器（控制台+文件）"""
    mgr = NotificationManager()
    mgr.add_notifier(ConsoleNotifier(enabled=True))
    mgr.add_notifier(FileNotifier(enabled=True))
    return mgr
