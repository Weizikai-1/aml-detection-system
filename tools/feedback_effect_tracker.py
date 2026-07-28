"""
反馈效果追踪报告 (Feedback Effect Tracker)

职责:
- 追踪分析师反馈对系统改进的实际效果
- 对比反馈前后系统表现（误报率、漏报率、准确率变化）
- 生成结构化效果追踪报告

戒律遵循:
- M1: 基于真实反馈和运行数据，不编造结果
- M2: 报告包含完整的指标对比和分析
- M4: 报告可追溯，记录生成时间和数据来源
- P1: 关注漏报率变化（不遗漏）
- P2: 关注误报率变化（不误报）

存储结构:
    data/feedback/
        ├── effect_snapshots.json     # 指标快照序列
        └── effect_reports/           # 生成的报告
            └── <report_id>.json
"""
import os
import json
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple


def _now_str() -> str:
    return datetime.now().isoformat()


def _now_ts() -> float:
    return datetime.now().timestamp()


class FeedbackEffectTracker:
    """
    反馈效果追踪器

    用法:
        tracker = FeedbackEffectTracker()

        # 记录系统指标快照
        tracker.record_snapshot({
            "false_positive_rate": 0.35,
            "false_negative_rate": 0.12,
            "accuracy_rate": 0.78,
            "total_feedback": 10,
        })

        # 生成效果追踪报告
        report = tracker.generate_report(start_ts, end_ts)
    """

    def __init__(self, feedback_dir: str = ""):
        """
        Args:
            feedback_dir: 反馈数据目录，默认使用 FEEDBACK_DIR
        """
        if not feedback_dir:
            from config import FEEDBACK_DIR
            feedback_dir = FEEDBACK_DIR
        self.feedback_dir = feedback_dir
        self.snapshots_path = os.path.join(feedback_dir, "effect_snapshots.json")
        self.reports_dir = os.path.join(feedback_dir, "effect_reports")
        os.makedirs(self.reports_dir, exist_ok=True)

    # ============================================================
    # 快照管理
    # ============================================================
    def record_snapshot(
        self,
        metrics: Dict[str, Any],
        description: str = "",
    ) -> str:
        """
        记录系统指标快照

        戒律:
        - M1: 快照基于真实系统指标，不编造
        - M4: 记录时间戳，可追溯

        Args:
            metrics: 指标字典，建议包含:
                - false_positive_rate: 误报率
                - false_negative_rate: 漏报率
                - accuracy_rate: 准确率
                - total_feedback: 总反馈数
                - rule_hit_count: 规则命中数
                - report_count: 报告数
            description: 快照描述

        Returns:
            snapshot_id
        """
        snapshot_id = f"S-{uuid.uuid4().hex[:8].upper()}"
        snapshot = {
            "snapshot_id": snapshot_id,
            "timestamp": _now_str(),
            "created_at": _now_ts(),
            "description": description or "定期快照",
            "metrics": dict(metrics),
        }

        snapshots = self._load_snapshots()
        snapshots.append(snapshot)
        # 戒律 P4: 原子写入
        tmp_path = self.snapshots_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(snapshots, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.snapshots_path)
        except OSError as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise RuntimeError(f"快照保存失败: {e}") from e

        return snapshot_id

    def _load_snapshots(self) -> List[Dict[str, Any]]:
        """加载所有快照"""
        if not os.path.exists(self.snapshots_path):
            return []
        try:
            with open(self.snapshots_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def list_snapshots(
        self,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """列出指定时间段的快照"""
        snapshots = self._load_snapshots()
        results = []
        for s in snapshots:
            created = s.get("created_at", 0.0)
            try:
                created = float(created)
            except (TypeError, ValueError):
                continue
            if start_ts is not None and created < start_ts:
                continue
            if end_ts is not None and created > end_ts:
                continue
            results.append(s)
        return results

    # ============================================================
    # 效果追踪报告
    # ============================================================
    def generate_report(
        self,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
        feedback_manager=None,
    ) -> Dict[str, Any]:
        """
        生成反馈效果追踪报告

        戒律:
        - M1: 基于真实快照和反馈数据，不编造
        - M2: 报告包含完整指标对比
        - M4: 记录报告生成时间和数据来源
        - P1: 重点关注漏报率变化
        - P2: 重点关注误报率变化

        Args:
            start_ts: 起始时间戳（秒），None表示从最早快照开始
            end_ts: 结束时间戳（秒），None表示到最新快照
            feedback_manager: 反馈管理器实例（可选，用于获取反馈统计）

        Returns:
            报告字典
        """
        snapshots = self.list_snapshots(start_ts=start_ts, end_ts=end_ts)

        report_id = f"RPT-{uuid.uuid4().hex[:8].upper()}"
        report = {
            "report_id": report_id,
            "generated_at": _now_str(),
            "period": {
                "start_ts": start_ts,
                "end_ts": end_ts,
            },
            "snapshot_count": len(snapshots),
            "data_sources": ["effect_snapshots"],
        }

        if not snapshots:
            report["summary"] = "无快照数据，无法生成效果分析"
            report["metrics_comparison"] = None
            report["improvement"] = None
            self._save_report(report)
            return report

        # 提取首尾快照的指标
        first = snapshots[0]
        last = snapshots[-1]
        first_metrics = first.get("metrics", {})
        last_metrics = last.get("metrics", {})

        # 指标对比
        comparison = self._compare_metrics(first_metrics, last_metrics)
        report["metrics_comparison"] = comparison

        # 改进幅度评估
        improvement = self._assess_improvement(first_metrics, last_metrics)
        report["improvement"] = improvement

        # 反馈统计（如果提供了 feedback_manager）
        if feedback_manager is not None:
            fb_stats = self._collect_feedback_stats(
                feedback_manager, start_ts, end_ts
            )
            report["feedback_stats"] = fb_stats
            report["data_sources"].append("feedback_manager")
        else:
            report["feedback_stats"] = None

        # 趋势分析
        report["trend"] = self._analyze_trend(snapshots)

        # 摘要
        report["summary"] = self._build_summary(
            first_metrics, last_metrics, improvement
        )

        self._save_report(report)
        return report

    def _compare_metrics(
        self,
        first: Dict[str, Any],
        last: Dict[str, Any],
    ) -> Dict[str, Any]:
        """对比首尾指标"""
        keys = set(first.keys()) | set(last.keys())
        comparison = {}
        for key in keys:
            v1 = first.get(key)
            v2 = last.get(key)
            if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
                comparison[key] = {
                    "before": v1,
                    "after": v2,
                    "delta": round(v2 - v1, 4),
                    "delta_percent": (
                        round((v2 - v1) / v1 * 100, 2) if v1 != 0 else None
                    ),
                }
            else:
                comparison[key] = {"before": v1, "after": v2}
        return comparison

    def _assess_improvement(
        self,
        first: Dict[str, Any],
        last: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        评估改进幅度

        戒律:
        - P1: 漏报率下降是改进（不遗漏）
        - P2: 误报率下降是改进（不误报）
        - 准确率上升是改进
        """
        improvements: List[str] = []
        regressions: List[str] = []

        # 误报率（戒律 P2: 越低越好）
        fp_before = first.get("false_positive_rate")
        fp_after = last.get("false_positive_rate")
        if isinstance(fp_before, (int, float)) and isinstance(fp_after, (int, float)):
            if fp_after < fp_before:
                improvements.append(
                    f"误报率从 {fp_before:.4f} 降至 {fp_after:.4f}（戒律 P2: 改进）"
                )
            elif fp_after > fp_before:
                regressions.append(
                    f"误报率从 {fp_before:.4f} 升至 {fp_after:.4f}（戒律 P2: 恶化）"
                )

        # 漏报率（戒律 P1: 越低越好）
        fn_before = first.get("false_negative_rate")
        fn_after = last.get("false_negative_rate")
        if isinstance(fn_before, (int, float)) and isinstance(fn_after, (int, float)):
            if fn_after < fn_before:
                improvements.append(
                    f"漏报率从 {fn_before:.4f} 降至 {fn_after:.4f}（戒律 P1: 改进）"
                )
            elif fn_after > fn_before:
                regressions.append(
                    f"漏报率从 {fn_before:.4f} 升至 {fn_after:.4f}（戒律 P1: 恶化）"
                )

        # 准确率（越高越好）
        acc_before = first.get("accuracy_rate")
        acc_after = last.get("accuracy_rate")
        if isinstance(acc_before, (int, float)) and isinstance(acc_after, (int, float)):
            if acc_after > acc_before:
                improvements.append(
                    f"准确率从 {acc_before:.4f} 升至 {acc_after:.4f}（改进）"
                )
            elif acc_after < acc_before:
                regressions.append(
                    f"准确率从 {acc_before:.4f} 降至 {acc_after:.4f}（恶化）"
                )

        return {
            "improvements": improvements,
            "regressions": regressions,
            "net_improvement": len(improvements) - len(regressions),
        }

    def _collect_feedback_stats(
        self,
        feedback_manager,
        start_ts: Optional[float],
        end_ts: Optional[float],
    ) -> Dict[str, Any]:
        """收集指定时段的反馈统计"""
        all_feedback = feedback_manager.list_feedback(limit=10000)
        in_period = []
        for entry in all_feedback:
            created = entry.get("created_at", 0.0)
            try:
                created = float(created)
            except (TypeError, ValueError):
                continue
            if start_ts is not None and created < start_ts:
                continue
            if end_ts is not None and created > end_ts:
                continue
            in_period.append(entry)

        return {
            "total_in_period": len(in_period),
            "false_positive": sum(
                1 for e in in_period if e.get("feedback_type") == "false_positive"
            ),
            "false_negative": sum(
                1 for e in in_period if e.get("feedback_type") == "false_negative"
            ),
            "confirmed": sum(
                1 for e in in_period if e.get("feedback_type") == "confirmed"
            ),
            "affected_accounts": len(set(
                e.get("account", "") for e in in_period if e.get("account")
            )),
        }

    def _analyze_trend(self, snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
        """分析指标趋势"""
        if len(snapshots) < 2:
            return {"trend": "insufficient_data"}

        # 提取误报率和漏报率的趋势
        fp_trend = []
        fn_trend = []
        acc_trend = []
        for s in snapshots:
            metrics = s.get("metrics", {})
            fp = metrics.get("false_positive_rate")
            fn = metrics.get("false_negative_rate")
            acc = metrics.get("accuracy_rate")
            if isinstance(fp, (int, float)):
                fp_trend.append(fp)
            if isinstance(fn, (int, float)):
                fn_trend.append(fn)
            if isinstance(acc, (int, float)):
                acc_trend.append(acc)

        return {
            "false_positive_trend": fp_trend,
            "false_negative_trend": fn_trend,
            "accuracy_trend": acc_trend,
            "snapshot_count": len(snapshots),
        }

    def _build_summary(
        self,
        first: Dict[str, Any],
        last: Dict[str, Any],
        improvement: Dict[str, Any],
    ) -> str:
        """构建报告摘要"""
        net = improvement.get("net_improvement", 0)
        improvements = improvement.get("improvements", [])
        regressions = improvement.get("regressions", [])

        parts = []
        if improvements:
            parts.append(f"改进项 {len(improvements)} 项")
        if regressions:
            parts.append(f"恶化项 {len(regressions)} 项")

        if net > 0:
            return f"反馈效果积极：{', '.join(parts)}，净改进 {net} 项"
        elif net < 0:
            return f"反馈效果需关注：{', '.join(parts)}，净恶化 {abs(net)} 项"
        else:
            return f"反馈效果中性：{', '.join(parts) or '无明显变化'}"

    def _save_report(self, report: Dict[str, Any]) -> None:
        """保存报告（戒律 P4: 原子写入）"""
        report_id = report.get("report_id", "RPT-UNKNOWN")
        path = os.path.join(self.reports_dir, f"{report_id}.json")
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except OSError as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise RuntimeError(f"报告保存失败: {e}") from e

    # ============================================================
    # 报告查询
    # ============================================================
    def list_reports(self) -> List[Dict[str, Any]]:
        """列出所有已生成的报告"""
        reports = []
        if not os.path.exists(self.reports_dir):
            return reports
        for fname in os.listdir(self.reports_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.reports_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    reports.append(json.load(f))
            except (json.JSONDecodeError, OSError):
                continue
        # 按生成时间倒序
        reports.sort(key=lambda r: r.get("generated_at", ""), reverse=True)
        return reports

    def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        """获取指定报告"""
        path = os.path.join(self.reports_dir, f"{report_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
