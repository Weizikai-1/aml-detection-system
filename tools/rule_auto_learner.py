"""
规则自适应学习器 (B2-1: 基于反馈闭环的自适应学习)

职责:
- 从 feedback_manager 累积的真实反馈数据中学习
- 按规则统计 FP/FN 率，生成阈值调整建议
- 保守策略：单次调整幅度 ≤20%，需 ≥10 条反馈才触发
- 人工审核 gate：生成建议后需人工 apply_suggestion 才生效

戒律遵守:
- M1: 学习基于真实反馈数据，不编造
- M2: 每条建议附明确理由（哪条规则 FP/FN 率偏高）
- M3: 调整后的风险分仍在 0-100 范围（由 rule_tuner.validate_params 保证）
- M4: 学习过程完整记录（输入统计/算法版本/输出建议）
- P1: FN 率高时优先放宽，避免漏报
- P2: FP 率高时收紧，避免误报；同时高 FP+FN 仅告警不自动调整
- P3: 建议必须经 compare_effect 验证才提交
- P4: 学习失败不影响主流程

存储结构:
    data/rule_suggestions/
        ├── index.json                    # 索引（轻量，便于列表）
        └── <suggestion_id>.json          # 单条建议完整记录
"""
import os
import json
import uuid
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

from config import RULE_AUTO_LEARN_CONFIG, RULE_SUGGESTIONS_DIR


# ============================================================
# 规则名映射：中文规则名 ↔ rule_tuner schema key
# ============================================================
# feedback_manager.get_rule_stats() 返回的 key 是中文规则名
# rule_tuner.TUNABLE_SCHEMA 的 key 是英文标识符
RULE_NAME_MAPPING = {
    "分拆转账": "smurfing",
    "快进快出": "fast_in_fast_out",
    "对敲交易": "round_trip",
    "大额交易": "large_amount",
    "基线偏离": "baseline_deviation",
}

# 反向映射（英文 → 中文）
RULE_NAME_REVERSE = {v: k for k, v in RULE_NAME_MAPPING.items()}


class RuleAutoLearner:
    """
    规则自适应学习器

    用法:
        learner = RuleAutoLearner()

        # 1. 从反馈数据学习，生成调整建议
        suggestions = learner.learn_from_feedback(transactions=real_txns)

        # 2. 查看待审核建议
        pending = learner.list_pending_suggestions()

        # 3. 应用某条建议（人工审核 gate）
        ok, msg = learner.apply_suggestion("SG-xxxxxxxx")

        # 4. 或拒绝建议
        learner.reject_suggestion("SG-xxxxxxxx", reason="人工判断不需要调整")
    """

    def __init__(
        self,
        feedback_manager=None,
        rule_tuner=None,
        storage_dir: str = None,
        config: Dict[str, Any] = None,
    ):
        """
        Args:
            feedback_manager: FeedbackManager 实例，None 时延迟创建
            rule_tuner: RuleTuner 实例，None 时延迟创建
            storage_dir: 建议存储目录，None 时使用 RULE_SUGGESTIONS_DIR
            config: 配置字典，None 时使用 RULE_AUTO_LEARN_CONFIG
        """
        self._feedback_manager = feedback_manager
        self._rule_tuner = rule_tuner
        self.storage_dir = storage_dir or RULE_SUGGESTIONS_DIR
        self.config = config or RULE_AUTO_LEARN_CONFIG
        os.makedirs(self.storage_dir, exist_ok=True)
        self.index_path = os.path.join(self.storage_dir, "index.json")

    # ============================================================
    # 延迟加载依赖
    # ============================================================
    @property
    def feedback_manager(self):
        if self._feedback_manager is None:
            from tools.feedback_manager import FeedbackManager
            self._feedback_manager = FeedbackManager()
        return self._feedback_manager

    @property
    def rule_tuner(self):
        if self._rule_tuner is None:
            from tools.rule_tuner import RuleTuner
            self._rule_tuner = RuleTuner()
        return self._rule_tuner

    # ============================================================
    # 主入口：从反馈数据学习
    # ============================================================
    def learn_from_feedback(
        self,
        transactions: Optional[List[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        从反馈数据学习，生成规则调整建议

        Args:
            transactions: 用于 compare_effect 验证的真实交易数据
                         （None 时跳过验证，仅生成未验证建议）

        Returns:
            建议列表，每条建议包含:
            - suggestion_id: 建议ID
            - rule_name: 规则中文名
            - rule_key: 规则英文 key（对应 rule_tuner.TUNABLE_SCHEMA）
            - action: tighten(收紧) / loosen(放宽) / conflict_warning(冲突告警)
            - reason: 调整理由（戒律 M2）
            - params: 建议的新参数
            - metrics: FP/FN 统计数据
            - validated: 是否经过 compare_effect 验证
            - validation_result: 验证结果（含 before/after 对比）
            - status: pending / applied / rejected / expired
            - created_at: 创建时间
            - algorithm_version: 算法版本（戒律 M4）
        """
        if not self.config.get("enabled", True):
            return []

        # 1. 加载反馈统计
        rule_stats = self.feedback_manager.get_rule_stats()
        if not rule_stats:
            return []

        # 2. 为每条规则生成建议
        suggestions: List[Dict[str, Any]] = []
        for rule_name_cn, stats in rule_stats.items():
            # 跳过不在映射表中的规则（如虚拟货币/跨境等新规则暂不自动调整）
            if rule_name_cn not in RULE_NAME_MAPPING:
                continue

            rule_key = RULE_NAME_MAPPING[rule_name_cn]
            metrics = self._compute_rule_metrics(rule_name_cn, stats)

            # 反馈数不足跳过（戒律：避免小样本过拟合）
            total = metrics["total"]
            min_required = self.config["min_feedback_count"]
            if total < min_required:
                continue

            suggestion = self._generate_suggestion(rule_name_cn, rule_key, metrics)
            if suggestion is None:
                continue

            # 3. 用 compare_effect 验证（如果有交易数据）
            if transactions and suggestion.get("params"):
                validated, result = self._validate_suggestion(suggestion, transactions)
                suggestion["validated"] = validated
                suggestion["validation_result"] = result
                # 戒律 P3: 验证失败（高风险命中数下降过多）则标记不应用
                if not validated:
                    suggestion["status"] = "rejected"
                    suggestion["rejection_reason"] = result.get("reason", "验证失败")
            else:
                suggestion["validated"] = False
                suggestion["validation_result"] = None

            suggestions.append(suggestion)

        # 4. 持久化建议
        if suggestions:
            self.save_suggestions(suggestions)

        return suggestions

    # ============================================================
    # 计算规则指标
    # ============================================================
    def _compute_rule_metrics(
        self,
        rule_name: str,
        stats: Dict[str, int],
    ) -> Dict[str, Any]:
        """
        计算单规则的 FP/FN/confirmed 数量和比率

        戒律 M1: 基于真实反馈统计，不编造

        Args:
            rule_name: 规则中文名
            stats: {"false_positive": n, "false_negative": n, "confirmed": n}

        Returns:
            {
                "rule_name": str,
                "false_positive": int,
                "false_negative": int,
                "confirmed": int,
                "total": int,
                "fp_rate": float,  # FP / (FP + confirmed)
                "fn_rate": float,  # FN / (FN + confirmed)
            }
        """
        fp = stats.get("false_positive", 0)
        fn = stats.get("false_negative", 0)
        confirmed = stats.get("confirmed", 0)
        total = fp + fn + confirmed

        # FP 率 = FP / (FP + confirmed) —— 系统标记可疑中误报比例
        sys_flagged = fp + confirmed
        fp_rate = fp / sys_flagged if sys_flagged > 0 else 0.0

        # FN 率 = FN / (FN + confirmed) —— 系统应标记但漏掉的比例
        should_flag = fn + confirmed
        fn_rate = fn / should_flag if should_flag > 0 else 0.0

        return {
            "rule_name": rule_name,
            "false_positive": fp,
            "false_negative": fn,
            "confirmed": confirmed,
            "total": total,
            "fp_rate": round(fp_rate, 4),
            "fn_rate": round(fn_rate, 4),
        }

    # ============================================================
    # 生成调整建议
    # ============================================================
    def _generate_suggestion(
        self,
        rule_name_cn: str,
        rule_key: str,
        metrics: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        基于指标生成调整建议

        戒律 P1: FN 率高时放宽（避免漏报）
        戒律 P2: FP 率高时收紧（避免误报）
        戒律 P2: 同时高 FP+FN 仅告警不自动调整
        """
        fp_rate = metrics["fp_rate"]
        fn_rate = metrics["fn_rate"]
        fp_threshold = self.config["fp_rate_threshold"]
        fn_threshold = self.config["fn_rate_threshold"]

        fp_high = fp_rate > fp_threshold
        fn_high = fn_rate > fn_threshold

        # 冲突场景：FP 和 FN 同时高（戒律 P2: 仅告警不自动调整）
        if fp_high and fn_high:
            if self.config.get("conflict_warning_only", True):
                return self._make_conflict_warning(rule_name_cn, rule_key, metrics)
            # 如果配置允许调整，优先处理 FP（保守策略，避免误报）

        if fp_high:
            return self._tighten_rule(rule_name_cn, rule_key, metrics)

        if fn_high:
            return self._loosen_rule(rule_name_cn, rule_key, metrics)

        # FP 和 FN 都在阈值内，无需调整
        return None

    def _make_conflict_warning(
        self,
        rule_name_cn: str,
        rule_key: str,
        metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        """生成冲突告警建议（不调整参数）"""
        return self._build_suggestion(
            rule_name_cn=rule_name_cn,
            rule_key=rule_key,
            action="conflict_warning",
            reason=(
                f"规则[{rule_name_cn}] FP率={metrics['fp_rate']:.2f} "
                f"(阈值{self.config['fp_rate_threshold']})，"
                f"FN率={metrics['fn_rate']:.2f} "
                f"(阈值{self.config['fn_rate_threshold']})，"
                f"同时偏高，规则可能存在设计缺陷，建议人工复核"
            ),
            params={},
            metrics=metrics,
        )

    def _tighten_rule(
        self,
        rule_name_cn: str,
        rule_key: str,
        metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        收紧规则阈值（减少误报）

        戒律 P2: 收紧策略针对不同规则特点
        - smurfing: 提高 min_count
        - fast_in_fast_out: 提高 min_ratio
        - round_trip: 降低 max_amount_diff_ratio
        - large_amount: 提高 threshold
        - baseline_deviation: 提高 amount_zscore_threshold
        """
        max_ratio = self.config["max_adjust_ratio"]
        current = self.rule_tuner.get_tunable_params()
        new_params: Dict[str, Any] = {}

        if rule_key == "smurfing":
            old_val = current["smurfing"]["min_count"]
            # 提高 min_count，但不超过 schema 上限 20
            new_val = min(int(old_val * (1 + max_ratio)), 20)
            if new_val > old_val:
                new_params = {"smurfing": {"min_count": new_val}}

        elif rule_key == "fast_in_fast_out":
            old_val = current["fast_in_fast_out"]["min_ratio"]
            # 提高 min_ratio，但不超过 1.0
            new_val = min(round(old_val * (1 + max_ratio), 4), 1.0)
            if new_val > old_val:
                new_params = {"fast_in_fast_out": {"min_ratio": new_val}}

        elif rule_key == "round_trip":
            old_val = current["round_trip"]["max_amount_diff_ratio"]
            # 降低差异比例上限（更严格匹配）
            new_val = max(round(old_val * (1 - max_ratio), 4), 0.0)
            if new_val < old_val:
                new_params = {"round_trip": {"max_amount_diff_ratio": new_val}}

        elif rule_key == "large_amount":
            old_val = current["large_amount"]["threshold"]
            # 提高阈值，但不超过 schema 上限
            new_val = min(round(old_val * (1 + max_ratio), 2), 10000000)
            if new_val > old_val:
                new_params = {"large_amount": {"threshold": new_val}}

        elif rule_key == "baseline_deviation":
            old_val = current["baseline_deviation"]["amount_zscore_threshold"]
            # 提高 Z-score 阈值（更严格的偏离才触发）
            new_val = min(round(old_val * (1 + max_ratio), 2), 10.0)
            if new_val > old_val:
                new_params = {"baseline_deviation": {"amount_zscore_threshold": new_val}}

        if not new_params:
            # 无法进一步收紧（已达上限）
            return self._build_suggestion(
                rule_name_cn=rule_name_cn,
                rule_key=rule_key,
                action="no_action",
                reason=(
                    f"规则[{rule_name_cn}] FP率={metrics['fp_rate']:.2f}偏高，"
                    f"但参数已达上限，无法进一步收紧"
                ),
                params={},
                metrics=metrics,
            )

        return self._build_suggestion(
            rule_name_cn=rule_name_cn,
            rule_key=rule_key,
            action="tighten",
            reason=(
                f"规则[{rule_name_cn}] FP率={metrics['fp_rate']:.2f} "
                f"超过阈值{self.config['fp_rate_threshold']}，"
                f"收紧阈值以减少误报（戒律 P2）"
            ),
            params=new_params,
            metrics=metrics,
        )

    def _loosen_rule(
        self,
        rule_name_cn: str,
        rule_key: str,
        metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        放宽规则阈值（减少漏报）

        戒律 P1: 放宽策略针对不同规则特点
        - smurfing: 降低 min_count
        - fast_in_fast_out: 降低 min_ratio
        - round_trip: 提高 max_amount_diff_ratio
        - large_amount: 降低 threshold
        - baseline_deviation: 降低 amount_zscore_threshold
        """
        max_ratio = self.config["max_adjust_ratio"]
        current = self.rule_tuner.get_tunable_params()
        new_params: Dict[str, Any] = {}

        if rule_key == "smurfing":
            old_val = current["smurfing"]["min_count"]
            # 降低 min_count，但不低于 schema 下限 2
            new_val = max(int(old_val * (1 - max_ratio)), 2)
            if new_val < old_val:
                new_params = {"smurfing": {"min_count": new_val}}

        elif rule_key == "fast_in_fast_out":
            old_val = current["fast_in_fast_out"]["min_ratio"]
            # 降低 min_ratio，但不低于 0.5
            new_val = max(round(old_val * (1 - max_ratio), 4), 0.5)
            if new_val < old_val:
                new_params = {"fast_in_fast_out": {"min_ratio": new_val}}

        elif rule_key == "round_trip":
            old_val = current["round_trip"]["max_amount_diff_ratio"]
            # 提高差异比例上限（更宽松匹配）
            new_val = min(round(old_val * (1 + max_ratio), 4), 1.0)
            if new_val > old_val:
                new_params = {"round_trip": {"max_amount_diff_ratio": new_val}}

        elif rule_key == "large_amount":
            old_val = current["large_amount"]["threshold"]
            # 降低阈值，但不低于 schema 下限
            new_val = max(round(old_val * (1 - max_ratio), 2), 10000)
            if new_val < old_val:
                new_params = {"large_amount": {"threshold": new_val}}

        elif rule_key == "baseline_deviation":
            old_val = current["baseline_deviation"]["amount_zscore_threshold"]
            # 降低 Z-score 阈值（更敏感的偏离触发）
            new_val = max(round(old_val * (1 - max_ratio), 2), 1.0)
            if new_val < old_val:
                new_params = {"baseline_deviation": {"amount_zscore_threshold": new_val}}

        if not new_params:
            return self._build_suggestion(
                rule_name_cn=rule_name_cn,
                rule_key=rule_key,
                action="no_action",
                reason=(
                    f"规则[{rule_name_cn}] FN率={metrics['fn_rate']:.2f}偏高，"
                    f"但参数已达下限，无法进一步放宽"
                ),
                params={},
                metrics=metrics,
            )

        return self._build_suggestion(
            rule_name_cn=rule_name_cn,
            rule_key=rule_key,
            action="loosen",
            reason=(
                f"规则[{rule_name_cn}] FN率={metrics['fn_rate']:.2f} "
                f"超过阈值{self.config['fn_rate_threshold']}，"
                f"放宽阈值以减少漏报（戒律 P1）"
            ),
            params=new_params,
            metrics=metrics,
        )

    def _build_suggestion(
        self,
        rule_name_cn: str,
        rule_key: str,
        action: str,
        reason: str,
        params: Dict[str, Any],
        metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        """构建建议字典"""
        now = datetime.now()
        return {
            "suggestion_id": f"SG-{uuid.uuid4().hex[:8].upper()}",
            "rule_name": rule_name_cn,
            "rule_key": rule_key,
            "action": action,
            "reason": reason,
            "params": params,
            "metrics": metrics,
            "validated": False,
            "validation_result": None,
            "status": "pending",
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "_created_at_ts": time.time_ns() / 1e9,
            "algorithm_version": self.config.get("algorithm_version", "unknown"),
            "ttl_days": self.config.get("suggestion_ttl_days", 30),
        }

    # ============================================================
    # 验证建议
    # ============================================================
    def _validate_suggestion(
        self,
        suggestion: Dict[str, Any],
        transactions: List[Any],
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        用 rule_tuner.compare_effect 验证建议

        戒律 P3: 建议必须经验证才提交
        戒律 P1: 高风险命中数下降超过限制则拒绝

        Returns:
            (is_valid, result_dict)
        """
        params = suggestion.get("params", {})
        if not params:
            # 无参数调整（如冲突告警），无需验证
            return True, {"reason": "无参数调整，无需验证"}

        try:
            comparison = self.rule_tuner.compare_effect(transactions, params)
        except Exception as e:
            return False, {"reason": f"compare_effect 执行失败: {e}"}

        # 戒律 P1: 优先检查规则完全失效（比高风险下降更严重的场景）
        for rule_name, after_count in comparison["after"]["rule_counts"].items():
            if comparison["before"]["rule_counts"].get(rule_name, 0) > 0 and after_count == 0:
                return False, {
                    "reason": f"规则[{rule_name}]调参后不再命中任何交易",
                    "comparison": comparison,
                }

        # 检查高风险命中数下降
        before_high = comparison["before"]["high_risk_hits"]
        after_high = comparison["after"]["high_risk_hits"]
        high_risk_drop_limit = self.config["high_risk_drop_limit"]

        if before_high > 0 and after_high < before_high:
            drop_ratio = (before_high - after_high) / before_high
            if drop_ratio > high_risk_drop_limit:
                return False, {
                    "reason": (
                        f"高风险命中数从 {before_high} 降至 {after_high} "
                        f"（下降 {drop_ratio*100:.0f}% > 限制 {high_risk_drop_limit*100:.0f}%），"
                        f"可能遗漏高风险交易（戒律 P1）"
                    ),
                    "comparison": comparison,
                }

        return True, {
            "reason": "验证通过",
            "comparison": comparison,
        }

    # ============================================================
    # 持久化
    # ============================================================
    def save_suggestions(self, suggestions: List[Dict[str, Any]]) -> int:
        """持久化建议到文件"""
        index = self._load_index()
        saved_count = 0
        for s in suggestions:
            sid = s["suggestion_id"]
            # 写入单条记录
            path = os.path.join(self.storage_dir, f"{sid}.json")
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(s, f, ensure_ascii=False, indent=2, default=str)
                saved_count += 1
            except (OSError, TypeError) as e:
                # 戒律 P4: 单条写入失败不影响其他
                print(f"  [规则学习器] 建议 {sid} 保存失败: {e}")
                continue

            # 更新索引
            index_entry = {
                "suggestion_id": sid,
                "rule_name": s["rule_name"],
                "rule_key": s["rule_key"],
                "action": s["action"],
                "status": s["status"],
                "created_at": s["created_at"],
                "_created_at_ts": s.get("_created_at_ts", 0.0),
                "validated": s.get("validated", False),
            }
            index = [e for e in index if e.get("suggestion_id") != sid]
            index.append(index_entry)

        # 索引按时间倒序
        index.sort(key=lambda x: x.get("_created_at_ts", 0.0), reverse=True)
        try:
            with open(self.index_path, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2, default=str)
        except (OSError, TypeError) as e:
            print(f"  [规则学习器] 索引更新失败: {e}")

        return saved_count

    def _load_index(self) -> List[Dict]:
        """加载索引"""
        if not os.path.exists(self.index_path):
            return []
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    # ============================================================
    # 查询
    # ============================================================
    def list_pending_suggestions(self) -> List[Dict[str, Any]]:
        """列出待审核建议（已过滤过期）"""
        index = self._load_index()
        pending: List[Dict[str, Any]] = []
        now_ts = time.time()
        ttl_seconds = self.config.get("suggestion_ttl_days", 30) * 86400
        for entry in index:
            if entry.get("status") != "pending":
                continue
            # 过期检查
            created_ts = entry.get("_created_at_ts", 0.0)
            if created_ts > 0 and (now_ts - created_ts) > ttl_seconds:
                continue
            pending.append(entry)
        return pending

    def list_all_suggestions(self, limit: int = 100) -> List[Dict[str, Any]]:
        """列出所有建议（含已处理）"""
        return self._load_index()[:limit]

    def get_suggestion(self, suggestion_id: str) -> Optional[Dict[str, Any]]:
        """获取单条建议完整记录"""
        path = os.path.join(self.storage_dir, f"{suggestion_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    # ============================================================
    # 应用 / 拒绝建议
    # ============================================================
    def apply_suggestion(self, suggestion_id: str) -> Tuple[bool, str]:
        """
        应用建议（人工审核 gate）

        戒律 P3: 需人工主动调用才生效
        戒律 M4: 记录应用时间和操作人（可选）

        Returns:
            (success, message)
        """
        suggestion = self.get_suggestion(suggestion_id)
        if suggestion is None:
            return False, f"建议 {suggestion_id} 不存在"

        if suggestion["status"] != "pending":
            return False, f"建议 {suggestion_id} 状态为 {suggestion['status']}，不可应用"

        # 过期检查
        now_ts = time.time()
        created_ts = suggestion.get("_created_at_ts", 0.0)
        ttl_seconds = suggestion.get("ttl_days", 30) * 86400
        if created_ts > 0 and (now_ts - created_ts) > ttl_seconds:
            self._update_suggestion_status(suggestion_id, "expired")
            return False, f"建议 {suggestion_id} 已过期"

        params = suggestion.get("params", {})
        if not params:
            # 无参数调整（如冲突告警），仅标记为已处理
            self._update_suggestion_status(suggestion_id, "applied")
            return True, "无参数调整，已标记为已处理"

        # 验证参数合法性
        is_valid, errors, _ = self.rule_tuner.validate_params(params)
        if not is_valid:
            return False, f"参数校验失败: {'; '.join(errors)}"

        # 应用配置
        try:
            self.rule_tuner.apply_config(params)
            self._update_suggestion_status(suggestion_id, "applied")
            return True, f"建议已应用: {suggestion['reason']}"
        except Exception as e:
            return False, f"应用失败: {e}"

    def reject_suggestion(self, suggestion_id: str, reason: str = "") -> bool:
        """
        拒绝建议

        Args:
            suggestion_id: 建议ID
            reason: 拒绝理由（戒律 M2: 标注理由）

        Returns:
            是否成功
        """
        suggestion = self.get_suggestion(suggestion_id)
        if suggestion is None:
            return False
        if suggestion["status"] != "pending":
            return False
        self._update_suggestion_status(suggestion_id, "rejected", reason=reason)
        return True

    def _update_suggestion_status(
        self,
        suggestion_id: str,
        status: str,
        reason: str = "",
    ):
        """更新建议状态"""
        suggestion = self.get_suggestion(suggestion_id)
        if suggestion is None:
            return
        suggestion["status"] = status
        if reason:
            suggestion["rejection_reason"] = reason
        suggestion["processed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 写回文件
        path = os.path.join(self.storage_dir, f"{suggestion_id}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(suggestion, f, ensure_ascii=False, indent=2, default=str)
        except (OSError, TypeError) as e:
            print(f"  [规则学习器] 建议 {suggestion_id} 状态更新失败: {e}")

        # 更新索引
        index = self._load_index()
        for entry in index:
            if entry.get("suggestion_id") == suggestion_id:
                entry["status"] = status
                break
        try:
            with open(self.index_path, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2, default=str)
        except (OSError, TypeError):
            pass

    # ============================================================
    # 统计
    # ============================================================
    def get_stats(self) -> Dict[str, Any]:
        """获取建议统计"""
        index = self._load_index()
        if not index:
            return {
                "total": 0,
                "pending": 0,
                "applied": 0,
                "rejected": 0,
                "expired": 0,
                "by_action": {},
                "by_rule": {},
            }

        by_action: Dict[str, int] = {}
        by_rule: Dict[str, int] = {}
        for entry in index:
            action = entry.get("action", "unknown")
            by_action[action] = by_action.get(action, 0) + 1
            rule = entry.get("rule_name", "unknown")
            by_rule[rule] = by_rule.get(rule, 0) + 1

        return {
            "total": len(index),
            "pending": sum(1 for e in index if e.get("status") == "pending"),
            "applied": sum(1 for e in index if e.get("status") == "applied"),
            "rejected": sum(1 for e in index if e.get("status") == "rejected"),
            "expired": sum(1 for e in index if e.get("status") == "expired"),
            "by_action": by_action,
            "by_rule": by_rule,
        }
