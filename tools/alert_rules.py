"""
告警规则定义 (Alert Rules)

职责:
- 定义所有可用的告警规则（数据结构+元信息）
- 支持规则启用/禁用、严重级别、阈值配置
- 提供内置默认规则集，覆盖反洗钱系统的关键监控点

设计原则:
- M1: 所有规则基于真实运行数据，阈值不臆测
- P1: 关键风险事件必须告警（不遗漏）
- P2: 避免告警疲劳（严重级别分层、抑制去重）
"""
import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class AlertSeverity(str, Enum):
    """告警严重级别"""
    INFO = "info"           # 通知性
    WARNING = "warning"     # 警告
    CRITICAL = "critical"   # 严重
    EMERGENCY = "emergency" # 紧急


class AlertCategory(str, Enum):
    """告警类别"""
    RISK_DETECTION = "risk_detection"         # 风险检测
    SYSTEM_HEALTH = "system_health"           # 系统健康
    COMPLIANCE = "compliance"                 # 合规相关
    PERFORMANCE = "performance"               # 性能
    EVALUATION = "evaluation"                 # 评估指标
    WORKFLOW = "workflow"                     # 工作流事件


@dataclass
class AlertRule:
    """告警规则定义"""
    rule_id: str
    name: str
    description: str
    severity: AlertSeverity
    category: AlertCategory
    enabled: bool = True
    threshold: Optional[float] = None
    # 抑制窗口（秒）：相同规则告警间隔（避免风暴）
    suppress_window_sec: int = 300

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "description": self.description,
            "severity": self.severity.value,
            "category": self.category.value,
            "enabled": self.enabled,
            "threshold": self.threshold,
            "suppress_window_sec": self.suppress_window_sec,
        }


# ============================================================
# 内置默认告警规则
# ============================================================

DEFAULT_ALERT_RULES: List[AlertRule] = [
    # ----- 风险检测告警 -----
    AlertRule(
        rule_id="risk_high_count_threshold",
        name="高风险交易超阈值",
        description="单次分析高风险交易（≥70分）数量超过阈值时告警",
        severity=AlertSeverity.CRITICAL,
        category=AlertCategory.RISK_DETECTION,
        threshold=10,
    ),
    AlertRule(
        rule_id="risk_critical_transaction",
        name="发现极严重可疑交易",
        description="任意交易风险评分≥85分时立即告警",
        severity=AlertSeverity.EMERGENCY,
        category=AlertCategory.RISK_DETECTION,
        threshold=85,
        suppress_window_sec=0,  # 紧急告警不抑制
    ),
    AlertRule(
        rule_id="risk_shell_company_detected",
        name="空壳公司识别命中",
        description="规则引擎检测到空壳公司特征时告警",
        severity=AlertSeverity.WARNING,
        category=AlertCategory.RISK_DETECTION,
    ),
    AlertRule(
        rule_id="risk_repeat_offender",
        name="累犯账户高活跃",
        description="累犯账户（历史可疑≥6次）在本批次再次命中时告警",
        severity=AlertSeverity.WARNING,
        category=AlertCategory.RISK_DETECTION,
    ),

    # ----- 系统健康告警 -----
    AlertRule(
        rule_id="health_node_failure",
        name="Agent 节点执行失败",
        description="任意Agent节点执行失败时告警",
        severity=AlertSeverity.WARNING,
        category=AlertCategory.SYSTEM_HEALTH,
    ),
    AlertRule(
        rule_id="health_data_quality_low",
        name="数据质量低",
        description="数据质量评分低于阈值时告警",
        severity=AlertSeverity.WARNING,
        category=AlertCategory.SYSTEM_HEALTH,
        threshold=0.6,
    ),
    AlertRule(
        rule_id="health_cache_miss_rate_high",
        name="缓存命中率过低",
        description="缓存命中率持续低于阈值时告警（可能系统压力增加）",
        severity=AlertSeverity.INFO,
        category=AlertCategory.SYSTEM_HEALTH,
        threshold=0.3,
    ),

    # ----- 合规告警 -----
    AlertRule(
        rule_id="compliance_report_rejected",
        name="报告被驳回",
        description="合规审核驳回报告时告警",
        severity=AlertSeverity.WARNING,
        category=AlertCategory.COMPLIANCE,
    ),
    AlertRule(
        rule_id="compliance_human_review_high",
        name="需人工审核报告过多",
        description="需要人工审核的报告比例超过阈值时告警",
        severity=AlertSeverity.INFO,
        category=AlertCategory.COMPLIANCE,
        threshold=0.3,
    ),

    # ----- 性能告警 -----
    AlertRule(
        rule_id="perf_analysis_too_slow",
        name="分析耗时过长",
        description="单次分析总耗时超过阈值（秒）时告警",
        severity=AlertSeverity.WARNING,
        category=AlertCategory.PERFORMANCE,
        threshold=60.0,
    ),
    AlertRule(
        rule_id="perf_node_too_slow",
        name="单节点耗时过长",
        description="单个Agent节点耗时超过阈值（秒）时告警",
        severity=AlertSeverity.INFO,
        category=AlertCategory.PERFORMANCE,
        threshold=30.0,
    ),

    # ----- 评估指标告警 -----
    AlertRule(
        rule_id="eval_precision_drop",
        name="精度下降",
        description="评估Precision相比基线下降超过阈值时告警",
        severity=AlertSeverity.WARNING,
        category=AlertCategory.EVALUATION,
        threshold=0.10,  # 10%
    ),
    AlertRule(
        rule_id="eval_recall_drop",
        name="召回率下降",
        description="评估Recall相比基线下降超过阈值时告警（戒律P1：不遗漏）",
        severity=AlertSeverity.CRITICAL,
        category=AlertCategory.EVALUATION,
        threshold=0.10,  # 10%
    ),
    AlertRule(
        rule_id="eval_f1_drop",
        name="F1下降",
        description="评估F1相比基线下降超过阈值时告警",
        severity=AlertSeverity.WARNING,
        category=AlertCategory.EVALUATION,
        threshold=0.05,  # 5%
    ),

    # ----- 工作流告警 -----
    AlertRule(
        rule_id="workflow_interrupted",
        name="分析流程被中断",
        description="工作流被用户中断时告警",
        severity=AlertSeverity.WARNING,
        category=AlertCategory.WORKFLOW,
        suppress_window_sec=0,
    ),
    AlertRule(
        rule_id="workflow_no_suspicious",
        name="未发现可疑交易",
        description="分析流程未发现任何可疑交易时通知（可能漏报）",
        severity=AlertSeverity.INFO,
        category=AlertCategory.WORKFLOW,
    ),
]


class AlertRuleRegistry:
    """告警规则注册中心"""

    def __init__(self):
        self._rules: dict = {}
        # 加载默认规则
        for rule in DEFAULT_ALERT_RULES:
            self.register(rule)

    def register(self, rule: AlertRule):
        # 浅拷贝避免直接修改 DEFAULT_ALERT_RULES 中的原始对象
        # 否则外部 disable 一条规则会污染全局默认规则集
        self._rules[rule.rule_id] = copy.copy(rule)

    def get(self, rule_id: str) -> Optional[AlertRule]:
        return self._rules.get(rule_id)

    def list_all(self) -> List[AlertRule]:
        return list(self._rules.values())

    def list_enabled(self) -> List[AlertRule]:
        return [r for r in self._rules.values() if r.enabled]

    def enable(self, rule_id: str) -> bool:
        if rule_id in self._rules:
            self._rules[rule_id].enabled = True
            return True
        return False

    def disable(self, rule_id: str) -> bool:
        if rule_id in self._rules:
            self._rules[rule_id].enabled = False
            return True
        return False

    def by_category(self, category: AlertCategory) -> List[AlertRule]:
        return [r for r in self._rules.values() if r.category == category and r.enabled]

    def to_dict(self) -> dict:
        return {
            rid: r.to_dict() for rid, r in self._rules.items()
        }


# 全局默认注册中心
default_registry = AlertRuleRegistry()
