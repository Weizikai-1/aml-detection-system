"""
规则8: 制裁名单/黑名单检测 (Sanction List)

检测逻辑: 对交易双方进行OFAC SDN、央行关注名单、自定义黑名单、制裁国家、虚拟货币制裁地址匹配
"""
from typing import List
from graph.state import SuspiciousTransaction, Transaction
from utils import get_logger

logger = get_logger("rules.sanction_list")


def _make_suspicious(txn, rule_name, evidence, risk_score=50, structured_evidence=None):
    return {
        "transaction": txn, "rule_hits": [rule_name], "risk_score": risk_score,
        "evidence": [evidence], "structured_evidence": structured_evidence or [],
        "graph_evidence": None, "llm_analysis": None, "llm_confidence": None,
        "is_false_positive": None, "community_id": None,
    }


def detect(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    try:
        from tools.sanction_checker import sanction_checker
    except ImportError:
        return []

    hits = sanction_checker.check_transactions(transactions)

    suspicious = []
    for hit in hits:
        txn = hit["transaction"]
        match_type = hit["match_type"]
        entity = hit["entity"]
        risk_score = hit["risk_score"]
        evidence = hit["evidence"]
        matched_field = hit["matched_field"]

        rule_name = "制裁名单"
        if match_type == "ofac_sdn":
            rule_name = "制裁名单(OFAC SDN)"
        elif match_type == "pboc_watchlist":
            rule_name = "制裁名单(央行关注)"
        elif match_type == "custom_blacklist":
            rule_name = "制裁名单(自定义黑名单)"
        elif match_type == "sanctioned_country":
            rule_name = "制裁名单(制裁国家)"
        elif match_type == "crypto_sanction":
            rule_name = "制裁名单(虚拟货币)"

        suspicious.append(_make_suspicious(
            txn, rule_name, evidence, risk_score=risk_score,
            structured_evidence=[{
                "rule": rule_name,
                "details": {
                    "match_type": match_type, "entity": entity,
                    "matched_field": matched_field,
                }
            }],
        ))

    return suspicious
