"""
多维度分析对比工具

功能:
- 对比多次历史分析结果
- 多维度指标对比（交易数、规则命中、风险分布、性能、规则分布）
- 识别趋势和离群值
- 数据指纹比对（识别是否同一份数据集）

严格遵守戒律:
- M1: 仅基于真实历史记录，不编造
- M4: 完整记录对比维度，便于审计追溯
- P1: 异常运行（离群值）需提示，可能存在配置问题或数据异常
"""
import os
import json
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict


# 离群值检测阈值（基于均值的标准差倍数）
OUTLIER_STD_THRESHOLD = 2.0


class AnalysisComparator:
    """
    多维度分析对比工具

    用法:
        comparator = AnalysisComparator(history_manager)

        # 1. 对比多个执行记录
        comparison = comparator.compare(["exec_id_a", "exec_id_b", "exec_id_c"])

        # 2. 查找指标趋势
        trend = comparator.find_trend("rule_hit_count", limit=10)

        # 3. 找出离群值
        outliers = comparator.find_outliers("duration_seconds")

        # 4. 两个执行记录详细对比
        diff = comparator.compare_two("exec_id_a", "exec_id_b")
    """

    # 核心对比指标
    METRIC_DEFINITIONS = {
        "transactions_count": {
            "label": "交易笔数",
            "field": "transactions_count",
            "unit": "笔",
            "higher_is": "neutral",  # 越高无好坏
        },
        "rule_hit_count": {
            "label": "规则命中数",
            "field": "rule_hit_count",
            "unit": "笔",
            "higher_is": "risk",  # 越高表示可疑越多
        },
        "llm_confirmed_count": {
            "label": "LLM确认数",
            "field": "llm_confirmed_count",
            "unit": "笔",
            "higher_is": "risk",
        },
        "report_count": {
            "label": "生成报告数",
            "field": "report_count",
            "unit": "份",
            "higher_is": "risk",
        },
        "duration_seconds": {
            "label": "总耗时",
            "field": "duration_seconds",
            "unit": "秒",
            "higher_is": "bad",
        },
        "high_risk_reports": {
            "label": "高风险报告数",
            "field": "risk_distribution",  # 特殊处理：从 risk_distribution 提取
            "unit": "份",
            "higher_is": "risk",
            "is_composite": True,  # 复合字段，需要从 risk_distribution 提取
        },
    }

    def __init__(self, history_manager):
        """
        Args:
            history_manager: HistoryManager 实例
        """
        self.hm = history_manager

    # ============================================================
    # 多记录对比
    # ============================================================
    def compare(self, execution_ids: List[str]) -> Dict[str, Any]:
        """
        对比多个执行记录

        戒律 M1: 仅使用真实历史记录，不编造数据
        戒律 M4: 完整保留所有对比维度

        Args:
            execution_ids: 执行ID列表（至少2个）

        Returns:
            {
                "records": [...],          # 每个记录的摘要
                "metrics": {...},          # 各指标对比
                "risk_distribution": {...}, # 风险分布对比
                "rule_details": {...},     # 各规则命中数对比
                "data_fingerprints": {...},# 数据指纹对比
                "warnings": [...],         # 戒律警告
            }
        """
        # 参数校验
        if not execution_ids:
            return self._empty_comparison("未提供执行ID")
        if len(execution_ids) == 1:
            return self._empty_comparison("至少需要2个执行ID才能对比")

        # 加载所有记录（戒律 M1: 真实数据）
        records = []
        missing_ids = []
        for eid in execution_ids:
            r = self.hm.get_run(eid)
            if r is None:
                missing_ids.append(eid)
            else:
                records.append(r)

        if missing_ids:
            return self._empty_comparison(
                f"以下执行ID不存在: {', '.join(missing_ids)}"
            )

        if len(records) < 2:
            return self._empty_comparison("有效记录不足2条")

        # 各指标对比
        metrics_comparison = self._compare_metrics(records)

        # 风险分布对比
        risk_comparison = self._compare_risk_distribution(records)

        # 各规则命中数对比
        rule_comparison = self._compare_rule_details(records)

        # 数据指纹对比
        fingerprints = self._compare_fingerprints(records)

        # 戒律警告
        warnings = self._generate_warnings(records, metrics_comparison, fingerprints)

        return {
            "records": [self._summarize(r) for r in records],
            "metrics": metrics_comparison,
            "risk_distribution": risk_comparison,
            "rule_details": rule_comparison,
            "data_fingerprints": fingerprints,
            "warnings": warnings,
        }

    def _empty_comparison(self, message: str) -> Dict[str, Any]:
        """空对比结果"""
        return {
            "records": [],
            "metrics": {},
            "risk_distribution": {},
            "rule_details": {},
            "data_fingerprints": {},
            "warnings": [message] if message else [],
        }

    def _summarize(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """生成记录摘要（轻量）"""
        return {
            "execution_id": record.get("execution_id", ""),
            "timestamp": record.get("timestamp", ""),
            "analysis_date": record.get("analysis_date", ""),
            "transactions_count": record.get("transactions_count", 0),
            "rule_hit_count": record.get("rule_hit_count", 0),
            "llm_confirmed_count": record.get("llm_confirmed_count", 0),
            "report_count": record.get("report_count", 0),
            "duration_seconds": record.get("duration_seconds", 0),
            "interrupted": record.get("interrupted", False),
            "error": record.get("error", ""),
        }

    # ============================================================
    # 指标对比
    # ============================================================
    def _compare_metrics(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """对比核心指标"""
        result = {}
        for metric_key, definition in self.METRIC_DEFINITIONS.items():
            if definition.get("is_composite"):
                # 复合指标：从 risk_distribution 提取
                values = []
                for r in records:
                    risk_dist = r.get("risk_distribution", {})
                    # 高风险 = critical + high
                    high_count = risk_dist.get("critical", 0) + risk_dist.get("high", 0)
                    values.append(high_count)
            else:
                field = definition["field"]
                values = [r.get(field, 0) for r in records]

            result[metric_key] = {
                "label": definition["label"],
                "unit": definition["unit"],
                "values": values,
                "min": min(values) if values else 0,
                "max": max(values) if values else 0,
                "avg": sum(values) / len(values) if values else 0,
                "delta": max(values) - min(values) if values else 0,
                "delta_ratio": (
                    (max(values) - min(values)) / min(values)
                    if values and min(values) > 0 else 0.0
                ),
            }

        return result

    def _compare_risk_distribution(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """对比风险等级分布"""
        levels = ["critical", "high", "medium", "low", "unknown"]
        comparison = {level: [] for level in levels}

        for r in records:
            risk_dist = r.get("risk_distribution", {})
            for level in levels:
                comparison[level].append(risk_dist.get(level, 0))

        # 加汇总信息
        result = {}
        for level in levels:
            values = comparison[level]
            result[level] = {
                "values": values,
                "total": sum(values),
                "min": min(values) if values else 0,
                "max": max(values) if values else 0,
            }
        return result

    def _compare_rule_details(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """对比各规则命中数"""
        # 收集所有规则名
        all_rules = set()
        for r in records:
            rule_details = r.get("rule_details", {})
            all_rules.update(rule_details.keys())

        comparison = {}
        for rule in sorted(all_rules):
            values = []
            for r in records:
                rule_details = r.get("rule_details", {})
                values.append(rule_details.get(rule, 0))
            comparison[rule] = {
                "values": values,
                "total": sum(values),
                "min": min(values),
                "max": max(values),
                "delta": max(values) - min(values),
            }
        return comparison

    def _compare_fingerprints(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """对比数据指纹（识别是否同一份数据集）"""
        hashes = [r.get("transactions_hash", "") for r in records]
        unique_hashes = set(h for h in hashes if h)

        return {
            "hashes": hashes,
            "all_same": len(unique_hashes) <= 1,
            "unique_count": len(unique_hashes),
            "is_same_dataset": len(unique_hashes) == 1,
        }

    def _generate_warnings(
        self,
        records: List[Dict[str, Any]],
        metrics: Dict[str, Any],
        fingerprints: Dict[str, Any],
    ) -> List[str]:
        """生成对比中的戒律警告"""
        warnings = []

        # 戒律 P1: 高风险报告数突降警告
        high_risk_metric = metrics.get("high_risk_reports", {})
        if high_risk_metric:
            values = high_risk_metric["values"]
            if len(values) >= 2 and max(values) > 0:
                # 后续运行高风险报告数为 0 但之前不为 0
                if values[-1] == 0 and values[0] > 0:
                    warnings.append(
                        f"高风险报告数从 {values[0]} 降至 {values[-1]}，"
                        f"可能存在配置调整或数据问题（戒律 P1: 不遗漏）"
                    )

        # 数据指纹完全不同，但用户在对比（提示数据集不同）
        if not fingerprints["all_same"] and len(records) > 1:
            warnings.append(
                "对比的执行记录使用了不同的交易数据集，"
                "指标差异可能来自数据本身而非系统变化"
            )

        # 中断的运行
        for r in records:
            if r.get("interrupted", False):
                warnings.append(
                    f"执行 {r.get('execution_id')} 被中断，"
                    f"指标可能不完整"
                )

        # 报错运行
        for r in records:
            err = r.get("error", "")
            if err:
                warnings.append(
                    f"执行 {r.get('execution_id')} 有错误: {err}"
                )

        return warnings

    # ============================================================
    # 两两详细对比
    # ============================================================
    def compare_two(self, exec_id_a: str, exec_id_b: str) -> Dict[str, Any]:
        """
        两个执行记录的详细对比

        Returns:
            {
                "record_a": {...},
                "record_b": {...},
                "metric_diffs": {...},     # 各指标差异
                "risk_diff": {...},         # 风险分布差异
                "rule_diff": {...},         # 规则命中差异
                "is_same_dataset": bool,
                "summary": str,             # 文字摘要
            }
        """
        ra = self.hm.get_run(exec_id_a)
        rb = self.hm.get_run(exec_id_b)

        if ra is None:
            return {"error": f"执行ID不存在: {exec_id_a}"}
        if rb is None:
            return {"error": f"执行ID不存在: {exec_id_b}"}

        # 各指标差异
        metric_diffs = {}
        for metric_key, definition in self.METRIC_DEFINITIONS.items():
            if definition.get("is_composite"):
                risk_dist_a = ra.get("risk_distribution", {})
                risk_dist_b = rb.get("risk_distribution", {})
                va = risk_dist_a.get("critical", 0) + risk_dist_a.get("high", 0)
                vb = risk_dist_b.get("critical", 0) + risk_dist_b.get("high", 0)
            else:
                field = definition["field"]
                va = ra.get(field, 0)
                vb = rb.get(field, 0)

            diff = vb - va
            pct = (diff / va * 100) if va != 0 else (float("inf") if diff > 0 else 0.0)
            metric_diffs[metric_key] = {
                "label": definition["label"],
                "value_a": va,
                "value_b": vb,
                "diff": diff,
                "diff_pct": pct,
            }

        # 风险分布差异
        levels = ["critical", "high", "medium", "low", "unknown"]
        risk_diff = {}
        for level in levels:
            va = ra.get("risk_distribution", {}).get(level, 0)
            vb = rb.get("risk_distribution", {}).get(level, 0)
            risk_diff[level] = {
                "value_a": va,
                "value_b": vb,
                "diff": vb - va,
            }

        # 规则命中差异
        rule_details_a = ra.get("rule_details", {})
        rule_details_b = rb.get("rule_details", {})
        all_rules = set(rule_details_a.keys()) | set(rule_details_b.keys())
        rule_diff = {}
        for rule in sorted(all_rules):
            va = rule_details_a.get(rule, 0)
            vb = rule_details_b.get(rule, 0)
            rule_diff[rule] = {
                "value_a": va,
                "value_b": vb,
                "diff": vb - va,
            }

        # 数据指纹
        hash_a = ra.get("transactions_hash", "")
        hash_b = rb.get("transactions_hash", "")
        is_same_dataset = bool(hash_a) and hash_a == hash_b

        # 生成摘要
        summary_parts = []
        if is_same_dataset:
            summary_parts.append("两次分析使用了相同数据集")
        else:
            summary_parts.append("两次分析使用了不同数据集")

        rule_delta = metric_diffs.get("rule_hit_count", {}).get("diff", 0)
        if rule_delta != 0:
            summary_parts.append(
                f"规则命中数 {'增加' if rule_delta > 0 else '减少'} {abs(rule_delta)} 笔"
            )

        duration_delta = metric_diffs.get("duration_seconds", {}).get("diff", 0)
        if abs(duration_delta) > 0.5:
            summary_parts.append(
                f"耗时 {'增加' if duration_delta > 0 else '减少'} {abs(duration_delta):.2f} 秒"
            )

        return {
            "record_a": self._summarize(ra),
            "record_b": self._summarize(rb),
            "metric_diffs": metric_diffs,
            "risk_diff": risk_diff,
            "rule_diff": rule_diff,
            "is_same_dataset": is_same_dataset,
            "summary": "；".join(summary_parts) + "。",
        }

    # ============================================================
    # 趋势分析
    # ============================================================
    def find_trend(self, metric: str, limit: int = 20) -> Dict[str, Any]:
        """
        查找指定指标的趋势

        Args:
            metric: 指标名（参考 METRIC_DEFINITIONS）
            limit: 最大返回记录数

        Returns:
            {
                "metric": str,
                "label": str,
                "data_points": [(timestamp, value), ...],  # 按时间正序
                "trend": "rising" | "falling" | "stable",
                "min": number,
                "max": number,
                "avg": number,
            }
        """
        if metric not in self.METRIC_DEFINITIONS:
            raise ValueError(
                f"未知指标: {metric}，可用: {list(self.METRIC_DEFINITIONS.keys())}"
            )

        definition = self.METRIC_DEFINITIONS[metric]
        runs = self.hm.list_runs(limit=limit)

        # 倒序返回，我们想按时间正序分析趋势
        runs = list(reversed(runs))

        data_points = []
        for run in runs:
            eid = run.get("execution_id")
            full = self.hm.get_run(eid) if eid else None
            if full is None:
                continue

            if definition.get("is_composite"):
                risk_dist = full.get("risk_distribution", {})
                value = risk_dist.get("critical", 0) + risk_dist.get("high", 0)
            else:
                value = full.get(definition["field"], 0)

            data_points.append((run.get("timestamp", ""), value))

        if not data_points:
            return {
                "metric": metric,
                "label": definition["label"],
                "data_points": [],
                "trend": "stable",
                "min": 0,
                "max": 0,
                "avg": 0,
            }

        values = [v for _, v in data_points]
        trend = self._detect_trend(values)

        return {
            "metric": metric,
            "label": definition["label"],
            "data_points": data_points,
            "trend": trend,
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
        }

    @staticmethod
    def _detect_trend(values: List[float]) -> str:
        """检测趋势：rising/falling/stable"""
        if len(values) < 2:
            return "stable"

        # 简单线性回归斜率
        n = len(values)
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n

        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return "stable"

        slope = numerator / denominator

        # 用均值比例判定显著性
        if y_mean == 0:
            return "stable"
        rel_slope = slope / abs(y_mean)
        if rel_slope > 0.05:
            return "rising"
        if rel_slope < -0.05:
            return "falling"
        return "stable"

    # ============================================================
    # 离群值检测
    # ============================================================
    def find_outliers(
        self,
        metric: str,
        threshold: float = OUTLIER_STD_THRESHOLD,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        找出指标离群值（戒律 P1: 异常运行应关注）

        Args:
            metric: 指标名
            threshold: 离群值阈值（标准差倍数）
            limit: 检查最近多少条记录

        Returns:
            {
                "metric": str,
                "label": str,
                "threshold": float,
                "mean": float,
                "std": float,
                "outliers": [...],  # 离群记录
                "total_checked": int,
            }
        """
        if metric not in self.METRIC_DEFINITIONS:
            raise ValueError(
                f"未知指标: {metric}，可用: {list(self.METRIC_DEFINITIONS.keys())}"
            )

        definition = self.METRIC_DEFINITIONS[metric]
        runs = self.hm.list_runs(limit=limit)

        # 收集所有值
        records = []
        for run in runs:
            eid = run.get("execution_id")
            full = self.hm.get_run(eid) if eid else None
            if full is None:
                continue

            if definition.get("is_composite"):
                risk_dist = full.get("risk_distribution", {})
                value = risk_dist.get("critical", 0) + risk_dist.get("high", 0)
            else:
                value = full.get(definition["field"], 0)

            records.append({
                "execution_id": eid,
                "timestamp": run.get("timestamp", ""),
                "value": value,
                "summary": self._summarize(full),
            })

        if len(records) < 3:
            # 数据太少无法判定离群值
            return {
                "metric": metric,
                "label": definition["label"],
                "threshold": threshold,
                "mean": sum(r["value"] for r in records) / len(records) if records else 0,
                "std": 0.0,
                "outliers": [],
                "total_checked": len(records),
                "message": "数据点不足（<3），无法判定离群值",
            }

        values = [r["value"] for r in records]
        mean = sum(values) / len(values)
        # 总体标准差
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = variance ** 0.5

        outliers = []
        if std > 0:
            for r in records:
                z_score = abs(r["value"] - mean) / std
                if z_score >= threshold:
                    r_copy = dict(r)
                    r_copy["z_score"] = round(z_score, 2)
                    r_copy["deviation"] = r["value"] - mean
                    outliers.append(r_copy)

        # 按偏离程度排序
        outliers.sort(key=lambda x: x["z_score"], reverse=True)

        return {
            "metric": metric,
            "label": definition["label"],
            "threshold": threshold,
            "mean": round(mean, 2),
            "std": round(std, 2),
            "outliers": outliers,
            "total_checked": len(records),
        }

    # ============================================================
    # 综合统计
    # ============================================================
    def overview(self, limit: int = 30) -> Dict[str, Any]:
        """
        综合概览：最近 N 次运行的关键指标统计

        Returns:
            {
                "total_runs": int,
                "date_range": [start, end],
                "metric_stats": {metric: {min, max, avg, last}},
                "trends": {metric: trend},
            }
        """
        runs = self.hm.list_runs(limit=limit)
        if not runs:
            return {
                "total_runs": 0,
                "date_range": [],
                "metric_stats": {},
                "trends": {},
            }

        runs = list(reversed(runs))  # 时间正序

        # 收集所有指标值
        metric_values: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        for run in runs:
            eid = run.get("execution_id")
            full = self.hm.get_run(eid) if eid else None
            if full is None:
                continue
            ts = run.get("timestamp", "")

            for metric_key, definition in self.METRIC_DEFINITIONS.items():
                if definition.get("is_composite"):
                    risk_dist = full.get("risk_distribution", {})
                    value = risk_dist.get("critical", 0) + risk_dist.get("high", 0)
                else:
                    value = full.get(definition["field"], 0)
                metric_values[metric_key].append((ts, value))

        # 计算各指标统计
        metric_stats = {}
        trends = {}
        for metric_key, ts_values in metric_values.items():
            values = [v for _, v in ts_values]
            if not values:
                continue
            metric_stats[metric_key] = {
                "label": self.METRIC_DEFINITIONS[metric_key]["label"],
                "min": min(values),
                "max": max(values),
                "avg": sum(values) / len(values),
                "last": values[-1],
            }
            trends[metric_key] = self._detect_trend(values)

        return {
            "total_runs": len(runs),
            "date_range": [runs[0].get("timestamp", ""), runs[-1].get("timestamp", "")],
            "metric_stats": metric_stats,
            "trends": trends,
        }
