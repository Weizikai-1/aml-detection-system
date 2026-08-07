"""
规则引擎编排器 — 调度20条规则，合并去重，排序输出
"""
from typing import List, Dict
from rules import ALL_RULES, CORE_RULES
from settings import RISK as RISK_CFG
import logging

log = logging.getLogger("aml.engine")


def run_engine(txns: List[dict], rules_subset: List[str] = None) -> List[dict]:
    """运行规则引擎，返回可疑交易列表（按 risk_score 降序）"""
    # 确保每笔交易有唯一 _idx 用于去重
    for i, txn in enumerate(txns):
        if "_idx" not in txn:
            txn["_idx"] = i

    results = []
    enabled = [r for r in ALL_RULES if rules_subset is None or r[0] in rules_subset]

    for rule_id, rule_fn, rule_name in enabled:
        try:
            hits = rule_fn(txns)
            log.debug(f"{rule_name}: {len(hits)} hits")
            results.extend(hits)
        except Exception as e:
            log.warning(f"{rule_name} 执行异常: {e}")

    return _merge_and_rank(results)


def _merge_and_rank(results: List[dict]) -> List[dict]:
    """按 _idx 去重，合并证据，按风险分降序"""
    merged: Dict[str, dict] = {}
    for r in results:
        txn = r["transaction"]
        tid = str(txn.get("_idx", id(txn)))
        if tid in merged:
            existing = merged[tid]
            existing["evidence"].extend(r["evidence"])
            existing["risk_score"] = max(existing["risk_score"], r["risk_score"])
            if r["rule"] not in existing["rules"]:
                existing["rules"].append(r["rule"])
        else:
            merged[tid] = {
                "transaction": txn,
                "evidence": list(r["evidence"]),
                "risk_score": r["risk_score"],
                "rules": [r["rule"]],
            }

    result_list = list(merged.values())
    result_list.sort(key=lambda x: x["risk_score"], reverse=True)
    return result_list


def summary(hits: List[dict]) -> dict:
    """生成命中摘要（阈值由 settings.RISK 控制）"""
    high_threshold = RISK_CFG.get("levels", {}).get("high", 70)
    medium_threshold = RISK_CFG.get("levels", {}).get("medium", 50)
    return {
        "total_hits": len(hits),
        "by_rule": _count_by_rule(hits),
        "high_risk": len([h for h in hits if h["risk_score"] >= high_threshold]),
        "medium_risk": len([h for h in hits if medium_threshold <= h["risk_score"] < high_threshold]),
        "low_risk": len([h for h in hits if h["risk_score"] < medium_threshold]),
    }


def _count_by_rule(hits: List[dict]) -> dict:
    counts = {}
    for h in hits:
        for r in h["rules"]:
            counts[r] = counts.get(r, 0) + 1
    return counts
