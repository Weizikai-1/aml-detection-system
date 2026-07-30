"""
规则5: 基线偏离检测 (Baseline Deviation)

检测逻辑: 检测账户行为基线偏离交易，包括金额偏离(Z-score)、时段偏离、对手偏离
"""
from typing import List, Dict, Any
from graph.state import SuspiciousTransaction, Transaction
from utils import get_logger
from config import AML_CONFIG

logger = get_logger("rules.baseline_deviation")


def _make_suspicious(txn, rule_name, evidence, risk_score=50, structured_evidence=None):
    return {
        "transaction": txn, "rule_hits": [rule_name], "risk_score": risk_score,
        "evidence": [evidence], "structured_evidence": structured_evidence or [],
        "graph_evidence": None, "llm_analysis": None, "llm_confidence": None,
        "is_false_positive": None, "community_id": None,
    }


def detect(
    transactions: List[Transaction],
    account_baselines: Dict[str, Dict[str, Any]],
) -> List[SuspiciousTransaction]:
    cfg = AML_CONFIG["rules"]["baseline_deviation"]
    min_txns = cfg["min_txns_for_baseline"]
    z_threshold = cfg["amount_zscore_threshold"]
    night_boost = cfg["night_activity_boost"]
    new_cp_weight = cfg["new_counterparty_weight"]
    max_score = cfg["max_risk_score"]

    suspicious = []

    for txn in transactions:
        amount = float(txn.get("amount", 0))
        from_acc = txn.get("from_account", "UNKNOWN")
        to_acc = txn.get("to_account", "UNKNOWN")
        if from_acc and to_acc and from_acc == to_acc:
            continue

        for acc, role in [(from_acc, "付款方"), (to_acc, "收款方")]:
            baseline = account_baselines.get(acc)
            if not baseline:
                continue
            if baseline.get("total_txns", 0) < min_txns:
                continue

            avg_amt = baseline.get("avg_amount", 0)
            std_amt = baseline.get("std_amount", 0)
            if avg_amt <= 0 or std_amt <= 0:
                continue

            z_score = abs(amount - avg_amt) / std_amt
            if z_score < z_threshold:
                continue

            base_score = min(z_score * 10, max_score)
            score = base_score

            deviation_reasons = [f"金额Z-score={z_score:.2f}（均值{avg_amt:,.0f}元，标准差{std_amt:,.0f}元）"]

            is_night = txn.get("is_night", False)
            if night_boost and is_night and baseline.get("night_transaction_ratio", 0) < 0.1:
                score = min(score * 1.3, max_score)
                deviation_reasons.append(f"非活跃时段交易（该账户夜间交易占比仅{baseline['night_transaction_ratio']:.1%}）")

            counterparty = to_acc if role == "付款方" else from_acc
            top_cps = baseline.get("top_counterparties", [])
            cp_count = baseline.get("counterparty_count", 0)
            if counterparty not in top_cps and cp_count >= 3:
                score = min(score * (1 + new_cp_weight), max_score)
                deviation_reasons.append("陌生交易对手")

            evidence = (
                f"基线偏离[{role}]: 账户{acc}单笔{amount:,.0f}元，"
                + "；".join(deviation_reasons)
                + f"，偏离风险评分{round(score)}分"
            )

            structured_evidence = [{
                "rule": "基线偏离",
                "details": {
                    "account": acc, "role": role, "amount": amount,
                    "avg_amount": avg_amt, "std_amount": std_amt,
                    "z_score": z_score, "deviation_reasons": deviation_reasons,
                }
            }]

            suspicious.append(_make_suspicious(
                txn, "基线偏离", evidence, risk_score=round(score),
                structured_evidence=structured_evidence))

    return suspicious
