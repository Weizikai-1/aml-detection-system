"""
规则2: 快进快出 (Fast-In-Fast-Out)

检测逻辑: 资金进入账户后10分钟内≥95%金额转出
"""
from datetime import timedelta
from collections import defaultdict
from typing import Dict, List
from graph.state import SuspiciousTransaction, Transaction
from utils import parse_timestamp, get_logger
from config import AML_CONFIG

logger = get_logger("rules.fast_in_fast_out")


def _make_suspicious(txn, rule_name, evidence, risk_score=50, structured_evidence=None):
    return {
        "transaction": txn, "rule_hits": [rule_name], "risk_score": risk_score,
        "evidence": [evidence], "structured_evidence": structured_evidence or [],
        "graph_evidence": None, "llm_analysis": None, "llm_confidence": None,
        "is_false_positive": None, "community_id": None,
    }


def detect(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    max_minutes = AML_CONFIG["rules"]["fast_in_fast_out"]["max_minutes"]
    min_ratio = AML_CONFIG["rules"]["fast_in_fast_out"]["min_ratio"]
    min_amount = AML_CONFIG["rules"]["fast_in_fast_out"]["min_amount"]
    score_primary = AML_CONFIG["rules"]["fast_in_fast_out"]["risk_score_primary"]
    score_secondary = AML_CONFIG["rules"]["fast_in_fast_out"]["risk_score_secondary"]

    account_txns: Dict[str, List[Transaction]] = defaultdict(list)
    for txn in transactions:
        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        if not from_acc or not to_acc:
            continue
        if from_acc == to_acc:
            continue
        account_txns[from_acc].append(txn)
        account_txns[to_acc].append(txn)

    suspicious = []
    seen_pairs = set()

    for account, txns in account_txns.items():
        txns_sorted = sorted(txns, key=lambda x: x.get("timestamp", ""))

        for i, txn in enumerate(txns_sorted):
            if txn["to_account"] != account:
                continue
            if txn.get("amount", 0) < min_amount:
                continue

            in_ts = parse_timestamp(txn.get("timestamp"))
            if in_ts is None:
                continue

            in_amount = txn.get("amount", 0)
            if in_amount <= 0:
                continue
            out_amount = 0.0
            out_txns = []

            for j in range(i + 1, len(txns_sorted)):
                t2 = txns_sorted[j]
                if t2["from_account"] != account:
                    continue

                out_ts = parse_timestamp(t2.get("timestamp"))
                if out_ts is None:
                    continue
                if (out_ts - in_ts) > timedelta(minutes=max_minutes):
                    break

                out_amount += t2.get("amount", 0)
                out_txns.append(t2)

                if out_amount >= in_amount * min_ratio:
                    break

            if out_amount >= in_amount * min_ratio and len(out_txns) > 0:
                tid = txn.get("transaction_id")
                role_in = "in"
                if (tid, role_in) not in seen_pairs:
                    seen_pairs.add((tid, role_in))
                    evidence = (
                        f"快进快出: 账户[{account}] {in_ts.strftime('%H:%M:%S')} 入账 {in_amount:,.2f} 元，"
                        f"{max_minutes}分钟内转出 {out_amount:,.2f} 元 "
                        f"(占比 {out_amount / in_amount * 100:.1f}% ≥ {min_ratio * 100:.0f}%)"
                    )
                    structured_evidence = [{
                        "rule": "快进快出",
                        "details": {
                            "account": account, "in_amount": in_amount,
                            "out_amount": out_amount, "ratio": out_amount / in_amount,
                            "max_minutes": max_minutes, "min_ratio": min_ratio,
                        }
                    }]
                    suspicious.append(_make_suspicious(
                        txn, "快进快出", evidence, risk_score=score_primary,
                        structured_evidence=structured_evidence))

                for ot in out_txns:
                    otid = ot.get("transaction_id")
                    role_out = "out"
                    if (otid, role_out) not in seen_pairs:
                        seen_pairs.add((otid, role_out))
                        evidence2 = "快进快出(关联出账): 账户[{}] 该笔出账与前序入账构成快进快出模式".format(account)
                        structured_evidence2 = [{
                            "rule": "快进快出",
                            "details": {
                                "account": account, "in_amount": in_amount,
                                "out_amount": out_amount, "ratio": out_amount / in_amount,
                                "max_minutes": max_minutes, "min_ratio": min_ratio,
                            }
                        }]
                        suspicious.append(_make_suspicious(
                            ot, "快进快出", evidence2, risk_score=score_secondary,
                            structured_evidence=structured_evidence2))

    return suspicious
