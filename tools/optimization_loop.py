"""
五阶段自动优化闭环 (Optimization Loop)

职责:
- 串联数据→评估→反馈→调参→验证五阶段，形成完整的参数优化闭环
- 基于真实交易数据、真值集和分析师反馈，自动推荐参数优化方案
- 非破坏性: 只推荐候选参数，不自动应用（需人工确认）

戒律遵循:
- M1: 基于真实交易数据、真值集和反馈，不编造
- M2: 推荐结果附带完整理由和指标依据
- M4: 闭环执行完整记录，可追溯
- P1: 漏报反馈多时提高 recall 权重；候选参数 recall 大幅下降时拒绝
- P2: 误报反馈多时提高 precision 权重；总命中激增时警告
- P4: 不破坏现有配置，只推荐候选参数

五阶段:
1. 数据收集: 加载交易数据 + 真值集 + 当前参数
2. 评估当前参数: 计算当前参数的 precision/recall/f1
3. 反馈收集与权重调整: 根据反馈类型和时间衰减调整目标权重
4. 调参: 多目标优化 + 交叉影响分析
5. 验证: A/B 测试 + 不变量检查 + 综合决策
"""
import os
import json
import copy
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional

from config import OPTIMIZATION_LOOP_DIR


def _now_str() -> str:
    return datetime.now().isoformat()


# ============================================================
# 默认配置
# ============================================================

# 默认目标权重（戒律 P1/P2 平衡）
DEFAULT_OBJECTIVE_WEIGHTS: Dict[str, float] = {
    "precision": 0.35,  # 戒律 P2: 不误报
    "recall": 0.35,     # 戒律 P1: 不遗漏
    "f1": 0.30,         # 平衡指标
}

# 权重调整参数
MAX_WEIGHT_SHIFT = 0.15       # 单次最大偏移 15%
MIN_WEIGHT = 0.15             # 单个权重下限
MAX_WEIGHT = 0.55             # 单个权重上限
WEIGHT_NORMALIZE_FACTOR = 10.0  # 10 条加权反馈达到最大偏移

# 默认搜索空间（包含当前默认值，确保优化器能发现"当前即最优"）
DEFAULT_SEARCH_SPACE: Dict[str, Dict[str, List[Any]]] = {
    "smurfing": {
        "min_count": [3, 5, 8],
        "amount_low": [30000, 40000],
    },
    "fast_in_fast_out": {
        "min_ratio": [0.90, 0.95, 0.98],
    },
    "large_amount": {
        "threshold": [50000, 100000, 200000],
    },
}


# ============================================================
# 结果数据结构
# ============================================================
class OptimizationLoopResult:
    """五阶段闭环执行结果"""

    def __init__(
        self,
        loop_id: str,
        timestamp: str,
        stages: Dict[str, Any],
        recommendation: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.loop_id = loop_id
        self.timestamp = timestamp
        self.stages = stages
        self.recommendation = recommendation
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "loop_id": self.loop_id,
            "timestamp": self.timestamp,
            "stages": self.stages,
            "recommendation": self.recommendation,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OptimizationLoopResult":
        return cls(
            loop_id=data.get("loop_id", ""),
            timestamp=data.get("timestamp", ""),
            stages=data.get("stages", {}),
            recommendation=data.get("recommendation", {}),
            metadata=data.get("metadata", {}),
        )


# ============================================================
# 五阶段自动优化闭环
# ============================================================
class OptimizationLoop:
    """
    五阶段自动优化闭环

    用法:
        loop = OptimizationLoop()
        result = loop.run_loop(
            transactions=transactions,
            ground_truth={tid: is_suspicious},
            current_params=tuner.get_tunable_params(),
        )
        print(result.recommendation["action"])  # "apply" / "keep" / "review"
    """

    def __init__(
        self,
        storage_dir: str = "",
        feedback_manager=None,
        ground_truth_versioner=None,
    ):
        """
        Args:
            storage_dir: 结果存储目录，默认 OPTIMIZATION_LOOP_DIR
            feedback_manager: 反馈管理器（可选，延迟创建避免循环依赖）
            ground_truth_versioner: 真值集版本管理器（可选）
        """
        self.storage_dir = storage_dir or OPTIMIZATION_LOOP_DIR
        os.makedirs(self.storage_dir, exist_ok=True)
        self._feedback_manager = feedback_manager
        self._ground_truth_versioner = ground_truth_versioner
        self._index_path = os.path.join(self.storage_dir, "index.json")

    # ============================================================
    # 延迟初始化依赖
    # ============================================================
    @property
    def feedback_manager(self):
        if self._feedback_manager is None:
            from tools.feedback_manager import FeedbackManager
            self._feedback_manager = FeedbackManager()
        return self._feedback_manager

    @property
    def ground_truth_versioner(self):
        if self._ground_truth_versioner is None:
            from tools.ground_truth_versioner import GroundTruthVersioner
            self._ground_truth_versioner = GroundTruthVersioner()
        return self._ground_truth_versioner

    # ============================================================
    # 主入口
    # ============================================================
    def run_loop(
        self,
        transactions: List[Any],
        ground_truth: Dict[str, Any],
        current_params: Optional[Dict[str, Any]] = None,
        search_space: Optional[Dict[str, Dict[str, List[Any]]]] = None,
        dataset_name: str = "",
        objective_weights: Optional[Dict[str, float]] = None,
    ) -> OptimizationLoopResult:
        """
        运行完整的五阶段优化闭环

        戒律:
        - M1: 基于真实数据和真值集
        - M2: 推荐结果附带理由
        - M4: 全过程记录
        - P4: 非破坏性，只推荐不应用

        Args:
            transactions: 真实交易列表
            ground_truth: 真值字典 {transaction_id: is_suspicious(Bool|None)}
            current_params: 当前参数（None 时使用 RuleTuner 默认参数）
            search_space: 搜索空间（None 时使用默认搜索空间）
            dataset_name: 真值集名称（用于追溯）
            objective_weights: 初始目标权重（None 时使用默认权重）

        Returns:
            OptimizationLoopResult
        """
        loop_id = f"LOOP-{uuid.uuid4().hex[:8].upper()}"
        timestamp = _now_str()

        # ===== Stage 1: 数据收集 =====
        stage1 = self._stage1_collect_data(
            transactions, ground_truth, current_params, dataset_name
        )

        # ===== Stage 2: 评估当前参数 =====
        stage2 = self._stage2_evaluate_current(
            stage1["transactions"],
            stage1["ground_truth"],
            stage1["current_params"],
        )

        # ===== Stage 3: 反馈收集与权重调整 =====
        stage3 = self._stage3_adjust_weights(objective_weights)

        # ===== Stage 4: 调参 =====
        stage4 = self._stage4_tune_params(
            stage1["transactions"],
            stage1["ground_truth"],
            stage1["current_params"],
            search_space,
            stage3["adjusted_weights"],
        )

        # ===== Stage 5: 验证 =====
        stage5 = self._stage5_validate(
            stage1["transactions"],
            stage1["ground_truth"],
            stage1["current_params"],
            stage4,
            stage3["adjusted_weights"],
        )

        # ===== 综合推荐 =====
        recommendation = self._build_recommendation(stage2, stage4, stage5)

        result = OptimizationLoopResult(
            loop_id=loop_id,
            timestamp=timestamp,
            stages={
                "stage1_data_collection": stage1,
                "stage2_current_evaluation": stage2,
                "stage3_feedback_weights": stage3,
                "stage4_parameter_tuning": stage4,
                "stage5_validation": stage5,
            },
            recommendation=recommendation,
            metadata={
                "dataset_name": dataset_name,
                "transaction_count": len(stage1["transactions"]),
                "ground_truth_count": len(stage1["ground_truth"]),
            },
        )

        # 戒律 M4: 持久化
        self._save_result(result)
        return result

    # ============================================================
    # Stage 1: 数据收集
    # ============================================================
    def _stage1_collect_data(
        self,
        transactions: List[Any],
        ground_truth: Dict[str, Any],
        current_params: Optional[Dict[str, Any]],
        dataset_name: str,
    ) -> Dict[str, Any]:
        """
        Stage 1: 数据收集

        戒律:
        - M1: 真实数据收集，不编造
        - M4: 记录数据来源和规模

        Returns:
            {
                "transactions": List,
                "ground_truth": Dict,
                "current_params": Dict,
                "data_warnings": List[str],
                "data_summary": {...},
            }
        """
        from tools.rule_tuner import RuleTuner

        warnings: List[str] = []

        # 真值集加载：如果 ground_truth 为空且 dataset_name 提供，尝试从版本器加载
        gt = dict(ground_truth) if ground_truth else {}
        if not gt and dataset_name:
            try:
                ds = self.ground_truth_versioner.get_latest_version(dataset_name)
                if ds is not None:
                    gt = {
                        tid: rec.is_suspicious
                        for tid, rec in ds.records.items()
                    }
            except Exception as e:
                warnings.append(f"真值集加载失败: {e}")

        # 数据完整性检查
        if not transactions:
            warnings.append("交易数据为空，评估指标将为 0")
        if not gt:
            warnings.append("真值集为空，无法计算 precision/recall")
        else:
            # 检查真值集中 None（待定）记录比例
            none_count = sum(1 for v in gt.values() if v is None)
            if none_count > 0:
                none_ratio = none_count / len(gt)
                warnings.append(
                    f"真值集中有 {none_count} 条待定记录（{none_ratio*100:.1f}%），"
                    f"将不计入指标计算"
                )

        # 当前参数：未提供时使用 RuleTuner 默认参数
        if current_params is None:
            tuner = RuleTuner()
            params = tuner.get_defaults()
        else:
            params = copy.deepcopy(current_params)

        return {
            "transactions": list(transactions) if transactions else [],
            "ground_truth": gt,
            "current_params": params,
            "data_warnings": warnings,
            "data_summary": {
                "transaction_count": len(transactions) if transactions else 0,
                "ground_truth_count": len(gt),
                "ground_truth_suspicious": sum(1 for v in gt.values() if v is True),
                "ground_truth_normal": sum(1 for v in gt.values() if v is False),
                "ground_truth_pending": sum(1 for v in gt.values() if v is None),
                "dataset_name": dataset_name,
            },
        }

    # ============================================================
    # Stage 2: 评估当前参数
    # ============================================================
    def _stage2_evaluate_current(
        self,
        transactions: List[Any],
        ground_truth: Dict[str, Any],
        current_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Stage 2: 评估当前参数的指标

        戒律:
        - M1: 基于真实数据和真值集评估
        - M2: 完整记录评估指标

        Returns:
            {
                "current_metrics": {...},
                "evaluation_warnings": List[str],
            }
        """
        from tools.multi_objective_optimizer import MultiObjectiveOptimizer

        warnings: List[str] = []
        optimizer = MultiObjectiveOptimizer()

        try:
            metrics = optimizer._evaluate_params(
                transactions, ground_truth, current_params
            )
        except Exception as e:
            warnings.append(f"当前参数评估失败: {e}")
            metrics = {
                "precision": 0.0, "recall": 0.0, "f1": 0.0,
                "fp_rate": 0.0, "fn_rate": 0.0,
                "tp": 0, "fp": 0, "tn": 0, "fn": 0,
                "total_hits": 0,
            }

        return {
            "current_metrics": metrics,
            "evaluation_warnings": warnings,
        }

    # ============================================================
    # Stage 3: 反馈收集与权重调整
    # ============================================================
    def _stage3_adjust_weights(
        self,
        initial_weights: Optional[Dict[str, float]],
    ) -> Dict[str, Any]:
        """
        Stage 3: 根据反馈类型和时间衰减调整目标权重

        戒律:
        - P1: 漏报反馈多 → 提高 recall 权重（不遗漏）
        - P2: 误报反馈多 → 提高 precision 权重（不误报）
        - M1: 基于真实反馈数据
        - M4: 权重调整过程可追溯

        算法:
        1. 统计加权误报计数(W_FP)和加权漏报计数(W_FN)
        2. 偏移: shift = clip((W_FN - W_FP) / FACTOR, -1, 1) * MAX_SHIFT
           - W_FN > W_FP → shift > 0 → recall 权重提高
           - W_FP > W_FN → shift < 0 → precision 权重提高
        3. 限制每个权重在 [MIN_WEIGHT, MAX_WEIGHT]
        4. 归一化确保 sum = 1.0

        Returns:
            {
                "adjusted_weights": {precision, recall, f1},
                "base_weights": {...},
                "weight_shift": float,
                "shift_direction": str,
                "feedback_stats": {...},
                "adjustment_warnings": List[str],
            }
        """
        base = dict(initial_weights) if initial_weights else dict(DEFAULT_OBJECTIVE_WEIGHTS)
        warnings: List[str] = []

        # 收集反馈
        try:
            all_feedback = self.feedback_manager.list_feedback(limit=10000)
        except Exception as e:
            warnings.append(f"反馈加载失败，使用默认权重: {e}")
            all_feedback = []

        w_fp = 0.0  # 加权误报计数
        w_fn = 0.0  # 加权漏报计数
        w_conf = 0.0  # 加权确认计数
        raw_fp = 0
        raw_fn = 0
        raw_conf = 0

        for entry in all_feedback:
            fb_id = entry.get("feedback_id", "")
            if not fb_id:
                continue
            try:
                full = self.feedback_manager.get_feedback(fb_id)
            except Exception:
                continue
            if not full:
                continue

            weight = self.feedback_manager.get_feedback_weight(full)
            fb_type = full.get("feedback_type", "")

            if fb_type == "false_positive":
                w_fp += weight
                raw_fp += 1
            elif fb_type == "false_negative":
                w_fn += weight
                raw_fn += 1
            elif fb_type == "confirmed":
                w_conf += weight
                raw_conf += 1

        # 计算权重偏移
        # 正值: 漏报多，需提高 recall（戒律 P1）
        # 负值: 误报多，需提高 precision（戒律 P2）
        diff = w_fn - w_fp
        normalized_diff = max(-1.0, min(1.0, diff / WEIGHT_NORMALIZE_FACTOR))
        shift = normalized_diff * MAX_WEIGHT_SHIFT

        new_recall = base.get("recall", 0.35) + shift
        new_precision = base.get("precision", 0.35) - shift
        new_f1 = base.get("f1", 0.30)

        # 限制范围
        new_recall = max(MIN_WEIGHT, min(MAX_WEIGHT, new_recall))
        new_precision = max(MIN_WEIGHT, min(MAX_WEIGHT, new_precision))
        new_f1 = max(MIN_WEIGHT, min(MAX_WEIGHT, new_f1))

        # 归一化（确保 sum = 1.0）
        total = new_recall + new_precision + new_f1
        if total > 0:
            new_recall /= total
            new_precision /= total
            new_f1 /= total

        adjusted = {
            "precision": round(new_precision, 4),
            "recall": round(new_recall, 4),
            "f1": round(new_f1, 4),
        }

        # 偏移方向描述
        if shift > 0.001:
            direction = "recall_up"
            reason = f"漏报反馈(W_FN={w_fn:.2f}) > 误报反馈(W_FP={w_fp:.2f})，提高 recall 权重（戒律 P1: 不遗漏）"
        elif shift < -0.001:
            direction = "precision_up"
            reason = f"误报反馈(W_FP={w_fp:.2f}) > 漏报反馈(W_FN={w_fn:.2f})，提高 precision 权重（戒律 P2: 不误报）"
        else:
            direction = "none"
            reason = "漏报与误报反馈平衡（或无反馈），保持默认权重"

        return {
            "adjusted_weights": adjusted,
            "base_weights": base,
            "weight_shift": round(shift, 4),
            "shift_direction": direction,
            "shift_reason": reason,
            "feedback_stats": {
                "weighted_false_positive": round(w_fp, 4),
                "weighted_false_negative": round(w_fn, 4),
                "weighted_confirmed": round(w_conf, 4),
                "raw_count": {
                    "false_positive": raw_fp,
                    "false_negative": raw_fn,
                    "confirmed": raw_conf,
                    "total": raw_fp + raw_fn + raw_conf,
                },
            },
            "adjustment_warnings": warnings,
        }

    # ============================================================
    # Stage 4: 调参
    # ============================================================
    def _stage4_tune_params(
        self,
        transactions: List[Any],
        ground_truth: Dict[str, Any],
        current_params: Dict[str, Any],
        search_space: Optional[Dict[str, Dict[str, List[Any]]]],
        objective_weights: Dict[str, float],
    ) -> Dict[str, Any]:
        """
        Stage 4: 多目标优化 + 交叉影响分析

        戒律:
        - M1: 基于真实数据和真值集优化
        - M2: 优化结果包含完整指标
        - M4: 优化过程可追溯
        - P4: 不修改全局配置

        Returns:
            {
                "optimization_result": {...} | None,
                "cross_impact_result": {...} | None,
                "best_candidate_params": Dict | None,
                "same_as_current": bool,
                "tuning_warnings": List[str],
            }
        """
        from tools.multi_objective_optimizer import MultiObjectiveOptimizer
        from tools.cross_impact_analyzer import (
            CrossImpactAnalyzer, ParamChange,
        )

        warnings: List[str] = []
        ss = search_space if search_space is not None else dict(DEFAULT_SEARCH_SPACE)

        # 如果交易或真值集为空，跳过优化
        if not transactions or not ground_truth:
            warnings.append("交易数据或真值集为空，跳过参数优化")
            return {
                "optimization_result": None,
                "cross_impact_result": None,
                "best_candidate_params": None,
                "same_as_current": True,
                "tuning_warnings": warnings,
            }

        # 多目标优化
        optimizer = MultiObjectiveOptimizer()
        optimization_result = None
        try:
            optimization_result = optimizer.optimize(
                transactions=transactions,
                ground_truth=ground_truth,
                search_space=ss,
                objective_weights=objective_weights,
            )
        except ValueError as e:
            # 组合爆炸等参数错误
            warnings.append(f"多目标优化失败: {e}")
        except Exception as e:
            warnings.append(f"多目标优化异常: {e}")

        # 提取最佳候选参数
        best_candidate_params = None
        same_as_current = True
        if optimization_result and optimization_result.get("best_weighted"):
            best_partial = optimization_result["best_weighted"].get("params", {})
            # 合并: 当前参数 + 优化结果覆盖（确保候选参数完整）
            best_candidate_params = copy.deepcopy(current_params)
            for group, group_params in best_partial.items():
                if group not in best_candidate_params:
                    best_candidate_params[group] = {}
                if isinstance(group_params, dict):
                    best_candidate_params[group].update(group_params)
            # 检查是否与当前参数相同
            same_as_current = self._params_equal(best_candidate_params, current_params)

        if same_as_current:
            warnings.append("优化结果与当前参数相同，无需变更")

        # 交叉影响分析（仅当候选参数与当前不同时）
        cross_impact_result = None
        if best_candidate_params and not same_as_current:
            param_changes = self._build_param_changes(
                current_params, best_candidate_params
            )
            if param_changes:
                analyzer = CrossImpactAnalyzer()
                try:
                    ci_result = analyzer.analyze(
                        transactions=transactions,
                        baseline_params=current_params,
                        param_changes=param_changes,
                        metadata={"loop_stage": "tuning"},
                    )
                    cross_impact_result = ci_result.to_dict()
                    # 检查是否有强交叉影响
                    strong_count = len(ci_result.strong_impacts)
                    if strong_count > 0:
                        warnings.append(
                            f"检测到 {strong_count} 项强交叉影响，"
                            f"请关注参数变更对其他规则的副作用"
                        )
                except Exception as e:
                    warnings.append(f"交叉影响分析失败: {e}")

        return {
            "optimization_result": (
                self._summarize_optimization(optimization_result)
                if optimization_result else None
            ),
            "cross_impact_result": cross_impact_result,
            "best_candidate_params": best_candidate_params,
            "same_as_current": same_as_current,
            "tuning_warnings": warnings,
        }

    def _summarize_optimization(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """摘要保存优化结果（避免 all_evaluations 过大）"""
        if not result:
            return None
        return {
            "optimization_id": result.get("optimization_id"),
            "timestamp": result.get("timestamp"),
            "objective_weights": result.get("objective_weights"),
            "total_evaluated": result.get("total_evaluated", 0),
            "best_weighted": result.get("best_weighted"),
            "pareto_front_count": len(result.get("pareto_front", [])),
        }

    def _build_param_changes(
        self,
        current_params: Dict[str, Any],
        candidate_params: Dict[str, Any],
    ) -> List[Any]:
        """从当前参数和候选参数差异构建 ParamChange 列表"""
        from tools.cross_impact_analyzer import ParamChange

        changes: List[ParamChange] = []
        for group, group_params in candidate_params.items():
            current_group = current_params.get(group, {})
            if not isinstance(group_params, dict):
                continue
            for param, new_value in group_params.items():
                old_value = current_group.get(param)
                if old_value != new_value:
                    changes.append(ParamChange(
                        group=group,
                        param=param,
                        old_value=old_value,
                        new_value=new_value,
                    ))
        return changes

    def _params_equal(
        self,
        params_a: Dict[str, Any],
        params_b: Dict[str, Any],
    ) -> bool:
        """比较两组参数是否等价"""
        # 获取所有键的并集
        all_groups = set(params_a.keys()) | set(params_b.keys())
        for group in all_groups:
            ga = params_a.get(group, {})
            gb = params_b.get(group, {})
            if not isinstance(ga, dict) or not isinstance(gb, dict):
                if ga != gb:
                    return False
                continue
            all_keys = set(ga.keys()) | set(gb.keys())
            for key in all_keys:
                if ga.get(key) != gb.get(key):
                    return False
        return True

    # ============================================================
    # Stage 5: 验证
    # ============================================================
    def _stage5_validate(
        self,
        transactions: List[Any],
        ground_truth: Dict[str, Any],
        current_params: Dict[str, Any],
        stage4: Dict[str, Any],
        objective_weights: Dict[str, float],
    ) -> Dict[str, Any]:
        """
        Stage 5: A/B 测试 + 不变量检查 + 综合决策

        戒律:
        - M1: A/B 测试使用同一数据和真值集
        - M2: 决策附带理由
        - M4: 验证结果持久化
        - P1: recall 大幅下降时拒绝
        - P2: 总命中激增时警告
        - P4: 不修改全局配置

        Returns:
            {
                "ab_test": ABTestResult.to_dict() | None,
                "invariant_check": {...} | None,
                "validation_warnings": List[str],
            }
        """
        from tools.ab_test_runner import ABTestRunner
        from tools.invariant_checker import check_invariants

        warnings: List[str] = []
        candidate_params = stage4.get("best_candidate_params")
        same_as_current = stage4.get("same_as_current", True)

        # 如果候选参数为空或与当前相同，跳过验证
        if not candidate_params or same_as_current:
            return {
                "ab_test": None,
                "invariant_check": None,
                "validation_warnings": ["无候选参数或候选与当前相同，跳过验证"],
            }

        # A/B 测试
        ab_test_result = None
        try:
            runner = ABTestRunner()
            ab_result = runner.run_test(
                test_name="optimization_loop_validation",
                transactions=transactions,
                ground_truth=ground_truth,
                variant_a_params=current_params,
                variant_b_params=candidate_params,
                metric_weights=objective_weights,
                variant_a_name="current",
                variant_b_name="candidate",
            )
            ab_test_result = ab_result.to_dict()
        except Exception as e:
            warnings.append(f"A/B 测试失败: {e}")

        # 不变量检查：对候选参数运行规则并检查 M3/M2
        invariant_check = None
        try:
            from tools.rule_tuner import RuleTuner
            tuner = RuleTuner()
            candidate_hits = tuner._run_rules(transactions, candidate_params)
            # 展平所有规则命中
            all_hits = []
            for rule_name, hits in candidate_hits.items():
                all_hits.extend(hits)

            # 构造简化 state 供 invariant_checker 使用
            # llm_reviewed = all_hits 模拟所有命中都经过审核，避免 P1 误报
            state = {
                "rule_hits": all_hits,
                "llm_reviewed": all_hits,
                "llm_confirmed": [],
                "str_reports": [],
            }
            inv_result = check_invariants(state)
            invariant_check = inv_result

            # 只关注 error 级别违反
            error_violations = [
                v for v in inv_result.get("violations", [])
                if v.get("severity") == "error"
            ]
            if error_violations:
                warnings.append(
                    f"不变量检查发现 {len(error_violations)} 项严重违反: "
                    f"{'; '.join(v.get('detail', '') for v in error_violations)}"
                )
        except Exception as e:
            warnings.append(f"不变量检查失败: {e}")

        return {
            "ab_test": ab_test_result,
            "invariant_check": invariant_check,
            "validation_warnings": warnings,
        }

    # ============================================================
    # 综合推荐
    # ============================================================
    def _build_recommendation(
        self,
        stage2: Dict[str, Any],
        stage4: Dict[str, Any],
        stage5: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        基于五阶段结果构建最终推荐

        决策规则:
        - 候选参数为空或与当前相同 → action="keep"
        - 不变量检查有 error 级别违反 → action="keep"
        - A/B 测试推荐 "B" 且无戒律违反 → action="apply"
        - A/B 测试推荐 "A" → action="keep"
        - A/B 测试推荐 "review" 或 A/B 测试失败 → action="review"

        戒律:
        - M2: 推荐附带理由
        - P1: 候选参数 recall 大幅下降时不推荐应用
        - P2: 候选参数总命中激增时警告
        - P4: 非破坏性，只推荐

        Returns:
            {
                "action": "apply" | "keep" | "review",
                "reason": str,
                "candidate_params": Dict | None,
                "current_metrics": Dict,
                "candidate_metrics": Dict | None,
                "expected_improvement": Dict | None,
                "guardrail_violations": List,
                "guardrail_warnings": List,
            }
        """
        current_metrics = stage2.get("current_metrics", {})
        candidate_params = stage4.get("best_candidate_params")
        same_as_current = stage4.get("same_as_current", True)
        ab_test = stage5.get("ab_test")
        invariant_check = stage5.get("invariant_check")

        violations: List[str] = []
        warnings: List[str] = []

        # 收集不变量检查中的 error 级别违反
        if invariant_check:
            for v in invariant_check.get("violations", []):
                if v.get("severity") == "error":
                    violations.append(f"不变量违反: {v.get('detail', '')}")

        # 收集 A/B 测试的戒律违反和警告
        ab_decision = {}
        candidate_metrics = None
        expected_improvement = None
        if ab_test:
            ab_decision = ab_test.get("decision", {})
            violations.extend(ab_decision.get("guardrail_violations", []))
            warnings.extend(ab_decision.get("guardrail_warnings", []))
            candidate_metrics = ab_test.get("variant_b", {}).get("metrics", {})

            # 计算预期改进
            if candidate_metrics:
                expected_improvement = {}
                for key in ["precision", "recall", "f1"]:
                    old_val = current_metrics.get(key, 0.0)
                    new_val = candidate_metrics.get(key, 0.0)
                    delta = new_val - old_val
                    expected_improvement[key] = {
                        "current": round(old_val, 4),
                        "candidate": round(new_val, 4),
                        "delta": round(delta, 4),
                    }

        # ===== 决策逻辑 =====
        # 1. 无候选参数或与当前相同
        if not candidate_params or same_as_current:
            action = "keep"
            reason = "优化结果与当前参数相同或无有效候选参数，保持当前配置"
        # 2. 有不变量违反
        elif violations:
            action = "keep"
            reason = (
                f"候选参数存在 {len(violations)} 项戒律/不变量违反，"
                f"保留当前配置（戒律 P4: 非破坏性）"
            )
        # 3. A/B 测试推荐 B
        elif ab_decision.get("recommendation") == "B":
            action = "apply"
            score_a = ab_decision.get("weighted_score_a", 0)
            score_b = ab_decision.get("weighted_score_b", 0)
            reason = (
                f"候选参数加权得分({score_b:.4f})优于当前({score_a:.4f})，"
                f"且无戒律违反，建议应用"
            )
        # 4. A/B 测试推荐 A
        elif ab_decision.get("recommendation") == "A":
            action = "keep"
            reason = "候选参数未优于当前参数，保留当前配置"
        # 5. A/B 测试推荐 review 或无 A/B 测试结果
        elif ab_decision.get("recommendation") == "review":
            action = "review"
            reason = "候选参数与当前参数效果相当，需人工判断"
        else:
            # A/B 测试失败或无结果
            action = "review"
            reason = "A/B 测试未产生有效决策，建议人工审核候选参数"

        return {
            "action": action,
            "reason": reason,
            "candidate_params": candidate_params,
            "current_metrics": current_metrics,
            "candidate_metrics": candidate_metrics,
            "expected_improvement": expected_improvement,
            "guardrail_violations": violations,
            "guardrail_warnings": warnings,
        }

    # ============================================================
    # 持久化
    # ============================================================
    def _save_result(self, result: OptimizationLoopResult) -> None:
        """保存闭环结果（戒律 M4: 原子写入）"""
        path = os.path.join(self.storage_dir, f"{result.loop_id}.json")
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
            raise RuntimeError(f"闭环结果保存失败: {e}") from e

        # 更新索引
        self._update_index(result)

    def _update_index(self, result: OptimizationLoopResult) -> None:
        """更新索引文件"""
        index = self._load_index()
        entry = {
            "loop_id": result.loop_id,
            "timestamp": result.timestamp,
            "action": result.recommendation.get("action", ""),
            "transaction_count": result.metadata.get("transaction_count", 0),
            "ground_truth_count": result.metadata.get("ground_truth_count", 0),
            "dataset_name": result.metadata.get("dataset_name", ""),
        }
        # 替换或新增
        index = [e for e in index if e.get("loop_id") != result.loop_id]
        index.append(entry)
        # 按时间倒序
        index.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        tmp_path = self._index_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._index_path)
        except OSError:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    def _load_index(self) -> List[Dict[str, Any]]:
        """加载索引"""
        if not os.path.exists(self._index_path):
            return []
        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    # ============================================================
    # 查询
    # ============================================================
    def list_loops(self) -> List[Dict[str, Any]]:
        """列出所有闭环结果摘要（按时间倒序）"""
        return self._load_index()

    def get_loop(self, loop_id: str) -> Optional[OptimizationLoopResult]:
        """获取指定闭环结果"""
        path = os.path.join(self.storage_dir, f"{loop_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        try:
            return OptimizationLoopResult.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return None

    def delete_loop(self, loop_id: str) -> bool:
        """删除指定闭环结果"""
        path = os.path.join(self.storage_dir, f"{loop_id}.json")
        deleted = False
        if os.path.exists(path):
            try:
                os.remove(path)
                deleted = True
            except OSError:
                pass
        # 从索引移除
        index = self._load_index()
        new_index = [e for e in index if e.get("loop_id") != loop_id]
        if len(new_index) != len(index):
            try:
                with open(self._index_path, "w", encoding="utf-8") as f:
                    json.dump(new_index, f, ensure_ascii=False, indent=2)
            except OSError:
                pass
        return deleted
