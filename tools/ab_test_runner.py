"""
A/B 测试框架 (A/B Test Runner)

职责:
- 在同一交易数据 + 真值集上对比两组规则参数（A=基线 / B=候选）
- 计算各变体的 precision/recall/f1 等指标
- 基于戒律守护给出决策建议（推荐 B / 保留 A / 需人工判断）

戒律遵循:
- M1: 两个变体使用同一数据和真值集，公平对比，不编造结果
- M2: 决策附带理由和指标依据
- M4: 测试结果持久化，可追溯
- P1: B 导致高风险召回下降超过 30% 时拒绝推荐（不遗漏）
- P2: B 导致总命中激增超过 200% 时给出警告（不误报）
- P4: 不修改全局配置，通过 RuleTuner 临时配置运行

设计要点:
- 评估逻辑复用 MultiObjectiveOptimizer._evaluate_params，保证口径一致
- 决策不只看加权得分，还需通过戒律守护门禁
- ground_truth 结构: {transaction_id: is_suspicious(Bool|None)}
"""
import os
import json
import uuid
import copy
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from config import AB_TESTS_DIR


# 戒律守护阈值（与 rule_tuner.py 对齐）
RISK_RECALL_DROP_REJECT_RATIO = 0.30   # 高风险召回下降超过30%拒绝（戒律 P1）
HITS_SURGE_WARNING_RATIO = 2.0         # 总命中激增超过200%警告（戒律 P2）

# 默认指标权重（与 MultiObjectiveOptimizer 对齐）
DEFAULT_METRIC_WEIGHTS: Dict[str, float] = {
    "precision": 0.35,  # 戒律 P2: 不误报
    "recall": 0.35,     # 戒律 P1: 不遗漏
    "f1": 0.30,         # 平衡指标
}


def _now_str() -> str:
    return datetime.now().isoformat()


# ============================================================
# 数据结构
# ============================================================
class ABTestVariant:
    """A/B 测试的单个变体"""

    def __init__(
        self,
        name: str,
        params: Dict[str, Any],
        metrics: Optional[Dict[str, float]] = None,
    ):
        self.name: str = name
        self.params: Dict[str, Any] = copy.deepcopy(params)
        self.metrics: Dict[str, float] = metrics or {}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "params": self.params,
            "metrics": self.metrics,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ABTestVariant":
        return cls(
            name=data.get("name", ""),
            params=data.get("params", {}),
            metrics=data.get("metrics", {}),
        )

    @property
    def weighted_score(self) -> float:
        return self.metrics.get("weighted_score", 0.0)


class ABTestResult:
    """A/B 测试完整结果"""

    def __init__(
        self,
        test_id: str,
        test_name: str,
        timestamp: str,
        variant_a: ABTestVariant,
        variant_b: ABTestVariant,
        comparison: Dict[str, Any],
        decision: Dict[str, Any],
        metric_weights: Dict[str, float],
        ground_truth_name: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.test_id = test_id
        self.test_name = test_name
        self.timestamp = timestamp
        self.variant_a = variant_a
        self.variant_b = variant_b
        self.comparison = comparison
        self.decision = decision
        self.metric_weights = metric_weights
        self.ground_truth_name = ground_truth_name
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "test_id": self.test_id,
            "test_name": self.test_name,
            "timestamp": self.timestamp,
            "variant_a": self.variant_a.to_dict(),
            "variant_b": self.variant_b.to_dict(),
            "comparison": self.comparison,
            "decision": self.decision,
            "metric_weights": self.metric_weights,
            "ground_truth_name": self.ground_truth_name,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ABTestResult":
        return cls(
            test_id=data.get("test_id", ""),
            test_name=data.get("test_name", ""),
            timestamp=data.get("timestamp", ""),
            variant_a=ABTestVariant.from_dict(data.get("variant_a", {})),
            variant_b=ABTestVariant.from_dict(data.get("variant_b", {})),
            comparison=data.get("comparison", {}),
            decision=data.get("decision", {}),
            metric_weights=data.get("metric_weights", {}),
            ground_truth_name=data.get("ground_truth_name", ""),
            metadata=data.get("metadata", {}),
        )


# ============================================================
# A/B 测试运行器
# ============================================================
class ABTestRunner:
    """
    A/B 测试框架

    用法:
        runner = ABTestRunner()
        result = runner.run_test(
            test_name="smurfing_threshold_test",
            transactions=transactions,
            ground_truth={tid: is_suspicious},
            variant_a_params=baseline_params,
            variant_b_params={"smurfing": {"min_count": 3}},
            metric_weights={"precision": 0.4, "recall": 0.4, "f1": 0.2},
        )
        print(result.decision["recommendation"])
    """

    def __init__(self, storage_dir: str = ""):
        self.storage_dir = storage_dir or AB_TESTS_DIR
        os.makedirs(self.storage_dir, exist_ok=True)
        # 复用 MultiObjectiveOptimizer 的评估逻辑（戒律 M1: 口径一致）
        from tools.multi_objective_optimizer import MultiObjectiveOptimizer
        self._evaluator = MultiObjectiveOptimizer()

    # ============================================================
    # 变体评估
    # ============================================================
    def _evaluate_variant(
        self,
        transactions: List[Any],
        ground_truth: Dict[str, Any],
        params: Dict[str, Any],
        metric_weights: Dict[str, float],
    ) -> Dict[str, float]:
        """
        评估单个变体的指标

        戒律:
        - M1: 基于真实数据和真值集
        - P1: 计算 recall（不遗漏）
        - P2: 计算 precision（不误报）

        Returns:
            {precision, recall, f1, fp_rate, fn_rate, tp, fp, tn, fn,
             total_hits, weighted_score}
        """
        metrics = self._evaluator._evaluate_params(
            transactions, ground_truth, params
        )
        # 计算加权得分
        weighted_score = 0.0
        for obj, weight in metric_weights.items():
            weighted_score += weight * metrics.get(obj, 0.0)
        metrics["weighted_score"] = round(weighted_score, 4)
        return metrics

    # ============================================================
    # 对比与决策
    # ============================================================
    def _compare_variants(
        self,
        variant_a: ABTestVariant,
        variant_b: ABTestVariant,
    ) -> Dict[str, Any]:
        """
        对比两个变体的指标差异

        Returns:
            {metric: {a, b, delta, relative_change}}
        """
        comparison: Dict[str, Any] = {}
        # 对比所有数值型指标
        metric_keys = set(variant_a.metrics.keys()) | set(variant_b.metrics.keys())
        for key in sorted(metric_keys):
            a_val = variant_a.metrics.get(key, 0)
            b_val = variant_b.metrics.get(key, 0)
            if not isinstance(a_val, (int, float)) or not isinstance(b_val, (int, float)):
                continue
            delta = b_val - a_val
            relative = (delta / a_val) if a_val != 0 else (1.0 if delta > 0 else (-1.0 if delta < 0 else 0.0))
            comparison[key] = {
                "a": a_val,
                "b": b_val,
                "delta": round(delta, 4),
                "relative_change": round(relative, 4),
            }
        return comparison

    def _decide_winner(
        self,
        variant_a: ABTestVariant,
        variant_b: ABTestVariant,
        comparison: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        基于指标和戒律守护给出决策

        戒律:
        - P1: B 高风险召回下降超过阈值 -> 拒绝推荐 B
        - P2: B 总命中激增超过阈值 -> 警告
        - M2: 决策附带理由

        Returns:
            {
                "recommendation": "B" | "A" | "review",
                "reason": str,
                "guardrail_violations": [str],
                "guardrail_warnings": [str],
            }
        """
        violations: List[str] = []
        warnings: List[str] = []

        # 戒律 P1: 高风险召回下降检查
        # 高风险召回 ~ recall（在真值集层面，recall 衡量不遗漏）
        recall_cmp = comparison.get("recall", {})
        recall_a = recall_cmp.get("a", 0)
        recall_b = recall_cmp.get("b", 0)
        if recall_a > 0:
            recall_drop = (recall_a - recall_b) / recall_a
            if recall_drop >= RISK_RECALL_DROP_REJECT_RATIO:
                violations.append(
                    f"戒律 P1 违反: recall 从 {recall_a:.4f} 降至 {recall_b:.4f}"
                    f"（下降 {recall_drop*100:.1f}%），可能遗漏高风险交易"
                )

        # 戒律 P2: 总命中激增检查
        hits_cmp = comparison.get("total_hits", {})
        hits_a = hits_cmp.get("a", 0)
        hits_b = hits_cmp.get("b", 0)
        if hits_a > 0:
            surge = hits_b / hits_a
            if surge >= HITS_SURGE_WARNING_RATIO:
                warnings.append(
                    f"戒律 P2 警告: 总命中数从 {int(hits_a)} 升至 {int(hits_b)}"
                    f"（激增 {(surge-1)*100:.0f}%），可能产生大量误报"
                )

        # 综合决策
        score_a = variant_a.weighted_score
        score_b = variant_b.weighted_score

        if violations:
            recommendation = "A"
            reason = (
                f"候选 B 存在 {len(violations)} 项戒律违反，保留基线 A。"
                f"（加权得分 A={score_a:.4f} vs B={score_b:.4f}）"
            )
        elif score_b > score_a:
            recommendation = "B"
            reason = (
                f"候选 B 加权得分 {score_b:.4f} 高于基线 A {score_a:.4f}，"
                f"且无戒律违反。"
            )
            if warnings:
                reason += f" 但有 {len(warnings)} 项警告，请关注。"
        elif score_b == score_a:
            recommendation = "review"
            reason = (
                f"两变体加权得分相同（{score_a:.4f}），需人工判断。"
            )
        else:
            recommendation = "A"
            reason = (
                f"候选 B 加权得分 {score_b:.4f} 未超过基线 A {score_a:.4f}，保留 A。"
            )

        return {
            "recommendation": recommendation,
            "reason": reason,
            "guardrail_violations": violations,
            "guardrail_warnings": warnings,
            "weighted_score_a": round(score_a, 4),
            "weighted_score_b": round(score_b, 4),
        }

    # ============================================================
    # 主入口
    # ============================================================
    def run_test(
        self,
        test_name: str,
        transactions: List[Any],
        ground_truth: Dict[str, Any],
        variant_a_params: Dict[str, Any],
        variant_b_params: Dict[str, Any],
        metric_weights: Optional[Dict[str, float]] = None,
        variant_a_name: str = "baseline",
        variant_b_name: str = "candidate",
        ground_truth_name: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ABTestResult:
        """
        运行一次 A/B 测试

        戒律:
        - M1: 两变体使用同一 transactions 和 ground_truth
        - M2: 决策附带理由
        - M4: 结果持久化
        - P4: 不修改全局配置

        Args:
            test_name: 测试名称
            transactions: 交易列表
            ground_truth: 真值 {transaction_id: is_suspicious}
            variant_a_params: 基线参数
            variant_b_params: 候选参数
            metric_weights: 指标权重
            variant_a_name: 基线变体名
            variant_b_name: 候选变体名
            ground_truth_name: 真值集名称（用于追溯）
            metadata: 附加元数据

        Returns:
            ABTestResult
        """
        if metric_weights is None:
            metric_weights = DEFAULT_METRIC_WEIGHTS

        # 戒律 M1: 同一数据评估两个变体
        metrics_a = self._evaluate_variant(
            transactions, ground_truth, variant_a_params, metric_weights
        )
        metrics_b = self._evaluate_variant(
            transactions, ground_truth, variant_b_params, metric_weights
        )

        variant_a = ABTestVariant(variant_a_name, variant_a_params, metrics_a)
        variant_b = ABTestVariant(variant_b_name, variant_b_params, metrics_b)

        comparison = self._compare_variants(variant_a, variant_b)
        decision = self._decide_winner(variant_a, variant_b, comparison)

        test_id = f"ABT-{uuid.uuid4().hex[:8].upper()}"
        result = ABTestResult(
            test_id=test_id,
            test_name=test_name,
            timestamp=_now_str(),
            variant_a=variant_a,
            variant_b=variant_b,
            comparison=comparison,
            decision=decision,
            metric_weights=metric_weights,
            ground_truth_name=ground_truth_name,
            metadata=metadata or {},
        )

        # 戒律 M4: 持久化
        self._save_result(result)
        return result

    def _save_result(self, result: ABTestResult) -> None:
        """保存测试结果（戒律 M4: 原子写入）"""
        path = os.path.join(self.storage_dir, f"{result.test_id}.json")
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except OSError as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise RuntimeError(f"A/B 测试结果保存失败: {e}") from e

    # ============================================================
    # 查询
    # ============================================================
    def list_tests(self) -> List[Dict[str, Any]]:
        """列出所有测试结果摘要（按时间倒序）"""
        results: List[Dict[str, Any]] = []
        if not os.path.exists(self.storage_dir):
            return results
        for fname in os.listdir(self.storage_dir):
            if not fname.startswith("ABT-") or not fname.endswith(".json"):
                continue
            path = os.path.join(self.storage_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results.append({
                    "test_id": data.get("test_id", ""),
                    "test_name": data.get("test_name", ""),
                    "timestamp": data.get("timestamp", ""),
                    "recommendation": data.get("decision", {}).get("recommendation", ""),
                    "weighted_score_a": data.get("decision", {}).get("weighted_score_a", 0),
                    "weighted_score_b": data.get("decision", {}).get("weighted_score_b", 0),
                })
            except (json.JSONDecodeError, OSError):
                continue
        results.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return results

    def get_test(self, test_id: str) -> Optional[ABTestResult]:
        """获取指定测试结果"""
        path = os.path.join(self.storage_dir, f"{test_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        try:
            return ABTestResult.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return None

    def delete_test(self, test_id: str) -> bool:
        """删除测试结果"""
        path = os.path.join(self.storage_dir, f"{test_id}.json")
        if os.path.exists(path):
            try:
                os.remove(path)
                return True
            except OSError:
                return False
        return False
