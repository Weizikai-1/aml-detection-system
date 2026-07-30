"""
规则3: 对敲交易 (Round-Trip)

检测逻辑: 两个账户7天内互相转账，金额差异≤20%
"""
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple
from graph.state import SuspiciousTransaction, Transaction
from utils import parse_timestamp, get_logger
from config import AML_CONFIG

logger = get_logger("rules.round_trip")


def _make_suspicious(txn, rule_name, evidence, risk_score=50, structured_evidence=None):
    return {
        "transaction": txn, "rule_hits": [rule_name], "risk_score": risk_score,
        "evidence": [evidence], "structured_evidence": structured_evidence or [],
        "graph_evidence": None, "llm_analysis": None, "llm_confidence": None,
        "is_false_positive": None, "community_id": None,
    }


def detect(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    max_days = AML_CONFIG["rules"]["round_trip"]["max_days"]
    max_amount_diff_ratio = AML_CONFIG["rules"]["round_trip"]["max_amount_diff_ratio"]
    min_amount = AML_CONFIG["rules"]["round_trip"]["min_amount"]
    risk_score = AML_CONFIG["rules"]["round_trip"]["risk_score"]

    pair_txns: Dict[Tuple[str, str], List[Tuple[str, str, float, datetime, Transaction]]] = defaultdict(list)

    for txn in transactions:
        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        if not from_acc or not to_acc:
            continue
        amount = txn.get("amount", 0)
        ts = parse_timestamp(txn.get("timestamp"))
        if ts is None or amount < min_amount:
            continue
        if from_acc == to_acc:
            continue

        pair_key = tuple(sorted([from_acc, to_acc]))
        pair_txns[pair_key].append((from_acc, to_acc, amount, ts, txn))

    suspicious = []
    matched_ids = set()

    for pair, txns in pair_txns.items():
        if len(txns) < 2:
            continue

        txns.sort(key=lambda x: x[3])

        for i, (from_i, to_i, amt_i, ts_i, txn_i) in enumerate(txns):
            if txn_i.get("transaction_id") in matched_ids:
                continue

            for j in range(i + 1, len(txns)):
                from_j, to_j, amt_j, ts_j, txn_j = txns[j]

                time_diff = (ts_j - ts_i).days
                if time_diff > max_days:
                    break
                time_diff = abs(time_diff)

                if from_i == from_j or to_i == to_j:
                    continue
                if not (from_i == to_j and to_i == from_j):
                    continue

                max_amt = max(amt_i, amt_j)
                min_amt = min(amt_i, amt_j)
                if max_amt == 0:
                    continue
                diff_ratio = (max_amt - min_amt) / max_amt
                if diff_ratio > max_amount_diff_ratio:
                    continue

                tid_i = txn_i.get("transaction_id")
                tid_j = txn_j.get("transaction_id")

                if tid_i not in matched_ids:
                    matched_ids.add(tid_i)
                    evidence = (
                        f"对敲交易: {pair[0]} ↔ {pair[1]} 互相转账，"
                        f"金额 {amt_i:,.2f} vs {amt_j:,.2f} (差异 {diff_ratio * 100:.1f}%)，"
                        f"时间间隔 {time_diff} 天"
                    )
                    structured_evidence = [{
                        "rule": "对敲交易",
                        "details": {
                            "pair": list(pair), "amounts": [amt_i, amt_j],
                            "diff_ratio": diff_ratio, "time_diff_days": time_diff,
                            "max_days": max_days, "max_amount_diff_ratio": max_amount_diff_ratio,
                        }
                    }]
                    suspicious.append(_make_suspicious(
                        txn_i, "对敲交易", evidence, risk_score=risk_score,
                        structured_evidence=structured_evidence))

                if tid_j not in matched_ids:
                    matched_ids.add(tid_j)
                    evidence2 = (
                        f"对敲交易: {pair[0]} ↔ {pair[1]} 互相转账，"
                        f"金额 {amt_j:,.2f} vs {amt_i:,.2f} (差异 {diff_ratio * 100:.1f}%)"
                    )
                    structured_evidence2 = [{
                        "rule": "对敲交易",
                        "details": {
                            "pair": list(pair), "amounts": [amt_i, amt_j],
                            "diff_ratio": diff_ratio, "time_diff_days": time_diff,
                            "max_days": max_days, "max_amount_diff_ratio": max_amount_diff_ratio,
                        }
                    }]
                    suspicious.append(_make_suspicious(
                        txn_j, "对敲交易", evidence2, risk_score=risk_score,
                        structured_evidence=structured_evidence2))

    return suspicious
