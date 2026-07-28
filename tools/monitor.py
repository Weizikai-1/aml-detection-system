"""
监控告警引擎 (Monitor)

职责:
- 实时监控：监听工作流关键事件，自动评估告警规则
- 离线监控：批量检查历史分析结果，触发延迟告警
- 抑制去重：避免相同告警风暴
- 通知分发：触发告警后通过通知管理器分发

设计原则:
- M1: 所有监控基于真实数据（state、评估结果、运行指标）
- P1: 紧急告警不被抑制，关键事件不遗漏
- P2: 普通告警有抑制窗口，避免噪声
- M4: 所有跳过/降级均有日志，可追溯
- 错误隔离: 任意环节失败不影响主流程
"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from tools.alert_rules import (
    AlertRule,
    AlertRuleRegistry,
    AlertSeverity,
    AlertCategory,
    default_registry,
)
from tools.alert_history import Alert, AlertHistory
from tools.notifier import NotificationManager, create_default_manager


def _safe_get_threshold(rule: Optional[AlertRule], default: float) -> float:
    """安全获取阈值，避免 Python falsy 问题（threshold=0 被误判）"""
    if rule is None or rule.threshold is None:
        return default
    return float(rule.threshold)


class Monitor:
    """
    监控告警引擎

    串联 规则注册中心 + 告警历史 + 通知管理器
    """

    def __init__(
        self,
        rule_registry: AlertRuleRegistry = None,
        history: AlertHistory = None,
        notifier: NotificationManager = None,
    ):
        self.rules = rule_registry or default_registry
        self.history = history or AlertHistory()
        self.notifier = notifier or create_default_manager()

    # ============================================================
    # 核心触发接口
    # ============================================================
    def trigger(
        self,
        rule_id: str,
        message: str,
        context: Optional[dict] = None,
        force: bool = False,
    ) -> Optional[Alert]:
        """
        触发告警

        Args:
            rule_id: 告警规则ID
            message: 告警消息
            context: 上下文（来源数据）
            force: 强制触发（跳过抑制窗口）

        Returns:
            告警对象（被抑制时返回None）
        """
        rule = self.rules.get(rule_id)
        if rule is None:
            print(f"  [告警] 未知规则: {rule_id}")
            return None

        if not rule.enabled:
            return None

        # 抑制窗口检查（紧急级别不抑制）
        if not force and rule.severity != AlertSeverity.EMERGENCY:
            if rule.suppress_window_sec > 0:
                last_time = self.history.get_last_trigger_time(rule_id)
                if last_time:
                    try:
                        last_dt = datetime.fromisoformat(last_time)
                        now_dt = datetime.now()
                        elapsed = (now_dt - last_dt).total_seconds()
                        if elapsed < rule.suppress_window_sec:
                            return None
                    except Exception:
                        pass

        # 构造告警
        alert = Alert(
            alert_id=f"alert_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
            rule_id=rule.rule_id,
            rule_name=rule.name,
            severity=rule.severity.value,
            category=rule.category.value,
            message=message,
            triggered_at=datetime.now().isoformat(),
            context=context or {},
        )

        # 保存历史（错误隔离：失败不中断主流程）
        try:
            self.history.save_alert(alert)
        except Exception as e:
            print(f"  [告警] 保存历史失败: {e}")

        # 通知分发（错误隔离：失败不中断主流程）
        # 戒律 P1: 关键告警（critical/emergency）走 notify_critical 多渠道兜底
        try:
            if alert.severity in ("critical", "emergency"):
                results = self.notifier.notify_critical(alert)
            else:
                results = self.notifier.notify(alert)
            alert.notification_sent = any(results.values()) if results else False
        except Exception as e:
            print(f"  [告警] 通知分发失败: {e}")
            alert.notification_sent = False

        return alert

    # ============================================================
    # 实时工作流事件监控
    # ============================================================
    def check_workflow_state(self, state: dict) -> List[Alert]:
        """
        检查工作流状态，触发相关告警

        Args:
            state: 工作流最终状态

        Returns:
            触发的告警列表
        """
        triggered: List[Alert] = []

        # 1. 检查高风险交易超阈值
        rule_hits = state.get("rule_hits", [])
        high_risk_count = sum(1 for r in rule_hits if r.get("risk_score", 0) >= 70)
        rule = self.rules.get("risk_high_count_threshold")
        if rule:
            threshold = _safe_get_threshold(rule, float("inf"))
            if high_risk_count >= threshold:
                a = self.trigger(
                    "risk_high_count_threshold",
                    f"本批次高风险交易（≥70分）{high_risk_count}笔，超过阈值{threshold}",
                    {"high_risk_count": high_risk_count, "threshold": threshold},
                )
                if a:
                    triggered.append(a)

        # 2. 检查极严重可疑交易（每笔都触发，不抑制）
        for hit in rule_hits:
            score = hit.get("risk_score", 0)
            if score >= 85:
                # 安全获取 transaction_id（transaction 可能为 None 或非 dict）
                txn = hit.get("transaction") or {}
                if not isinstance(txn, dict):
                    txn = {}
                tid = txn.get("transaction_id", "unknown")
                a = self.trigger(
                    "risk_critical_transaction",
                    f"发现极严重可疑交易 {tid}（风险分 {score}）",
                    {"transaction_id": tid, "risk_score": score},
                    force=True,  # 紧急告警不抑制
                )
                if a:
                    triggered.append(a)

        # 3. 检查空壳公司识别
        shell_hits = [r for r in rule_hits if "空壳公司" in r.get("rule_hits", [])]
        if shell_hits:
            a = self.trigger(
                "risk_shell_company_detected",
                f"本批次检测到 {len(shell_hits)} 笔空壳公司特征交易",
                {"shell_count": len(shell_hits)},
            )
            if a:
                triggered.append(a)

        # 4. 检查累犯账户（评分>=70 且 evidence 中明确包含"高度累犯"标记）
        repeat_hits = []
        for r in rule_hits:
            if r.get("risk_score", 0) < 70:
                continue
            evidence = r.get("evidence", [])
            if not isinstance(evidence, list):
                continue
            # 精确匹配：evidence 列表中存在包含"高度累犯"的字符串元素
            has_repeat = any(
                isinstance(e, str) and "高度累犯" in e
                for e in evidence
            )
            if has_repeat:
                repeat_hits.append(r)
        if repeat_hits:
            a = self.trigger(
                "risk_repeat_offender",
                f"本批次 {len(repeat_hits)} 笔交易来自高度累犯账户",
                {"repeat_count": len(repeat_hits)},
            )
            if a:
                triggered.append(a)

        # 5. 检查数据质量
        quality = state.get("preprocessing_stats", {}).get("quality_score", 1.0)
        rule = self.rules.get("health_data_quality_low")
        if rule:
            threshold = _safe_get_threshold(rule, 0.0)
            if quality < threshold:
                a = self.trigger(
                    "health_data_quality_low",
                    f"数据质量评分 {quality:.2f} 低于阈值 {threshold}",
                    {"quality_score": quality, "threshold": threshold},
                )
                if a:
                    triggered.append(a)

        # 6. 检查性能
        total_time = state.get("total_processing_time", 0)
        rule = self.rules.get("perf_analysis_too_slow")
        if rule:
            threshold = _safe_get_threshold(rule, float("inf"))
            if total_time > threshold:
                a = self.trigger(
                    "perf_analysis_too_slow",
                    f"分析总耗时 {total_time:.2f}s 超过阈值 {threshold}s",
                    {"total_time": total_time, "threshold": threshold},
                )
                if a:
                    triggered.append(a)

        # 7. 单节点耗时
        step_times = state.get("step_times", {})
        rule = self.rules.get("perf_node_too_slow")
        if rule:
            threshold = _safe_get_threshold(rule, float("inf"))
            for step, t in step_times.items():
                if t > threshold:
                    a = self.trigger(
                        "perf_node_too_slow",
                        f"节点 {step} 耗时 {t:.2f}s 超过阈值 {threshold}s",
                        {"step": step, "time": t, "threshold": threshold},
                    )
                    if a:
                        triggered.append(a)

        # 8. 合规审核驳回
        rejected = state.get("rejected_reports", [])
        if rejected:
            a = self.trigger(
                "compliance_report_rejected",
                f"合规审核驳回 {len(rejected)} 份报告",
                {"rejected_count": len(rejected)},
            )
            if a:
                triggered.append(a)

        # 9. 合规人工审核过多
        human_review_tasks = state.get("human_review_tasks", [])
        compliance_stats = state.get("compliance_stats", {})
        total_reports = compliance_stats.get("total", 0)
        rule = self.rules.get("compliance_human_review_high")
        if rule and total_reports > 0:
            threshold = _safe_get_threshold(rule, 1.0)
            review_ratio = len(human_review_tasks) / total_reports
            if review_ratio > threshold:
                a = self.trigger(
                    "compliance_human_review_high",
                    f"人工审核比例 {review_ratio:.1%} 超过阈值 {threshold:.1%}"
                    f"（{len(human_review_tasks)}/{total_reports}）",
                    {
                        "review_count": len(human_review_tasks),
                        "total_reports": total_reports,
                        "ratio": review_ratio,
                        "threshold": threshold,
                    },
                )
                if a:
                    triggered.append(a)

        # 10. 节点执行失败（戒律 P1: 不遗漏系统故障）
        node_errors = self._extract_node_errors(state)
        if node_errors:
            a = self.trigger(
                "health_node_failure",
                f"检测到 {len(node_errors)} 个节点执行失败: "
                + ", ".join(n.get("node", "unknown") for n in node_errors[:3]),
                {
                    "failed_nodes": [n.get("node", "unknown") for n in node_errors],
                    "error_count": len(node_errors),
                },
            )
            if a:
                triggered.append(a)

        # 11. 缓存命中率过低
        rule_engine_stats = state.get("rule_engine_stats", {})
        if rule_engine_stats.get("cache_hit") is False:
            # 仅在缓存已启用时告警（cache_hit 字段存在且为 False 表示尝试过但未命中）
            cache_enabled = state.get("analysis_params", {}).get("use_cache", False)
            if cache_enabled:
                rule = self.rules.get("health_cache_miss_rate_high")
                if rule:
                    a = self.trigger(
                        "health_cache_miss_rate_high",
                        "规则引擎缓存未命中（可能系统压力增加或首次运行）",
                        {"cache_hit": False},
                    )
                    if a:
                        triggered.append(a)

        # 12. 中断事件
        if state.get("interrupted"):
            a = self.trigger(
                "workflow_interrupted",
                f"分析流程在 {state.get('current_step', '未知')} 被中断",
                {"current_step": state.get("current_step")},
                force=True,
            )
            if a:
                triggered.append(a)

        # 13. 未发现可疑
        # 戒律 P2: 仅在有真实交易数据时才告警，避免空数据集误报
        total_transactions = len(state.get("transactions", []) or [])
        if not rule_hits and not state.get("interrupted") and total_transactions > 0:
            a = self.trigger(
                "workflow_no_suspicious",
                "本次分析未发现任何可疑交易，请确认数据完整性",
                {"total_transactions": total_transactions},
            )
            if a:
                triggered.append(a)

        return triggered

    def _extract_node_errors(self, state: dict) -> List[Dict[str, Any]]:
        """从工作流状态中提取节点错误信息

        节点失败时 graph_setup.py 会写入 _node_meta.status="error" 和 _node_error
        """
        errors = []
        # 方式1: _node_error 字段（单个节点失败）
        node_error = state.get("_node_error")
        if node_error and isinstance(node_error, dict):
            errors.append(node_error)
        # 方式2: _node_meta.status == "error"
        node_meta = state.get("_node_meta")
        if node_meta and isinstance(node_meta, dict) and node_meta.get("status") == "error":
            if not errors:  # 避免重复
                errors.append(node_meta)
        return errors

    # ============================================================
    # 离线评估对比告警
    # ============================================================
    def check_evaluation_regression(
        self,
        baseline,
        current,
    ) -> List[Alert]:
        """
        检查评估指标回归

        Args:
            baseline: 基线 EvaluationResult
            current: 当前 EvaluationResult

        Returns:
            触发的告警列表
        """
        from tools.eval_regression import compare_evaluations
        report = compare_evaluations(baseline, current)
        triggered: List[Alert] = []

        for d in report.degraded:
            rule_id = f"eval_{d.metric}_drop"
            if self.rules.get(rule_id) is None:
                # M4: 未知规则跳过时记录日志，可追溯
                print(f"  [告警] 评估指标 {d.metric} 下降，但无对应告警规则，跳过")
                continue
            a = self.trigger(
                rule_id,
                f"评估 {d.metric} 下降 {abs(d.delta_pct):.1%}（{d.baseline:.4f} -> {d.current:.4f}）",
                {
                    "metric": d.metric,
                    "baseline": d.baseline,
                    "current": d.current,
                    "delta": d.delta,
                },
            )
            if a:
                triggered.append(a)

        return triggered

    # ============================================================
    # 告警统计
    # ============================================================
    def get_stats(self) -> dict:
        return {
            "rules": {
                "total": len(self.rules.list_all()),
                "enabled": len(self.rules.list_enabled()),
            },
            "history": self.history.stats(),
        }


# ============================================================
# 便捷单例
# ============================================================
_default_monitor: Optional[Monitor] = None


def get_monitor() -> Monitor:
    """获取默认监控器（单例）"""
    global _default_monitor
    if _default_monitor is None:
        _default_monitor = Monitor()
    return _default_monitor
