"""
端到端不变量检查器

在工作流执行后自动运行，验证核心业务戒律是否被满足。
违反时记录告警但不阻塞流程，确保问题可见但不影响已有结果。
"""
from typing import Dict, List, Any


def check_invariants(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    检查工作流状态是否满足核心不变量

    检查项目:
    1. 戒律 M3: 所有风险评分在 [0, 100] 范围内
    2. 戒律 M2: 所有可疑交易有证据链
    3. 戒律 P1: 高风险交易(risk_score>=70)不丢失
    4. 报告数量一致性: 有确认可疑交易时应生成报告

    Args:
        state: 工作流最终状态

    Returns:
        {
            "passed": bool,           # 是否全部通过
            "violations": list,       # 违反列表
            "checked_at": str,        # 检查时间
        }
    """
    from datetime import datetime

    violations = []

    # ===== 不变量1: 戒律 M3 — 风险评分范围 =====
    rule_hits = state.get("rule_hits", [])
    for s in rule_hits:
        score = s.get("risk_score", 0)
        if not isinstance(score, (int, float)):
            violations.append({
                "invariant": "M3_risk_score_range",
                "severity": "error",
                "detail": f"交易{s.get('transaction', {}).get('transaction_id', '')}的风险评分类型无效: {type(score).__name__}",
            })
        elif score < 0 or score > 100:
            violations.append({
                "invariant": "M3_risk_score_range",
                "severity": "error",
                "detail": f"交易{s.get('transaction', {}).get('transaction_id', '')}的风险评分{score}超出[0,100]范围",
            })

    # LLM审核后的交易也要检查
    llm_reviewed = state.get("llm_reviewed", [])
    for s in llm_reviewed:
        score = s.get("risk_score", 0)
        if not isinstance(score, (int, float)):
            violations.append({
                "invariant": "M3_risk_score_range",
                "severity": "error",
                "detail": f"LLM审核交易{s.get('transaction', {}).get('transaction_id', '')}的风险评分类型无效",
            })
        elif score < 0 or score > 100:
            violations.append({
                "invariant": "M3_risk_score_range",
                "severity": "error",
                "detail": f"LLM审核交易{s.get('transaction', {}).get('transaction_id', '')}的风险评分{score}超出[0,100]范围",
            })

    # ===== 不变量2: 戒律 M2 — 证据链不为空 =====
    for s in rule_hits:
        score = s.get("risk_score", 0)
        if isinstance(score, (int, float)) and score >= 60:
            evidence = s.get("evidence", [])
            if not evidence:
                violations.append({
                    "invariant": "M2_evidence_nonempty",
                    "severity": "error",
                    "detail": f"交易{s.get('transaction', {}).get('transaction_id', '')}评分{score}>=60但证据链为空",
                })

    # ===== 不变量3: 戒律 P1 — 高风险交易不丢失 =====
    # 检查rule_hits中risk_score>=70的交易是否在llm_confirmed或llm_reviewed中
    llm_confirmed = state.get("llm_confirmed", [])
    llm_reviewed_ids = set()
    for s in llm_reviewed:
        tid = s.get("transaction", {}).get("transaction_id", "")
        if tid:
            llm_reviewed_ids.add(tid)

    for s in rule_hits:
        score = s.get("risk_score", 0)
        if isinstance(score, (int, float)) and score >= 70:
            tid = s.get("transaction", {}).get("transaction_id", "")
            if tid and llm_reviewed and tid not in llm_reviewed_ids:
                violations.append({
                    "invariant": "P1_high_risk_not_lost",
                    "severity": "warning",
                    "detail": f"高风险交易{tid}(评分{score})未出现在LLM审核结果中",
                })

    # ===== 不变量4: 报告数量一致性 =====
    # 有确认可疑交易时应生成报告（除非LLM降级导致无审核结果）
    str_reports = state.get("str_reports", [])
    if llm_confirmed and not str_reports:
        # 只有当LLM确实确认了可疑交易但没有生成报告时才告警
        violations.append({
            "invariant": "report_consistency",
            "severity": "warning",
            "detail": f"有{len(llm_confirmed)}笔确认可疑交易但未生成STR报告",
        })

    # ===== 不变量5: 风险评分降序排列 =====
    if rule_hits:
        scores = [s.get("risk_score", 0) for s in rule_hits]
        if scores != sorted(scores, reverse=True):
            violations.append({
                "invariant": "risk_score_sorted",
                "severity": "info",
                "detail": "rule_hits未按风险评分降序排列",
            })

    passed = len(violations) == 0

    return {
        "passed": passed,
        "violations": violations,
        "violation_count": len(violations),
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
