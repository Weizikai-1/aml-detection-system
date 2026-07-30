"""
Agent 2: 规则引擎 Agent — 轻量编排器

编排10条检测规则，合并去重，支持备注降分。
"""
import time
from typing import List, Dict
from graph.state import AMLState, SuspiciousTransaction
from agents.rules import (
    detect_smurfing, detect_fast_in_fast_out, detect_round_trip,
    detect_large_amount, detect_baseline_deviation, detect_remark_keywords,
    detect_shell_companies, detect_sanction_list, detect_cross_border,
    detect_crypto_pattern,
)
from config import AML_CONFIG
from utils import get_logger

logger = get_logger("rule_engine")


def _merge_suspicious(all_suspicious: List[List[SuspiciousTransaction]]) -> List[SuspiciousTransaction]:
    """合并多规则命中结果，按交易ID去重，合并命中规则和证据"""
    merged: Dict[str, SuspiciousTransaction] = {}
    for rule_results in all_suspicious:
        for s in rule_results:
            tid = s["transaction"].get("transaction_id", "")
            if tid in merged:
                existing = merged[tid]
                for r in s["rule_hits"]:
                    if r not in existing["rule_hits"]:
                        existing["rule_hits"].append(r)
                for e in s["evidence"]:
                    if e not in existing["evidence"]:
                        existing["evidence"].append(e)
                existing["risk_score"] = max(existing["risk_score"], s["risk_score"])
            else:
                merged[tid] = s
    result = list(merged.values())
    result.sort(key=lambda x: x["risk_score"], reverse=True)
    return result


def _apply_remark_discount(suspicious_list: List[SuspiciousTransaction]) -> List[SuspiciousTransaction]:
    """对命中低风险备注关键词的交易，适度降低风险分"""
    cfg = AML_CONFIG["rules"]["remark_keywords"]
    if not cfg.get("enabled", False):
        return suspicious_list
    low_risk_words = cfg.get("low_risk_keywords", [])
    discount = cfg.get("low_risk_discount", 0.6)
    for s in suspicious_list:
        txn = s["transaction"]
        remark = str(txn.get("remark", "")).strip()
        if not remark:
            continue
        matched_words = [k for k in low_risk_words if k.lower() in remark.lower()]
        if matched_words:
            original_score = s.get("risk_score", 50)
            if not isinstance(original_score, (int, float)):
                original_score = 50
            new_score = max(min(round(original_score * discount), 100), 30)
            s["risk_score"] = new_score
            s["evidence"].append(
                f"备注降分: 交易备注包含正常业务关键词[{', '.join(matched_words)}]，"
                f"风险分从{original_score}调整为{new_score}"
            )
    suspicious_list.sort(key=lambda x: x["risk_score"], reverse=True)
    return suspicious_list


def create_rule_engine_agent(llm=None):
    """创建规则引擎Agent"""

    def rule_engine_node(state: AMLState) -> dict:
        start_time = time.time()
        logger.info("规则引擎启动")
        cleaned = state.get("cleaned_transactions", [])
        valid_cleaned = [t for t in cleaned if isinstance(t.get("amount"), (int, float))]
        if len(valid_cleaned) == 0:
            logger.warning("无交易数据，跳过规则检测")
            return {"rule_hits": [], "rule_hit_count": 0, "rule_details": {},
                    "rule_engine_stats": {"total_checked": 0}, "current_step": "rule_engine"}
        cleaned = valid_cleaned

        # 执行前4条规则
        rules = [
            ("分拆转账", detect_smurfing),
            ("快进快出", detect_fast_in_fast_out),
            ("对敲交易", detect_round_trip),
            ("大额交易", detect_large_amount),
        ]
        all_results = []
        rule_details = {}
        for name, detector in rules:
            result = detector(cleaned)
            all_results.append(result)
            rule_details[name] = len(result)
            logger.info(f"  {name}: {len(result)}笔")

        # 基线偏离 (需要 account_baselines)
        baselines = state.get("account_baselines", {})
        if baselines:
            result = detect_baseline_deviation(cleaned, baselines)
            all_results.append(result)
            rule_details["基线偏离"] = len(result)
        else:
            rule_details["基线偏离"] = 0

        # 其余规则
        for name, detector in [
            ("备注关键词", detect_remark_keywords),
            ("空壳公司", detect_shell_companies),
            ("制裁名单", detect_sanction_list),
            ("跨境交易", detect_cross_border),
            ("虚拟货币", detect_crypto_pattern),
        ]:
            result = detector(cleaned)
            all_results.append(result)
            rule_details[name] = len(result)
            logger.info(f"  {name}: {len(result)}笔")

        all_hits = _merge_suspicious(all_results)
        all_hits = _apply_remark_discount(all_hits)

        elapsed = time.time() - start_time
        logger.info(f"去重后可疑: {len(all_hits)}笔 | 耗时: {elapsed:.2f}s")

        return {
            "rule_hits": all_hits, "rule_hit_count": len(all_hits),
            "rule_details": rule_details,
            "rule_engine_stats": {"total_checked": len(cleaned), "total_hits_unique": len(all_hits)},
            "current_step": "rule_engine", "step_times": {"rule_engine": elapsed},
        }

    return rule_engine_node
