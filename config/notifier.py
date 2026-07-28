"""
通知渠道配置
"""
import os as _os

NOTIFIER_CONFIG = {
    "feishu": {
        "enabled": _os.getenv("FEISHU_WEBHOOK_URL", "") != "",
        "webhook_url": _os.getenv("FEISHU_WEBHOOK_URL", ""),
        "secret": _os.getenv("FEISHU_SECRET", ""),
        "min_severity": "critical",
    },
    "dingtalk": {
        "enabled": _os.getenv("DINGTALK_WEBHOOK_URL", "") != "",
        "webhook_url": _os.getenv("DINGTALK_WEBHOOK_URL", ""),
        "secret": _os.getenv("DINGTALK_SECRET", ""),
        "min_severity": "critical",
    },
    "wecom": {
        "enabled": _os.getenv("WECOM_WEBHOOK_URL", "") != "",
        "webhook_url": _os.getenv("WECOM_WEBHOOK_URL", ""),
        "min_severity": "warning",
    },
    "severity_routing": {
        "emergency": ["feishu", "dingtalk", "wecom", "email", "console"],
        "critical":  ["feishu", "dingtalk", "wecom", "email", "console"],
        "warning":   ["feishu", "wecom", "file"],
        "info":      ["file", "console"],
    },
}
