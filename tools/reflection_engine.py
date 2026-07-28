"""
反思引擎 — 从误报/漏报中学习，持续优化

职责:
- 分析误报原因，提取误报模式
- 分析漏报原因，提取漏报模式
- 生成规则调优建议（但不自动修改，只建议）
- 统计规则表现，找出薄弱环节

设计准则:
- M1: 所有建议基于真实数据，不编造
- P1: 反思结果不得导致漏报（宁可不优化，也不能漏）
- P2: 反思结果不得导致大量误报（建议需谨慎）
- M4: 建议有完整记录，可追溯
"""
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from datetime import datetime

from tools.memory_manager import MemoryManager, get_memory_manager
from tools.memory_retriever import _extract_case_features


class ReflectionEngine:
    """
    反思引擎

    从历史反馈中学习，生成优化建议：
    - 误报分析：为什么会误报？哪些规则容易误报？
    - 漏报分析：为什么会漏报？哪些规则需要加强？
    - 规则调优建议：参数怎么调？（只建议，不自动修改）
    """

    def __init__(self, memory_manager: MemoryManager = None):
        self.memory = memory_manager or get_memory_manager()

    # ============================================================
    # 误报分析
    # ============================================================
    def analyze_false_positives(self, limit: int = 100) -> dict:
        """
        分析误报模式

        M1: 基于真实误报数据，不编造
        P2: 分析结果用于减少误报

        Returns:
            {
                "total_fp": 总数,
                "top_reasons": 误报原因排名,
                "rule_fp_rate": 各规则误报率,
                "common_patterns": 常见误报模式,
                "suggestions": 优化建议列表
            }
        """
        fp_list = self.memory.get_false_positives(limit=limit)
        if not fp_list:
            return {
                "total_fp": 0,
                "top_reasons": [],
                "rule_fp_rate": {},
                "common_patterns": [],
                "suggestions": ["暂无误报数据，无法分析"],
            }

        # 加载完整数据
        full_fps = []
        from tools.memory_manager import MEMORY_FALSE_POSITIVES_DIR
        import os, json
        for fp_meta in fp_list:
            fpath = os.path.join(MEMORY_FALSE_POSITIVES_DIR, f"{fp_meta['id']}.json")
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    full_fps.append(data)
            except (FileNotFoundError, json.JSONDecodeError):
                continue

        # 统计误报原因
        reason_count = defaultdict(int)
        rule_fp_count = defaultdict(int)
        pattern_features = defaultdict(list)

        for fp in full_fps:
            reason = fp.get("reason", "未知原因")
            reason_count[reason] += 1

            case_data = fp.get("case_data", {})
            hit_rules = case_data.get("hit_rules", [])
            for rule in hit_rules:
                rule_fp_count[rule] += 1

            # 提取特征用于模式分析
            features = _extract_case_features(case_data)
            for feat_name, feat_val in features.items():
                pattern_features[feat_name].append(feat_val)

        # 计算常见模式（特征均值）
        common_patterns = []
        for feat_name, values in pattern_features.items():
            if values:
                avg = sum(values) / len(values)
                common_patterns.append({
                    "feature": feat_name,
                    "avg_value": round(avg, 4),
                    "interpretation": self._interpret_feature(feat_name, avg),
                })
        common_patterns.sort(key=lambda x: x["avg_value"], reverse=True)

        # 生成建议
        suggestions = self._generate_fp_suggestions(reason_count, rule_fp_count, common_patterns)

        # 排序
        top_reasons = sorted(reason_count.items(), key=lambda x: x[1], reverse=True)
        rule_fp_rate = dict(sorted(rule_fp_count.items(), key=lambda x: x[1], reverse=True))

        return {
            "total_fp": len(full_fps),
            "top_reasons": [{"reason": r, "count": c} for r, c in top_reasons],
            "rule_fp_rate": rule_fp_rate,
            "common_patterns": common_patterns,
            "suggestions": suggestions,
        }

    # ============================================================
    # 漏报分析
    # ============================================================
    def analyze_false_negatives(self, limit: int = 100) -> dict:
        """
        分析漏报模式

        M1: 基于真实漏报数据，不编造
        P1: 分析结果用于减少漏报

        Returns:
            {
                "total_fn": 总数,
                "top_missed_rules": 漏掉的规则排名,
                "common_patterns": 常见漏报模式,
                "suggestions": 优化建议列表
            }
        """
        fn_list = self.memory.get_false_negatives(limit=limit)
        if not fn_list:
            return {
                "total_fn": 0,
                "top_missed_rules": [],
                "common_patterns": [],
                "suggestions": ["暂无漏报数据，无法分析"],
            }

        # 加载完整数据
        full_fns = []
        from tools.memory_manager import MEMORY_FALSE_NEGATIVES_DIR
        import os, json
        for fn_meta in fn_list:
            fpath = os.path.join(MEMORY_FALSE_NEGATIVES_DIR, f"{fn_meta['id']}.json")
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    full_fns.append(data)
            except (FileNotFoundError, json.JSONDecodeError):
                continue

        # 统计漏掉的规则
        missed_rule_count = defaultdict(int)
        pattern_features = defaultdict(list)

        for fn in full_fns:
            missed_rule = fn.get("missed_rule", "未知规则")
            missed_rule_count[missed_rule] += 1

            case_data = fn.get("case_data", {})
            features = _extract_case_features(case_data)
            for feat_name, feat_val in features.items():
                pattern_features[feat_name].append(feat_val)

        # 常见模式
        common_patterns = []
        for feat_name, values in pattern_features.items():
            if values:
                avg = sum(values) / len(values)
                common_patterns.append({
                    "feature": feat_name,
                    "avg_value": round(avg, 4),
                    "interpretation": self._interpret_feature(feat_name, avg),
                })
        common_patterns.sort(key=lambda x: x["avg_value"], reverse=True)

        # 生成建议
        suggestions = self._generate_fn_suggestions(missed_rule_count, common_patterns)

        # 排序
        top_missed = sorted(missed_rule_count.items(), key=lambda x: x[1], reverse=True)

        return {
            "total_fn": len(full_fns),
            "top_missed_rules": [{"rule": r, "count": c} for r, c in top_missed],
            "common_patterns": common_patterns,
            "suggestions": suggestions,
        }

    # ============================================================
    # 规则表现分析
    # ============================================================
    def analyze_rule_performance(self) -> dict:
        """
        分析所有规则的表现

        M1: 基于真实统计数据
        M4: 数据可追溯

        Returns:
            {
                "rule_count": 规则数,
                "high_performance": 表现好的规则,
                "low_performance": 表现差的规则,
                "suggestions": 调优建议
            }
        """
        all_stats = self.memory.get_all_rule_stats()
        if not all_stats:
            return {
                "rule_count": 0,
                "high_performance": [],
                "low_performance": [],
                "suggestions": ["暂无规则统计数据"],
            }

        high_perf = []
        low_perf = []
        suggestions = []

        for rule_name, stat in all_stats.items():
            precision = stat.get("precision", 0)
            recall = stat.get("recall", 0)
            f1 = stat.get("f1", 0)
            total_hits = stat.get("total_hits", 0)

            entry = {
                "rule_name": rule_name,
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
                "total_hits": total_hits,
                "total_fp": stat.get("total_false_positives", 0),
                "total_fn": stat.get("total_false_negatives", 0),
            }

            if f1 >= 0.7 and total_hits >= 5:
                high_perf.append(entry)
            elif f1 < 0.4 and total_hits >= 3:
                low_perf.append(entry)
                suggestions.append(
                    f"规则 {rule_name} 表现较差（F1={f1:.2f}），建议调整参数或审查规则逻辑"
                )
            elif precision < 0.5 and total_hits >= 5:
                suggestions.append(
                    f"规则 {rule_name} 精确率低（{precision:.2f}），误报多，建议提高阈值"
                )
            elif recall < 0.5 and total_hits >= 5:
                suggestions.append(
                    f"规则 {rule_name} 召回率低（{recall:.2f}），漏报多，建议降低阈值或扩展规则"
                )

        high_perf.sort(key=lambda x: x["f1"], reverse=True)
        low_perf.sort(key=lambda x: x["f1"])

        return {
            "rule_count": len(all_stats),
            "high_performance": high_perf,
            "low_performance": low_perf,
            "suggestions": suggestions if suggestions else ["所有规则表现正常"],
        }

    # ============================================================
    # 生成调优建议（只建议，不自动修改）
    # ============================================================
    def generate_tuning_suggestions(self) -> dict:
        """
        生成综合调优建议

        P2: 只建议，不自动修改规则参数
        M1: 所有建议基于真实数据

        Returns:
            {
                "fp_analysis": 误报分析,
                "fn_analysis": 漏报分析,
                "rule_performance": 规则表现,
                "priority_suggestions": 优先级排序的建议列表
            }
        """
        fp_result = self.analyze_false_positives()
        fn_result = self.analyze_false_negatives()
        rule_result = self.analyze_rule_performance()

        # 合并建议并排序优先级
        all_suggestions = []

        # P1: 漏报相关建议优先级最高
        for i, sug in enumerate(fn_result["suggestions"]):
            all_suggestions.append({
                "priority": "high",
                "category": "漏报优化",
                "suggestion": sug,
                "impact": "减少漏报，提升召回率",
            })

        # 规则表现差的建议
        for i, sug in enumerate(rule_result["suggestions"]):
            if "漏报" in sug or "召回" in sug:
                all_suggestions.append({
                    "priority": "high",
                    "category": "规则调优",
                    "suggestion": sug,
                    "impact": "提升规则表现",
                })
            else:
                all_suggestions.append({
                    "priority": "medium",
                    "category": "规则调优",
                    "suggestion": sug,
                    "impact": "提升规则表现",
                })

        # P2: 误报相关建议中等优先级
        for i, sug in enumerate(fp_result["suggestions"]):
            all_suggestions.append({
                "priority": "medium",
                "category": "误报优化",
                "suggestion": sug,
                "impact": "减少误报，降低审核成本",
            })

        # 去重
        seen = set()
        unique_suggestions = []
        for s in all_suggestions:
            key = s["suggestion"]
            if key not in seen:
                seen.add(key)
                unique_suggestions.append(s)

        return {
            "fp_analysis": fp_result,
            "fn_analysis": fn_result,
            "rule_performance": rule_result,
            "priority_suggestions": unique_suggestions,
            "generated_at": datetime.now().isoformat(),
        }

    # ============================================================
    # 辅助方法
    # ============================================================
    @staticmethod
    def _interpret_feature(feature_name: str, value: float) -> str:
        """解释特征值的业务含义"""
        interpretations = {
            "amount": f"金额{'较大' if value > 0.7 else '中等' if value > 0.3 else '较小'}",
            "frequency": f"交易{'频繁' if value > 0.7 else '中等' if value > 0.3 else '稀少'}",
            "rule_hits": f"命中规则{'较多' if value > 0.7 else '中等' if value > 0.3 else '较少'}",
            "account_pattern": f"涉及账户{'较多' if value > 0.7 else '中等' if value > 0.3 else '较少'}",
            "time_pattern": f"夜间交易{'较多' if value > 0.7 else '中等' if value > 0.3 else '较少'}",
        }
        return interpretations.get(feature_name, f"数值: {value}")

    @staticmethod
    def _generate_fp_suggestions(reason_count: dict, rule_fp_count: dict,
                                 common_patterns: list) -> List[str]:
        """生成误报优化建议"""
        suggestions = []

        if not reason_count:
            return suggestions

        # 按误报原因给建议
        top_reason = max(reason_count.items(), key=lambda x: x[1]) if reason_count else None
        if top_reason and top_reason[1] >= 3:
            if "正常交易" in top_reason[0]:
                suggestions.append("误报主要来自正常交易，建议检查低金额、低频次交易的规则阈值")
            elif "个人" in top_reason[0]:
                suggestions.append("个人账户误报较多，建议增加账户类型白名单机制")

        # 按误报规则给建议
        if rule_fp_count:
            top_rule = max(rule_fp_count.items(), key=lambda x: x[1])
            if top_rule[1] >= 3:
                suggestions.append(f"规则 {top_rule[0]} 误报最多，建议适当提高该规则阈值")

        return suggestions if suggestions else ["误报样本不足，暂无法给出具体建议"]

    @staticmethod
    def _generate_fn_suggestions(missed_rule_count: dict,
                                 common_patterns: list) -> List[str]:
        """生成漏报优化建议"""
        suggestions = []

        if not missed_rule_count:
            return suggestions

        top_rule = max(missed_rule_count.items(), key=lambda x: x[1]) if missed_rule_count else None
        if top_rule and top_rule[1] >= 2:
            suggestions.append(f"规则 {top_rule[0]} 漏报最多，建议降低阈值或扩展规则覆盖范围")

        # 按模式给建议
        for pattern in common_patterns:
            if pattern["feature"] == "amount" and pattern["avg_value"] < 0.3:
                suggestions.append("漏报案件多为小额交易，建议加强小额分散交易的检测")
            elif pattern["feature"] == "frequency" and pattern["avg_value"] < 0.3:
                suggestions.append("漏报案件多为低频交易，建议加强长期低频转移的检测")

        return suggestions if suggestions else ["漏报样本不足，暂无法给出具体建议"]


_default_reflection = None


def get_reflection_engine() -> ReflectionEngine:
    """获取全局反思引擎单例"""
    global _default_reflection
    if _default_reflection is None:
        _default_reflection = ReflectionEngine()
    return _default_reflection
