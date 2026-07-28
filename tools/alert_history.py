"""
告警历史记录 (Alert History)

职责:
- 持久化所有触发的告警
- 提供查询、统计、去重（抑制窗口管理）
- 支持按严重级别/类别/时间筛选

设计原则:
- M1: 历史记录基于真实触发的告警，不臆测
- P1: 关键告警（emergency）永远不被抑制，确保不遗漏
"""
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional

from config import DATA_DIR


# 告警数据目录
ALERT_HISTORY_DIR = os.path.join(DATA_DIR, "alerts")
ALERT_HISTORY_INDEX = os.path.join(ALERT_HISTORY_DIR, "index.json")


@dataclass
class Alert:
    """告警事件"""
    alert_id: str
    rule_id: str
    rule_name: str
    severity: str
    category: str
    message: str
    triggered_at: str
    context: dict = field(default_factory=dict)
    # 抑制相关
    suppressed: bool = False
    notification_sent: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Alert":
        return cls(
            alert_id=data.get("alert_id", ""),
            rule_id=data.get("rule_id", ""),
            rule_name=data.get("rule_name", ""),
            severity=data.get("severity", "info"),
            category=data.get("category", "system_health"),
            message=data.get("message", ""),
            triggered_at=data.get("triggered_at", ""),
            context=data.get("context", {}),
            suppressed=data.get("suppressed", False),
            notification_sent=data.get("notification_sent", False),
        )


class AlertHistory:
    """告警历史记录管理器"""

    def __init__(self, history_dir: str = None):
        self.history_dir = history_dir or ALERT_HISTORY_DIR
        os.makedirs(self.history_dir, exist_ok=True)
        self.index_file = os.path.join(self.history_dir, "index.json")
        self._index: List[dict] = []  # [{alert_id, filename, ...}]
        self._save_seq = 0
        self._load_index()

    def _load_index(self):
        """加载索引"""
        if os.path.exists(self.index_file):
            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    self._index = json.load(f)
            except Exception:
                self._index = []

    def _save_index(self):
        """保存索引（带纳秒时间戳+seq稳定排序）"""
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(self._index, f, ensure_ascii=False, indent=2)

    def save_alert(self, alert: Alert) -> str:
        """保存单条告警到独立文件"""
        filename = f"{alert.alert_id}.json"
        filepath = os.path.join(self.history_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(alert.to_dict(), f, ensure_ascii=False, indent=2)

        # 更新索引
        self._save_seq += 1
        self._index.append({
            "alert_id": alert.alert_id,
            "rule_id": alert.rule_id,
            "severity": alert.severity,
            "category": alert.category,
            "triggered_at": alert.triggered_at,
            "filename": filename,
            "_seq": self._save_seq,
            # 戒律 M4: 索引保留抑制/通知状态，便于列表查询快速过滤
            "suppressed": alert.suppressed,
            "notification_sent": alert.notification_sent,
        })
        self._save_index()
        return filepath

    def list_alerts(
        self,
        severity: Optional[str] = None,
        category: Optional[str] = None,
        rule_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        """列出告警（支持筛选）"""
        results = list(self._index)

        if severity:
            results = [r for r in results if r.get("severity") == severity]
        if category:
            results = [r for r in results if r.get("category") == category]
        if rule_id:
            results = [r for r in results if r.get("rule_id") == rule_id]

        # 按触发时间+seq倒序（稳定排序）
        results.sort(
            key=lambda x: (
                x.get("triggered_at", ""),
                x.get("_seq", 0),
            ),
            reverse=True,
        )
        return results[:limit]

    def get_alert(self, alert_id: str) -> Optional[Alert]:
        """根据ID获取告警详情"""
        for entry in self._index:
            if entry.get("alert_id") == alert_id:
                filepath = os.path.join(self.history_dir, entry["filename"])
                if os.path.exists(filepath):
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            return Alert.from_dict(json.load(f))
                    except Exception:
                        return None
        return None

    def get_last_trigger_time(self, rule_id: str) -> Optional[str]:
        """获取某规则最后一次触发时间（用于抑制窗口判断）"""
        last_time = None
        for entry in self._index:
            if entry.get("rule_id") == rule_id:
                t = entry.get("triggered_at", "")
                if t and (last_time is None or t > last_time):
                    last_time = t
        return last_time

    def stats(self) -> dict:
        """统计告警信息"""
        total = len(self._index)
        by_severity: Dict[str, int] = {}
        by_category: Dict[str, int] = {}
        by_rule: Dict[str, int] = {}

        for entry in self._index:
            sev = entry.get("severity", "unknown")
            cat = entry.get("category", "unknown")
            rid = entry.get("rule_id", "unknown")
            by_severity[sev] = by_severity.get(sev, 0) + 1
            by_category[cat] = by_category.get(cat, 0) + 1
            by_rule[rid] = by_rule.get(rid, 0) + 1

        return {
            "total": total,
            "by_severity": by_severity,
            "by_category": by_category,
            "by_rule": by_rule,
        }

    def clear(self):
        """清空所有告警（谨慎使用）"""
        for f in os.listdir(self.history_dir):
            if f.endswith(".json"):
                try:
                    os.remove(os.path.join(self.history_dir, f))
                except Exception:
                    pass
        self._index = []
        self._save_index()
