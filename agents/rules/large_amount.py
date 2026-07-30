"""
规则4: 大额交易 (Large Amount)

检测逻辑: 单笔交易≥10万元
"""
from typing import List
from graph.state import SuspiciousTransaction, Transaction
from utils import get_logger
from config import AML_CONFIG

logger = get_logger("rules.large_amount")


def _make_suspicious(txn, rule_name, evidence, risk_score=50, structured_evidence=None):
    return {
        "transaction": txn, "rule_hits": [rule_name], "risk_score": risk_score,
        "evidence": [evidence], "structured_evidence": structured_evidence or [],
        "graph_evidence": None, "llm_analysis": None, "llm_confidence": None,
        "is_false_positive": None, "community_id": None,
    }


def detect(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    threshold = AML_CONFIG["rules"]["large_amount"]["threshold"]
    risk_score = AML_CONFIG["rules"]["large_amount"]["risk_score"]

    suspicious = []
    for txn in transactions:
        amount = txn.get("amount", 0)
        if not isinstance(amount, (int, float)) or amount < threshold:
            continue
        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        self_transfer_tag = "（自转账）" if from_acc and to_acc and from_acc == to_acc else ""
        evidence = f"大额交易{self_transfer_tag}: 单笔金额 {amount:,.2f} 元 ≥ 阈值 {threshold:,.2f} 元"
        structured_evidence = [{
            "rule": "大额交易",
            "details": {
                "amount": amount, "threshold": threshold,
                "is_self_transfer": from_acc == to_acc if from_acc and to_acc else False,
            }
        }]
        suspicious.append(_make_suspicious(
            txn, "大额交易", evidence, risk_score=risk_score,
            structured_evidence=structured_evidence))

    return suspicious
