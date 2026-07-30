"""
规则6: 备注关键词检测 (Remark Keywords)

检测逻辑: 基于交易备注的高风险关键词检测，命中则标记可疑
"""
from typing import List
from graph.state import SuspiciousTransaction, Transaction
from utils import get_logger
from config import AML_CONFIG

logger = get_logger("rules.remark_keywords")


def _make_suspicious(txn, rule_name, evidence, risk_score=50, structured_evidence=None):
    return {
        "transaction": txn, "rule_hits": [rule_name], "risk_score": risk_score,
        "evidence": [evidence], "structured_evidence": structured_evidence or [],
        "graph_evidence": None, "llm_analysis": None, "llm_confidence": None,
        "is_false_positive": None, "community_id": None,
    }


def detect(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    cfg = AML_CONFIG["rules"]["remark_keywords"]
    if not cfg.get("enabled", False):
        return []

    high_risk_words = cfg.get("high_risk_keywords", [])
    risk_score = cfg.get("risk_score", 55)

    suspicious = []
    for txn in transactions:
        remark = str(txn.get("remark", "")).strip()
        if not remark:
            continue
        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        if from_acc and to_acc and from_acc == to_acc:
            continue

        matched_words = []
        for keyword in high_risk_words:
            if keyword.lower() in remark.lower():
                matched_words.append(keyword)

        if matched_words:
            evidence = (
                f"备注高风险关键词: 交易备注包含敏感词汇[{', '.join(matched_words)}]，"
                f"符合可疑交易备注特征"
            )
            structured_evidence = [{
                "rule": "备注关键词",
                "details": {"matched_words": matched_words}
            }]
            suspicious.append(_make_suspicious(
                txn, "备注关键词", evidence, risk_score=risk_score,
                structured_evidence=structured_evidence))

    return suspicious
