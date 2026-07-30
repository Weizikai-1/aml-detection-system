"""
规则1: 分拆转账 (Smurfing)

检测逻辑: 同一收款账户1小时内收到≥5笔来自不同付款方、金额在4万-5万之间的转账
"""
from datetime import timedelta
from collections import defaultdict
from typing import Dict, List
from graph.state import SuspiciousTransaction, Transaction
from utils import parse_timestamp, get_logger
from config import AML_CONFIG

logger = get_logger("rules.smurfing")


def _make_suspicious(txn, rule_name, evidence, risk_score=50, structured_evidence=None):
    return {
        "transaction": txn, "rule_hits": [rule_name], "risk_score": risk_score,
        "evidence": [evidence], "structured_evidence": structured_evidence or [],
        "graph_evidence": None, "llm_analysis": None, "llm_confidence": None,
        "is_false_positive": None, "community_id": None,
    }


def detect(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    window_hours = AML_CONFIG["rules"]["smurfing"]["hour_window"]
    min_count = AML_CONFIG["rules"]["smurfing"]["min_count"]
    amount_low = AML_CONFIG["rules"]["smurfing"]["amount_low"]
    amount_high = AML_CONFIG["rules"]["smurfing"]["amount_high"]
    risk_score = AML_CONFIG["rules"]["smurfing"]["risk_score"]

    incoming_by_account: Dict[str, List[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.get("amount", 0) < amount_low or txn.get("amount", 0) > amount_high:
            continue
        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        if not to_acc:
            continue
        if from_acc == to_acc:
            continue
        incoming_by_account[to_acc].append(txn)

    suspicious = []
    for account, txns in incoming_by_account.items():
        if len(txns) < min_count:
            continue

        txns_sorted = sorted(txns, key=lambda x: x.get("timestamp", ""))

        left = 0
        matched_groups = set()
        for right in range(len(txns_sorted)):
            right_ts = parse_timestamp(txns_sorted[right].get("timestamp"))
            if right_ts is None:
                continue

            while left <= right:
                left_ts = parse_timestamp(txns_sorted[left].get("timestamp"))
                if left_ts is None or (right_ts - left_ts) > timedelta(hours=window_hours):
                    left += 1
                else:
                    break

            window_txns = txns_sorted[left: right + 1]
            unique_payers = set(t["from_account"] for t in window_txns)

            if len(unique_payers) >= min_count and len(window_txns) >= min_count:
                window_amounts = [t.get("amount", 0) for t in window_txns]
                min_amount_win = min(window_amounts) if window_amounts else 0
                max_amount_win = max(window_amounts) if window_amounts else 0
                for t in window_txns:
                    tid = t.get("transaction_id")
                    if tid in matched_groups:
                        continue
                    matched_groups.add(tid)
                    evidence = (
                        f"分拆转账: 账户[{account}]在1小时窗口内收到{len(window_txns)}笔转账，"
                        f"来自{len(unique_payers)}个不同付款方，"
                        f"单笔金额{t.get('amount', 0):,.2f}元在({amount_low}-{amount_high})区间内"
                    )
                    structured_evidence = [{
                        "rule": "分拆转账",
                        "details": {
                            "target_account": account,
                            "window_txns": len(window_txns),
                            "unique_payers": len(unique_payers),
                            "amount_range": [min_amount_win, max_amount_win],
                            "config": {
                                "hour_window": window_hours,
                                "min_count": min_count,
                                "amount_low": amount_low,
                                "amount_high": amount_high,
                            }
                        }
                    }]
                    suspicious.append(_make_suspicious(
                        t, "分拆转账", evidence, risk_score=risk_score,
                        structured_evidence=structured_evidence))

    return suspicious
