"""
记忆检索器 — 从长期记忆中检索相似案例

职责:
- 将案件数据转换为特征向量
- 基于相似度检索历史案件
- 支持误报/漏报记忆的匹配检索
- 结合衰减权重计算最终相似度

设计准则:
- M1: 检索基于真实数据，不编造匹配结果
- P1: 漏报记忆优先匹配，避免漏掉可疑案件
- P2: 误报记忆用于降权，避免误报正常交易
"""
import math
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from tools.memory_manager import MemoryManager, get_memory_manager


# 特征权重配置（反洗钱领域知识）
FEATURE_WEIGHTS = {
    "amount": 0.25,          # 交易金额
    "frequency": 0.20,       # 交易频次
    "rule_hits": 0.25,       # 命中规则
    "account_pattern": 0.15,  # 账户模式（转出/转入数量）
    "time_pattern": 0.15,     # 时间模式（夜间/分散）
}


def _extract_case_features(case_data: dict) -> Dict[str, float]:
    """
    从案件数据提取特征向量

    M1: 只从真实数据中提取，不编造特征
    """
    features = {
        "amount": 0.0,
        "frequency": 0.0,
        "rule_hits": 0.0,
        "account_pattern": 0.0,
        "time_pattern": 0.0,
    }

    transactions = case_data.get("transactions", [])
    if not transactions:
        risk_score = case_data.get("risk_score", case_data.get("score", 0))
        features["amount"] = min(1.0, float(case_data.get("amount", 0)) / 1000000.0)
        features["frequency"] = min(1.0, float(case_data.get("transaction_count", 0)) / 100.0)
        features["rule_hits"] = min(1.0, len(case_data.get("hit_rules", [])) / 10.0)
        features["account_pattern"] = float(risk_score) / 100.0
        features["time_pattern"] = 0.5
        return features

    # 金额特征
    amounts = [float(t.get("amount", 0)) for t in transactions]
    total_amount = sum(amounts)
    features["amount"] = min(1.0, total_amount / 1000000.0)

    # 频次特征
    features["frequency"] = min(1.0, len(transactions) / 100.0)

    # 规则命中特征
    hit_rules = case_data.get("hit_rules", [])
    if not hit_rules:
        hit_rules = []
    features["rule_hits"] = min(1.0, len(hit_rules) / 10.0)

    # 账户模式特征
    from_accounts = set()
    to_accounts = set()
    for t in transactions:
        if t.get("from_account"):
            from_accounts.add(t["from_account"])
        if t.get("to_account"):
            to_accounts.add(t["to_account"])
    account_ratio = len(from_accounts) + len(to_accounts)
    features["account_pattern"] = min(1.0, account_ratio / 20.0)

    # 时间模式特征
    night_count = 0
    for t in transactions:
        ts = t.get("timestamp", "")
        if "T" in str(ts):
            try:
                hour = int(str(ts).split("T")[1].split(":")[0])
                if hour < 6 or hour >= 22:
                    night_count += 1
            except (ValueError, IndexError):
                pass
    features["time_pattern"] = night_count / max(1, len(transactions))

    return features


def _cosine_similarity(v1: Dict[str, float], v2: Dict[str, float]) -> float:
    """计算加权余弦相似度"""
    dot = 0.0
    norm1 = 0.0
    norm2 = 0.0
    for key in FEATURE_WEIGHTS:
        w = FEATURE_WEIGHTS[key]
        a = v1.get(key, 0.0)
        b = v2.get(key, 0.0)
        dot += w * a * b
        norm1 += w * a * a
        norm2 += w * b * b
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (math.sqrt(norm1) * math.sqrt(norm2))


class MemoryRetriever:
    """
    记忆检索器

    支持:
    - 相似案件检索
    - 误报模式匹配（用于降权）
    - 漏报模式匹配（用于提权）
    - 规则表现参考
    """

    def __init__(self, memory_manager: MemoryManager = None):
        self.memory = memory_manager or get_memory_manager()

    def search_similar_cases(self, case_data: dict, top_k: int = 5,
                             min_similarity: float = 0.3) -> List[dict]:
        """
        检索相似案件

        M1: 只返回真实存在的案件
        P2: 结合衰减权重，老案件影响小

        Args:
            case_data: 当前案件数据
            top_k: 返回最相似的K个
            min_similarity: 最小相似度阈值

        Returns:
            相似案件列表，按相似度降序
        """
        query_features = _extract_case_features(case_data)

        # 扫描所有案件记忆
        scored = []
        case_list = self.memory.list_cases(limit=1000)
        for case_meta in case_list:
            full_case = self.memory.get_case(case_meta["id"])
            if not full_case:
                continue
            case_features = _extract_case_features(full_case.get("case_data", {}))
            sim = _cosine_similarity(query_features, case_features)
            # 结合衰减权重
            final_score = sim * case_meta.get("weight", 1.0)
            if final_score >= min_similarity:
                scored.append({
                    "case_id": case_meta["id"],
                    "similarity": round(sim, 4),
                    "weighted_score": round(final_score, 4),
                    "decay_weight": case_meta.get("weight", 1.0),
                    "created_at": case_meta["created_at"],
                    "tags": case_meta.get("tags", []),
                    "case_data": full_case.get("case_data", {}),
                })

        scored.sort(key=lambda x: x["weighted_score"], reverse=True)
        return scored[:top_k]

    def check_false_positive_pattern(self, case_data: dict,
                                     threshold: float = 0.7) -> List[dict]:
        """
        检查是否匹配误报模式

        P2: 不误报 — 如果高度匹配历史误报，建议降权

        Args:
            case_data: 当前案件数据
            threshold: 匹配阈值

        Returns:
            匹配的误报记忆列表
        """
        query_features = _extract_case_features(case_data)

        matches = []
        fp_list = self.memory.get_false_positives(limit=500)
        for fp_meta in fp_list:
            # 懒加载全文
            from tools.memory_manager import MEMORY_FALSE_POSITIVES_DIR
            import os, json
            fpath = os.path.join(MEMORY_FALSE_POSITIVES_DIR, f"{fp_meta['id']}.json")
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    fp_data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                continue

            fp_features = _extract_case_features(fp_data.get("case_data", {}))
            sim = _cosine_similarity(query_features, fp_features)
            final_score = sim * fp_meta.get("weight", 1.0)
            if final_score >= threshold:
                matches.append({
                    "fp_id": fp_meta["id"],
                    "similarity": round(sim, 4),
                    "weighted_score": round(final_score, 4),
                    "reason": fp_data.get("reason", ""),
                    "created_at": fp_meta["created_at"],
                })

        matches.sort(key=lambda x: x["weighted_score"], reverse=True)
        return matches

    def check_false_negative_pattern(self, case_data: dict,
                                     threshold: float = 0.6) -> List[dict]:
        """
        检查是否匹配漏报模式

        P1: 不遗漏 — 如果高度匹配历史漏报，建议提权

        Args:
            case_data: 当前案件数据
            threshold: 匹配阈值

        Returns:
            匹配的漏报记忆列表
        """
        query_features = _extract_case_features(case_data)

        matches = []
        fn_list = self.memory.get_false_negatives(limit=500)
        for fn_meta in fn_list:
            from tools.memory_manager import MEMORY_FALSE_NEGATIVES_DIR
            import os, json
            fpath = os.path.join(MEMORY_FALSE_NEGATIVES_DIR, f"{fn_meta['id']}.json")
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    fn_data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                continue

            fn_features = _extract_case_features(fn_data.get("case_data", {}))
            sim = _cosine_similarity(query_features, fn_features)
            final_score = sim * fn_meta.get("weight", 1.0)
            if final_score >= threshold:
                matches.append({
                    "fn_id": fn_meta["id"],
                    "similarity": round(sim, 4),
                    "weighted_score": round(final_score, 4),
                    "missed_rule": fn_data.get("missed_rule", ""),
                    "created_at": fn_meta["created_at"],
                })

        matches.sort(key=lambda x: x["weighted_score"], reverse=True)
        return matches

    def get_memory_adjustment(self, case_data: dict) -> dict:
        """
        根据记忆计算风险调整建议

        戒律:
        - P1: 漏报匹配 → 提权（加风险分）
        - P2: 误报匹配 → 降权（减风险分）
        - M3: 最终调整在 [-30, +30] 范围内，保证总分在 [0, 100]

        Returns:
            {
                "score_adjustment": int,  # 建议调整分数 [-30, +30]
                "reason": str,            # 调整理由
                "similar_cases": [...],   # 相似案件
                "fp_matches": [...],      # 误报匹配
                "fn_matches": [...],      # 漏报匹配
            }
        """
        similar_cases = self.search_similar_cases(case_data, top_k=3)
        fp_matches = self.check_false_positive_pattern(case_data)
        fn_matches = self.check_false_negative_pattern(case_data)

        adjustment = 0.0
        reasons = []

        # P1: 漏报匹配 → 提权
        if fn_matches:
            fn_bonus = min(20, fn_matches[0]["weighted_score"] * 30)
            adjustment += fn_bonus
            reasons.append(f"匹配 {len(fn_matches)} 个历史漏报模式，加 {fn_bonus:.1f} 分")

        # P2: 误报匹配 → 降权
        if fp_matches:
            fp_penalty = min(25, fp_matches[0]["weighted_score"] * 35)
            adjustment -= fp_penalty
            reasons.append(f"匹配 {len(fp_matches)} 个历史误报模式，减 {fp_penalty:.1f} 分")

        # 相似案件参考（轻微调整）
        if similar_cases:
            avg_risk = 0.0
            for sc in similar_cases:
                cd = sc.get("case_data", {})
                avg_risk += cd.get("risk_score", cd.get("score", 50))
            avg_risk /= len(similar_cases)
            current_risk = case_data.get("risk_score", case_data.get("score", 50))
            diff = avg_risk - current_risk
            reference_adjustment = diff * 0.2  # 只参考20%
            adjustment += reference_adjustment
            if reference_adjustment > 0:
                reasons.append(f"参考 {len(similar_cases)} 个相似案件，微调 +{reference_adjustment:.1f} 分")
            elif reference_adjustment < 0:
                reasons.append(f"参考 {len(similar_cases)} 个相似案件，微调 {reference_adjustment:.1f} 分")

        # M3: 限制在 [-30, +30]
        adjustment = max(-30, min(30, adjustment))

        return {
            "score_adjustment": round(adjustment),
            "reason": "; ".join(reasons) if reasons else "无历史记忆可参考",
            "similar_cases": similar_cases,
            "fp_matches": fp_matches,
            "fn_matches": fn_matches,
        }

    def get_rule_reliability(self, rule_name: str) -> Optional[dict]:
        """获取规则的历史可靠性（用于审核参考）"""
        stat = self.memory.get_rule_stat(rule_name)
        if not stat:
            return None
        return {
            "rule_name": rule_name,
            "precision": round(stat.get("precision", 0), 4),
            "recall": round(stat.get("recall", 0), 4),
            "f1": round(stat.get("f1", 0), 4),
            "total_hits": stat.get("total_hits", 0),
            "total_false_positives": stat.get("total_false_positives", 0),
            "total_false_negatives": stat.get("total_false_negatives", 0),
            "reliability_level": (
                "high" if stat.get("f1", 0) > 0.7
                else "medium" if stat.get("f1", 0) > 0.4
                else "low"
            ),
        }


_default_retriever = None


def get_memory_retriever() -> MemoryRetriever:
    """获取全局记忆检索器单例"""
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = MemoryRetriever()
    return _default_retriever
