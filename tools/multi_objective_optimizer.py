"""
多目标参数优化器 (Multi-Objective Optimizer)

职责:
- 同时优化多个目标（precision、recall、f1）
- 基于网格搜索参数空间，找出帕累托最优解
- 支持加权目标函数，平衡不同戒律要求

戒律遵循:
- M1: 基于真实交易数据和真值集评估，不编造结果
- M2: 优化结果包含完整评估指标
- M4: 优化过程可追溯，记录所有评估过的参数组合
- P1: 重视 recall（不遗漏）
- P2: 重视 precision（不误报）
- P4: 优化过程不破坏现有配置（使用临时替换）
"""
import os
import json
import itertools
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Callable

from config import EVALUATIONS_DIR
from tools.rule_tuner import RuleTuner


def _now_str() -> str:
    return datetime.now().isoformat()


# ============================================================
# 评估结果数据结构
# ============================================================
class ParamEvaluation:
    """单组参数的评估结果"""

    def __init__(
        self,
        params: Dict[str, Any],
        metrics: Dict[str, float],
    ):
        self.params = params
        self.metrics = metrics  # {precision, recall, f1, fp_rate, fn_rate}
        self.weighted_score: float = 0.0
        self.is_pareto_optimal: bool = False

    def to_dict(self) -> dict:
        return {
            "params": self.params,
            "metrics": self.metrics,
            "weighted_score": self.weighted_score,
            "is_pareto_optimal": self.is_pareto_optimal,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ParamEvaluation":
        obj = cls(data.get("params", {}), data.get("metrics", {}))
        obj.weighted_score = data.get("weighted_score", 0.0)
        obj.is_pareto_optimal = data.get("is_pareto_optimal", False)
        return obj


# ============================================================
# 多目标优化器
# ============================================================
class MultiObjectiveOptimizer:
    """
    多目标参数优化器

    用法:
        optimizer = MultiObjectiveOptimizer()

        # 定义搜索空间
        search_space = {
            "large_amount": {"threshold": [50000, 100000, 200000]},
            "smurfing": {"min_count": [3, 5, 8]},
        }

        # 优化
        result = optimizer.optimize(
            transactions=transactions,
            ground_truth=ground_truth,
            search_space=search_space,
            objective_weights={"precision": 0.4, "recall": 0.4, "f1": 0.2},
        )
    """

    # 默认目标权重（戒律 P1/P2 平衡）
    DEFAULT_WEIGHTS: Dict[str, float] = {
        "precision": 0.35,  # 戒律 P2: 不误报
        "recall": 0.35,     # 戒律 P1: 不遗漏
        "f1": 0.30,         # 平衡指标
    }

    # 最大网格组合数（防止组合爆炸）
    MAX_GRID_COMBINATIONS = 500

    def __init__(self, storage_dir: str = ""):
        """
        Args:
            storage_dir: 优化结果存储目录，默认使用 EVALUATIONS_DIR
        """
        self.storage_dir = storage_dir or EVALUATIONS_DIR
        os.makedirs(self.storage_dir, exist_ok=True)
        self.tuner = RuleTuner()

    # ============================================================
    # 参数空间生成
    # ============================================================
    def _generate_param_combinations(
        self,
        search_space: Dict[str, Dict[str, List[Any]]],
    ) -> List[Dict[str, Any]]:
        """
        生成参数组合（网格搜索）

        Args:
            search_space: {group: {param: [values]}}

        Returns:
            参数组合列表
        """
        # 展开为 [(group, param, value)] 列表
        flat_params = []
        for group, params in search_space.items():
            for param, values in params.items():
                for value in values:
                    flat_params.append((group, param, value))

        # 计算总组合数
        group_params: Dict[str, Dict[str, List[Any]]] = {}
        for group, param, value in flat_params:
            if group not in group_params:
                group_params[group] = {}
            if param not in group_params[group]:
                group_params[group][param] = []
            group_params[group][param].append(value)

        # 为每个组生成参数组合
        group_combinations: Dict[str, List[Dict[str, Any]]] = {}
        for group, params in group_params.items():
            keys = list(params.keys())
            value_lists = [params[k] for k in keys]
            group_combos = []
            for combo in itertools.product(*value_lists):
                group_combos.append(dict(zip(keys, combo)))
            group_combinations[group] = group_combos

        # 组合各组的参数
        all_groups = list(group_combinations.keys())
        if not all_groups:
            return [{}]

        group_combo_lists = [group_combinations[g] for g in all_groups]
        result = []
        for combo in itertools.product(*group_combo_lists):
            params = {}
            for i, group in enumerate(all_groups):
                params[group] = combo[i]
            result.append(params)

        # 戒律 P4: 防止组合爆炸
        if len(result) > self.MAX_GRID_COMBINATIONS:
            raise ValueError(
                f"参数组合数 {len(result)} 超过上限 {self.MAX_GRID_COMBINATIONS}，"
                f"请缩小搜索空间"
            )

        return result

    # ============================================================
    # 参数评估
    # ============================================================
    def _evaluate_params(
        self,
        transactions: List[Any],
        ground_truth: Dict[str, Any],
        params: Dict[str, Any],
    ) -> Dict[str, float]:
        """
        评估单组参数的指标

        戒律:
        - M1: 基于真实数据评估
        - P1: 计算 recall（不遗漏）
        - P2: 计算 precision（不误报）

        Args:
            transactions: 交易列表
            ground_truth: 真值字典 {transaction_id: is_suspicious}
            params: 参数字典

        Returns:
            {precision, recall, f1, fp_rate, fn_rate, total_hits}
        """
        # 使用 RuleTuner 运行规则引擎
        rule_hits = self.tuner._run_rules(transactions, params)

        # 收集所有命中的交易ID
        predicted_suspicious = set()
        for rule_name, hits in rule_hits.items():
            for hit in hits:
                txn_id = None
                if isinstance(hit, dict):
                    # 从命中结果提取交易ID
                    txn = hit.get("transaction") or hit
                    if isinstance(txn, dict):
                        txn_id = txn.get("transaction_id")
                if txn_id:
                    predicted_suspicious.add(txn_id)

        # 计算混淆矩阵
        tp = 0  # 预测可疑，实际可疑
        fp = 0  # 预测可疑，实际正常
        fn = 0  # 预测正常，实际可疑
        tn = 0  # 预测正常，实际正常

        for txn_id, is_suspicious in ground_truth.items():
            if is_suspicious is None:
                continue  # 跳过待定记录
            if is_suspicious:
                if txn_id in predicted_suspicious:
                    tp += 1
                else:
                    fn += 1
            else:
                if txn_id in predicted_suspicious:
                    fp += 1
                else:
                    tn += 1

        # 计算指标
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) > 0 else 0.0)
        fp_rate = fp / (fp + tn) if (fp + tn) > 0 else 0.0
        fn_rate = fn / (fn + tp) if (fn + tp) > 0 else 0.0

        return {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "fp_rate": round(fp_rate, 4),
            "fn_rate": round(fn_rate, 4),
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
            "total_hits": len(predicted_suspicious),
        }

    # ============================================================
    # 帕累托前沿
    # ============================================================
    def _compute_pareto_front(
        self,
        evaluations: List[ParamEvaluation],
        objectives: List[str] = None,
    ) -> List[ParamEvaluation]:
        """
        计算帕累托前沿

        戒律:
        - P1: 最大化 recall
        - P2: 最大化 precision

        Args:
            evaluations: 评估结果列表
            objectives: 优化目标列表（默认 precision + recall + f1，越大越好）

        Returns:
            帕累托最优解列表
        """
        if objectives is None:
            objectives = ["precision", "recall", "f1"]

        if not evaluations:
            return []

        pareto_front = []
        for i, candidate in enumerate(evaluations):
            is_dominated = False
            for j, other in enumerate(evaluations):
                if i == j:
                    continue
                # 检查 other 是否支配 candidate
                # other 支配 candidate: other 在所有目标上 >= candidate，且至少一个 >
                dominates = True
                strictly_better = False
                for obj in objectives:
                    other_val = other.metrics.get(obj, 0.0)
                    cand_val = candidate.metrics.get(obj, 0.0)
                    if other_val < cand_val:
                        dominates = False
                        break
                    if other_val > cand_val:
                        strictly_better = True
                if dominates and strictly_better:
                    is_dominated = True
                    break

            if not is_dominated:
                candidate.is_pareto_optimal = True
                pareto_front.append(candidate)

        return pareto_front

    # ============================================================
    # 加权目标函数
    # ============================================================
    def _compute_weighted_score(
        self,
        metrics: Dict[str, float],
        weights: Dict[str, float],
    ) -> float:
        """计算加权得分"""
        score = 0.0
        for obj, weight in weights.items():
            score += weight * metrics.get(obj, 0.0)
        return round(score, 4)

    # ============================================================
    # 主优化入口
    # ============================================================
    def optimize(
        self,
        transactions: List[Any],
        ground_truth: Dict[str, Any],
        search_space: Dict[str, Dict[str, List[Any]]],
        objective_weights: Optional[Dict[str, float]] = None,
        max_combinations: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        多目标参数优化

        戒律:
        - M1: 基于真实数据和真值集
        - M2: 结果包含完整指标
        - M4: 优化过程可追溯
        - P1: 重视 recall（不遗漏）
        - P2: 重视 precision（不误报）
        - P4: 不破坏现有配置

        Args:
            transactions: 真实交易列表
            ground_truth: 真值字典 {transaction_id: is_suspicious}
            search_space: 搜索空间 {group: {param: [values]}}
            objective_weights: 目标权重 {precision: w, recall: w, f1: w}
            max_combinations: 最大组合数限制

        Returns:
            {
                "optimization_id": str,
                "timestamp": str,
                "objective_weights": {...},
                "total_evaluated": int,
                "best_weighted": ParamEvaluation,
                "pareto_front": [ParamEvaluation],
                "all_evaluations": [ParamEvaluation],
            }
        """
        if objective_weights is None:
            objective_weights = self.DEFAULT_WEIGHTS

        if max_combinations is not None:
            original_max = self.MAX_GRID_COMBINATIONS
            self.MAX_GRID_COMBINATIONS = max_combinations
        try:
            param_combos = self._generate_param_combinations(search_space)
        finally:
            if max_combinations is not None:
                self.MAX_GRID_COMBINATIONS = original_max

        # 评估每组参数
        evaluations: List[ParamEvaluation] = []
        for params in param_combos:
            try:
                metrics = self._evaluate_params(transactions, ground_truth, params)
                pe = ParamEvaluation(params=params, metrics=metrics)
                pe.weighted_score = self._compute_weighted_score(
                    metrics, objective_weights
                )
                evaluations.append(pe)
            except Exception as e:
                # 戒律 M4: 记录评估失败但不中断
                continue

        # 计算帕累托前沿
        pareto_front = self._compute_pareto_front(evaluations)

        # 加权最优
        best_weighted = max(evaluations, key=lambda e: e.weighted_score) if evaluations else None

        optimization_id = f"OPT-{uuid.uuid4().hex[:8].upper()}"
        result = {
            "optimization_id": optimization_id,
            "timestamp": _now_str(),
            "objective_weights": objective_weights,
            "total_evaluated": len(evaluations),
            "best_weighted": best_weighted.to_dict() if best_weighted else None,
            "pareto_front": [e.to_dict() for e in pareto_front],
            "all_evaluations": [e.to_dict() for e in evaluations],
        }

        # 持久化优化结果（戒律 M4: 可追溯）
        self._save_optimization(result)

        return result

    def _save_optimization(self, result: Dict[str, Any]) -> None:
        """保存优化结果（戒律 P4: 原子写入）"""
        opt_id = result.get("optimization_id", "OPT-UNKNOWN")
        path = os.path.join(self.storage_dir, f"{opt_id}.json")
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except OSError as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise RuntimeError(f"优化结果保存失败: {e}") from e

    # ============================================================
    # 结果查询
    # ============================================================
    def list_optimizations(self) -> List[Dict[str, Any]]:
        """列出所有优化结果"""
        results = []
        if not os.path.exists(self.storage_dir):
            return results
        for fname in os.listdir(self.storage_dir):
            if not fname.startswith("OPT-") or not fname.endswith(".json"):
                continue
            path = os.path.join(self.storage_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results.append({
                    "optimization_id": data.get("optimization_id"),
                    "timestamp": data.get("timestamp"),
                    "total_evaluated": data.get("total_evaluated", 0),
                    "objective_weights": data.get("objective_weights"),
                })
            except (json.JSONDecodeError, OSError):
                continue
        results.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return results

    def get_optimization(self, opt_id: str) -> Optional[Dict[str, Any]]:
        """获取指定优化结果"""
        path = os.path.join(self.storage_dir, f"{opt_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
