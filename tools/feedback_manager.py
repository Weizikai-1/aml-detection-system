"""
误反馈管理器

允许分析师对分析结果标记误报/漏报，反馈会持久化并影响账户画像，
从而在后续分析中调整风险权重（戒律 P1: 漏报加权；P2: 误报降权）。

戒律遵循:
- M1 真实数据: 反馈来自分析师真实判断，不编造
- M2 必须标注理由: 每条反馈必须附带 reason（戒律 P3: 有证据）
- M4 可追溯: 每条反馈记录 reviewer、timestamp、execution_id
- P1 不遗漏: 漏报标记提高相关账户未来风险评分
- P2 不误报: 误报标记降低相关账户未来风险评分
- P3 有证据: reason 为必填，空理由拒绝记录

存储结构:
    data/feedback/
        ├── index.json                    # 索引（轻量，便于列表）
        └── <feedback_id>.json            # 单条反馈完整记录

反馈类型:
    - false_positive: 系统误判可疑（应降权）
    - false_negative: 系统漏判可疑（应加权）
    - confirmed: 系统判定正确（用于统计准确率）
"""
import os
import json
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional


# 合法反馈类型
VALID_FEEDBACK_TYPES = {"false_positive", "false_negative", "confirmed"}


class FeedbackManager:
    """误反馈管理器"""

    def __init__(self, feedback_dir: str = None):
        if feedback_dir is None:
            from config import FEEDBACK_DIR
            feedback_dir = FEEDBACK_DIR
        self.feedback_dir = feedback_dir
        os.makedirs(self.feedback_dir, exist_ok=True)
        self.index_path = os.path.join(self.feedback_dir, "index.json")

    # ============================================================
    # 记录反馈
    # ============================================================
    def record_feedback(
        self,
        transaction_id: str,
        account: str,
        feedback_type: str,
        reason: str,
        reviewer: str,
        execution_id: str = "",
        original_risk_score: float = 0.0,
        suggested_risk_score: Optional[float] = None,
        rule_hits: Optional[List[str]] = None,
    ) -> str:
        """
        记录一条分析师反馈

        Args:
            transaction_id: 相关交易ID
            account: 受影响账户
            feedback_type: 反馈类型 (false_positive/false_negative/confirmed)
            reason: 反馈理由（必填，戒律 P3）
            reviewer: 分析师标识
            execution_id: 关联的分析执行ID
            original_risk_score: 系统原始风险分
            suggested_risk_score: 分析师建议风险分（可选）
            rule_hits: 命中的规则列表（可选，用于规则准确率统计）

        Returns:
            feedback_id

        Raises:
            ValueError: feedback_type 非法或 reason 为空
        """
        # 戒律 P3: 理由必填
        if not reason or not reason.strip():
            raise ValueError("反馈理由(reason)不能为空（戒律 P3: 有证据）")
        if feedback_type not in VALID_FEEDBACK_TYPES:
            raise ValueError(
                f"非法反馈类型: {feedback_type}，合法值: {VALID_FEEDBACK_TYPES}"
            )
        if not account or not account.strip():
            raise ValueError("受影响账户(account)不能为空")

        # ===== 层2: 内容质量评估 =====
        quality_warnings = self._assess_quality(
            reason, feedback_type, original_risk_score, suggested_risk_score
        )

        # ===== 层3: 一致性校验 =====
        consistency_warnings = self._check_consistency(
            transaction_id, account, feedback_type, reviewer
        )

        feedback_id = f"FB-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now()
        record = {
            "feedback_id": feedback_id,
            "transaction_id": transaction_id,
            "account": account,
            "feedback_type": feedback_type,
            "reason": reason.strip(),
            "reviewer": reviewer or "unknown",
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "created_at": now.timestamp(),
            "execution_id": execution_id,
            "original_risk_score": float(original_risk_score),
            "suggested_risk_score": (
                float(suggested_risk_score) if suggested_risk_score is not None else None
            ),
            "rule_hits": rule_hits or [],
            # 戒律 M4: 记录质量警告便于追溯（不阻止记录创建）
            "quality_warnings": quality_warnings,
            "consistency_warnings": consistency_warnings,
        }

        # 保存单条记录（戒律 M4: 先写临时文件再 os.replace 原子替换，避免崩溃导致半截文件）
        record_path = os.path.join(self.feedback_dir, f"{feedback_id}.json")
        tmp_path = os.path.join(self.feedback_dir, f"{feedback_id}.json.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, record_path)
        except OSError as e:
            # 清理可能残留的临时文件
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise RuntimeError(f"反馈记录保存失败: {e}") from e

        # 更新索引
        self._update_index(record)

        return feedback_id

    # ============================================================
    # 反馈质量三层校验（层2: 内容质量 / 层3: 一致性）
    # ============================================================
    # 戒律 M2/M4: 警告只记录不阻断，保证反馈可追溯的同时提示质量问题
    # 戒律 P3: 通过质量评估引导分析师提供有证据的理由
    MIN_REASON_LENGTH = 5  # 理由最小长度（字符）
    # 过于通用的理由（无实质信息，违反戒律 P3: 有证据）
    GENERIC_REASONS = {
        "误报", "漏报", "正常", "可疑", "错误", "不对", "有问题",
        "false positive", "false negative", "ok", "yes", "no",
    }
    # 同账户反馈滥用阈值（24小时内）
    ACCOUNT_BURST_WINDOW_SEC = 86400  # 24小时
    ACCOUNT_BURST_THRESHOLD = 5

    def _assess_quality(
        self,
        reason: str,
        feedback_type: str,
        original_risk_score: float,
        suggested_risk_score: Optional[float],
    ) -> List[str]:
        """
        层2: 内容质量评估

        评估维度:
        - 理由长度（过短则信息不足，违反戒律 P3: 有证据）
        - 理由实质内容（过于通用则无证据价值）
        - suggested_risk_score 与 feedback_type 的逻辑一致性
        - 风险分差异是否极端

        Returns:
            警告列表（空列表表示无警告）
        """
        warnings: List[str] = []
        reason_stripped = (reason or "").strip()

        # 1. 理由长度过短
        if len(reason_stripped) < self.MIN_REASON_LENGTH:
            warnings.append(
                f"理由长度过短（{len(reason_stripped)}字符），"
                f"建议至少{self.MIN_REASON_LENGTH}字符以提供充分证据（戒律 P3）"
            )

        # 2. 理由过于通用（无实质信息）
        if reason_stripped.lower() in self.GENERIC_REASONS:
            warnings.append(
                f"理由过于通用（'{reason_stripped}'），缺少具体证据（戒律 P3: 有证据）"
            )

        # 3. suggested_risk_score 与 feedback_type 的逻辑一致性
        if suggested_risk_score is not None:
            try:
                sug = float(suggested_risk_score)
                orig = float(original_risk_score) if original_risk_score is not None else 0.0
            except (TypeError, ValueError):
                sug = None
                orig = 0.0

            if sug is not None:
                # false_positive: 系统误判可疑 → 建议分应低于原始分
                if feedback_type == "false_positive" and sug >= orig and orig > 0:
                    warnings.append(
                        f"误报反馈但建议风险分({sug})≥原始分({orig})，"
                        f"逻辑矛盾（误报应降低风险分）"
                    )
                # false_negative: 系统漏判可疑 → 建议分应高于原始分
                elif feedback_type == "false_negative" and sug <= orig:
                    warnings.append(
                        f"漏报反馈但建议风险分({sug})≤原始分({orig})，"
                        f"逻辑矛盾（漏报应提高风险分）"
                    )
                # 极端分差警告（差异>80分可能不合理）
                diff = abs(sug - orig)
                if diff > 80:
                    warnings.append(
                        f"建议风险分与原始分差异过大（{diff:.0f}分），请确认是否合理"
                    )

        return warnings

    def _check_consistency(
        self,
        transaction_id: str,
        account: str,
        feedback_type: str,
        reviewer: str,
    ) -> List[str]:
        """
        层3: 一致性校验

        检查维度:
        - 同一交易已存在不同类型的反馈（潜在分析师意见冲突）
        - reviewer 可追溯性（戒律 M4）
        - 同一账户短时大量反馈（潜在滥用）

        Returns:
            警告列表（空列表表示无警告）
        """
        warnings: List[str] = []

        # 1. reviewer 可追溯性（戒律 M4: 必须可追溯）
        if not reviewer or not reviewer.strip() or reviewer.strip().lower() == "unknown":
            warnings.append(
                "reviewer 为空或unknown，影响可追溯性（戒律 M4）"
            )

        # 2. 同一交易已存在不同类型的反馈（意见冲突）
        if transaction_id:
            existing = self.list_feedback(limit=10000)
            for entry in existing:
                if (entry.get("transaction_id") == transaction_id
                        and entry.get("feedback_type", "") != feedback_type
                        and entry.get("feedback_type") in VALID_FEEDBACK_TYPES):
                    warnings.append(
                        f"交易 {transaction_id} 已存在不同的反馈类型"
                        f"({entry.get('feedback_type')})，本次标记为 {feedback_type}，"
                        f"可能存在分析师意见冲突，请复核"
                    )
                    break  # 一条冲突警告足够

        # 3. 同一账户短时大量反馈（潜在滥用）
        if account:
            now_ts = datetime.now().timestamp()
            account_entries = self.list_feedback(account=account, limit=10000)
            recent_count = 0
            for entry in account_entries:
                created_at = entry.get("created_at", 0.0)
                try:
                    created_ts = float(created_at)
                except (TypeError, ValueError):
                    continue
                if now_ts - created_ts <= self.ACCOUNT_BURST_WINDOW_SEC:
                    recent_count += 1
            # +1 因为本条反馈尚未写入
            if recent_count + 1 > self.ACCOUNT_BURST_THRESHOLD:
                warnings.append(
                    f"账户 {account} 在24小时内已有 {recent_count} 条反馈，"
                    f"本次将达 {recent_count + 1} 条，可能存在反馈滥用，请核实"
                )

        return warnings

    def _update_index(self, record: Dict[str, Any]):
        """更新索引文件"""
        index = self._load_index()
        index_entry = {
            "feedback_id": record["feedback_id"],
            "transaction_id": record["transaction_id"],
            "account": record["account"],
            "feedback_type": record["feedback_type"],
            "reviewer": record["reviewer"],
            "timestamp": record["timestamp"],
            "created_at": record.get("created_at", 0.0),
            "execution_id": record["execution_id"],
            "original_risk_score": record["original_risk_score"],
        }
        # 替换或新增
        index = [e for e in index if e.get("feedback_id") != record["feedback_id"]]
        index.append(index_entry)
        # 按 created_at 倒序
        index.sort(key=lambda x: x.get("created_at", 0.0), reverse=True)
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2, default=str)

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
    def list_feedback(
        self,
        account: Optional[str] = None,
        feedback_type: Optional[str] = None,
        reviewer: Optional[str] = None,
        execution_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        列出反馈（支持过滤）

        Args:
            account: 按账户过滤（精确匹配）
            feedback_type: 按反馈类型过滤
            reviewer: 按分析师过滤
            execution_id: 按执行ID过滤
            limit: 最大返回数

        Returns:
            反馈索引列表（按时间倒序）
        """
        index = self._load_index()
        results = []
        for entry in index:
            if account and entry.get("account") != account:
                continue
            if feedback_type and entry.get("feedback_type") != feedback_type:
                continue
            if reviewer and entry.get("reviewer") != reviewer:
                continue
            if execution_id and entry.get("execution_id") != execution_id:
                continue
            results.append(entry)
        return results[:limit]

    def get_feedback(self, feedback_id: str) -> Optional[Dict[str, Any]]:
        """获取单条反馈完整记录"""
        path = os.path.join(self.feedback_dir, f"{feedback_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def get_feedback_for_account(self, account: str) -> List[Dict[str, Any]]:
        """获取指定账户的所有反馈（完整记录）"""
        entries = self.list_feedback(account=account, limit=10000)
        full_records = []
        for e in entries:
            full = self.get_feedback(e["feedback_id"])
            if full:
                full_records.append(full)
        return full_records

    # ============================================================
    # 统计
    # ============================================================
    def get_stats(self) -> Dict[str, Any]:
        """反馈统计"""
        index = self._load_index()
        if not index:
            return {
                "total": 0,
                "false_positive": 0,
                "false_negative": 0,
                "confirmed": 0,
                "false_positive_rate": 0.0,
                "false_negative_rate": 0.0,
                "accuracy_rate": 0.0,
                "affected_accounts": 0,
                "reviewers": [],
            }

        fp_count = sum(1 for e in index if e.get("feedback_type") == "false_positive")
        fn_count = sum(1 for e in index if e.get("feedback_type") == "false_negative")
        conf_count = sum(1 for e in index if e.get("feedback_type") == "confirmed")
        total = len(index)

        # 准确率 = confirmed / (confirmed + fp + fn)
        judged = conf_count + fp_count + fn_count
        accuracy = conf_count / judged if judged > 0 else 0.0

        # 误报率 = fp / (fp + confirmed)  （系统标记可疑中误报比例）
        sys_flagged = fp_count + conf_count
        fp_rate = fp_count / sys_flagged if sys_flagged > 0 else 0.0

        # 漏报率 = fn / (fn + confirmed)  （系统应标记但漏掉的比例）
        should_flag = fn_count + conf_count
        fn_rate = fn_count / should_flag if should_flag > 0 else 0.0

        accounts = set(e.get("account", "") for e in index if e.get("account"))
        reviewers = list(set(e.get("reviewer", "") for e in index if e.get("reviewer")))

        return {
            "total": total,
            "false_positive": fp_count,
            "false_negative": fn_count,
            "confirmed": conf_count,
            "false_positive_rate": round(fp_rate, 4),
            "false_negative_rate": round(fn_rate, 4),
            "accuracy_rate": round(accuracy, 4),
            "affected_accounts": len(accounts),
            "reviewers": reviewers,
        }

    def get_account_feedback_summary(self, account: str) -> Dict[str, int]:
        """获取单个账户的反馈汇总（用于画像调整）"""
        entries = self.list_feedback(account=account, limit=10000)
        return {
            "false_positive_count": sum(
                1 for e in entries if e.get("feedback_type") == "false_positive"
            ),
            "false_negative_count": sum(
                1 for e in entries if e.get("feedback_type") == "false_negative"
            ),
            "confirmed_count": sum(
                1 for e in entries if e.get("feedback_type") == "confirmed"
            ),
        }

    # ============================================================
    # 反馈权重时间衰减（阶段二-2.2）
    # ============================================================
    # 戒律遵循:
    # - P1: 漏报衰减最慢（半衰期365天），保证长期风险记忆
    # - P2: 误报衰减较快（半衰期90天），允许账户行为正常化
    # - M1: 基于真实反馈时间戳计算，不编造
    # - M4: 权重计算可追溯（基于 created_at）
    #
    # 半衰期模型: weight = 0.5^(age_days / half_life_days)
    # - age_days = 0 时 weight = 1.0（最新反馈满权重）
    # - age_days = half_life 时 weight = 0.5（半衰）
    # - age_days = 2*half_life 时 weight = 0.25
    # 最小权重 0.05（避免完全归零，保留风险痕迹）
    FEEDBACK_HALF_LIFE_DAYS: Dict[str, int] = {
        "false_positive": 90,   # 误报：3个月半衰期
        "false_negative": 365,  # 漏报：1年半衰期（戒律 P1: 不遗漏）
        "confirmed": 180,       # 确认：6个月半衰期
    }
    MIN_FEEDBACK_WEIGHT = 0.05  # 最小权重下限

    def get_feedback_weight(
        self,
        record: Dict[str, Any],
        reference_time: Optional[float] = None,
    ) -> float:
        """
        计算单条反馈的时间衰减权重

        Args:
            record: 反馈记录（需含 created_at 和 feedback_type）
            reference_time: 参考时间戳（秒），默认为当前时间

        Returns:
            权重值 [MIN_FEEDBACK_WEIGHT, 1.0]

        戒律:
        - P1: 漏报衰减慢，长期保留风险记忆
        - P2: 误报衰减快，允许账户行为正常化
        - M4: 基于 created_at 精确计算
        """
        if reference_time is None:
            reference_time = datetime.now().timestamp()

        created_at = record.get("created_at", 0.0)
        try:
            created_ts = float(created_at)
        except (TypeError, ValueError):
            created_ts = 0.0

        # 防御：如果 created_at 异常（未来时间），视为满权重
        age_sec = reference_time - created_ts
        if age_sec <= 0:
            return 1.0

        age_days = age_sec / 86400.0
        fb_type = record.get("feedback_type", "")
        half_life = self.FEEDBACK_HALF_LIFE_DAYS.get(fb_type, 180)  # 默认180天

        # 半衰期模型: 0.5^(age/half_life)
        weight = 0.5 ** (age_days / max(half_life, 1))

        # 应用最小权重下限（戒律 P1: 保留风险痕迹，不归零）
        return max(weight, self.MIN_FEEDBACK_WEIGHT)

    def get_weighted_account_summary(
        self,
        account: str,
        reference_time: Optional[float] = None,
    ) -> Dict[str, float]:
        """
        获取账户的加权反馈汇总（考虑时间衰减）

        Args:
            account: 账户ID
            reference_time: 参考时间戳（秒），默认为当前时间

        Returns:
            {
                "false_positive_weight": 加权误报计数,
                "false_negative_weight": 加权漏报计数,
                "confirmed_weight": 加权确认计数,
                "total_weight": 总权重,
                "raw_count": {"false_positive": n, "false_negative": n, "confirmed": n},
            }
        """
        entries = self.list_feedback(account=account, limit=10000)
        fp_weight = 0.0
        fn_weight = 0.0
        conf_weight = 0.0
        raw_fp = 0
        raw_fn = 0
        raw_conf = 0

        for entry in entries:
            full = self.get_feedback(entry.get("feedback_id", ""))
            if not full:
                continue
            weight = self.get_feedback_weight(full, reference_time)
            fb_type = full.get("feedback_type", "")
            if fb_type == "false_positive":
                fp_weight += weight
                raw_fp += 1
            elif fb_type == "false_negative":
                fn_weight += weight
                raw_fn += 1
            elif fb_type == "confirmed":
                conf_weight += weight
                raw_conf += 1

        return {
            "false_positive_weight": round(fp_weight, 4),
            "false_negative_weight": round(fn_weight, 4),
            "confirmed_weight": round(conf_weight, 4),
            "total_weight": round(fp_weight + fn_weight + conf_weight, 4),
            "raw_count": {
                "false_positive": raw_fp,
                "false_negative": raw_fn,
                "confirmed": raw_conf,
            },
        }

    def get_rule_stats(self) -> Dict[str, Dict[str, int]]:
        """
        按规则统计反馈（哪些规则容易误报/漏报）

        Returns:
            {rule_name: {"false_positive": n, "false_negative": n, "confirmed": n}}
        """
        index = self._load_index()
        rule_stats: Dict[str, Dict[str, int]] = {}
        for entry in index:
            full = self.get_feedback(entry["feedback_id"])
            if not full:
                continue
            fb_type = full.get("feedback_type", "")
            rules = full.get("rule_hits", []) or []
            for rule in rules:
                if rule not in rule_stats:
                    rule_stats[rule] = {
                        "false_positive": 0,
                        "false_negative": 0,
                        "confirmed": 0,
                    }
                if fb_type in rule_stats[rule]:
                    rule_stats[rule][fb_type] += 1
        return rule_stats

    # ============================================================
    # 应用到画像
    # ============================================================
    def apply_to_profile(self, profile_manager) -> Dict[str, int]:
        """
        将反馈汇总应用到账户画像管理器

        戒律:
        - P1: 漏报多的账户提高风险乘数
        - P2: 误报多的账户降低风险乘数
        - M1: 基于真实反馈数据，不编造

        Args:
            profile_manager: AccountProfileManager 实例

        Returns:
            更新统计 {"accounts_updated": n, "fp_total": n, "fn_total": n}
        """
        index = self._load_index()
        # 按账户聚合反馈
        account_feedback: Dict[str, Dict[str, int]] = {}
        for entry in index:
            acc = entry.get("account", "")
            if not acc:
                continue
            if acc not in account_feedback:
                account_feedback[acc] = {
                    "false_positive_count": 0,
                    "false_negative_count": 0,
                }
            fb_type = entry.get("feedback_type", "")
            if fb_type == "false_positive":
                account_feedback[acc]["false_positive_count"] += 1
            elif fb_type == "false_negative":
                account_feedback[acc]["false_negative_count"] += 1

        # 戒律 M4: 既要让删除反馈后画像计数归零，又要保持幂等性
        # 因此遍历"有反馈的账户 ∪ 已有画像的账户"，对每个账户计算应有值，
        # 仅在当前值与应有值不同时才更新（保证幂等），无反馈的账户应有值为 0
        all_accounts = set(account_feedback.keys()) | set(profile_manager.get_all_profiles().keys())

        accounts_updated = 0
        fp_total = 0
        fn_total = 0
        for acc in all_accounts:
            counts = account_feedback.get(
                acc, {"false_positive_count": 0, "false_negative_count": 0}
            )
            profile = profile_manager.get_profile(acc)
            # 只在数值变化时更新（避免无意义写入，保证幂等）
            if (profile.false_positive_count != counts["false_positive_count"]
                    or profile.false_negative_count != counts["false_negative_count"]):
                profile.false_positive_count = counts["false_positive_count"]
                profile.false_negative_count = counts["false_negative_count"]
                accounts_updated += 1
            fp_total += counts["false_positive_count"]
            fn_total += counts["false_negative_count"]

        return {
            "accounts_updated": accounts_updated,
            "fp_total": fp_total,
            "fn_total": fn_total,
        }

    # ============================================================
    # 删除
    # ============================================================
    def delete_feedback(self, feedback_id: str) -> bool:
        """删除单条反馈"""
        path = os.path.join(self.feedback_dir, f"{feedback_id}.json")
        deleted = False
        if os.path.exists(path):
            # 戒律 M4: 捕获 OSError，避免文件被占用等异常导致崩溃
            try:
                os.remove(path)
                deleted = True
            except OSError:
                pass
        # 从索引移除
        index = self._load_index()
        new_index = [e for e in index if e.get("feedback_id") != feedback_id]
        if len(new_index) != len(index):
            with open(self.index_path, "w", encoding="utf-8") as f:
                json.dump(new_index, f, ensure_ascii=False, indent=2, default=str)
        return deleted

    def clear_all(self) -> int:
        """清空所有反馈，返回删除数"""
        count = 0
        if not os.path.exists(self.feedback_dir):
            return 0
        for f in os.listdir(self.feedback_dir):
            if f.endswith(".json"):
                try:
                    os.remove(os.path.join(self.feedback_dir, f))
                    count += 1
                except OSError:
                    pass
        return count
