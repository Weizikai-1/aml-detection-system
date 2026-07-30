"""
规则7: 空壳公司识别 (Shell Company)

检测逻辑: 多维度加权识别空壳公司账户（对手分散度高、留存率低、夜间交易多、快进快出特征）
"""
from typing import Dict, List
from graph.state import SuspiciousTransaction, Transaction
from utils import get_logger
from config import AML_CONFIG

logger = get_logger("rules.shell_company")


def _make_suspicious(txn, rule_name, evidence, risk_score=50, structured_evidence=None):
    return {
        "transaction": txn, "rule_hits": [rule_name], "risk_score": risk_score,
        "evidence": [evidence], "structured_evidence": structured_evidence or [],
        "graph_evidence": None, "llm_analysis": None, "llm_confidence": None,
        "is_false_positive": None, "community_id": None,
    }


def detect(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    cfg = AML_CONFIG["rules"]["shell_company"]
    if not cfg.get("enabled", False):
        return []

    min_txns = cfg.get("min_total_txns", 8)
    min_cps = cfg.get("min_counterparties", 5)
    max_retention = cfg.get("max_retention_rate", 0.15)
    night_threshold = cfg.get("night_ratio_threshold", 0.4)
    required_dims = cfg.get("required_dimensions", 3)
    risk_score = cfg.get("risk_score", 75)

    account_txns: Dict[str, List[Transaction]] = {}
    for txn in transactions:
        from_a = txn.get("from_account", "")
        to_a = txn.get("to_account", "")
        if from_a and to_a and from_a == to_a:
            continue
        if from_a:
            account_txns.setdefault(from_a, []).append(txn)
        if to_a:
            account_txns.setdefault(to_a, []).append(txn)

    suspicious = []
    big_threshold = AML_CONFIG["rules"]["large_amount"]["threshold"]

    for account, txns in account_txns.items():
        if len(txns) < min_txns:
            continue

        # 维度 1: 交易对手分散度
        counterparties = set()
        for t in txns:
            if t.get("from_account") != account:
                counterparties.add(t.get("from_account", ""))
            if t.get("to_account") != account:
                counterparties.add(t.get("to_account", ""))
        cp_count = len([c for c in counterparties if c])
        dim_diverse = cp_count >= min_cps

        # 维度 2: 资金留存率
        total_in = sum(t.get("amount", 0) for t in txns if t.get("to_account") == account)
        total_out = sum(t.get("amount", 0) for t in txns if t.get("from_account") == account)
        if total_in > 0:
            retention = max(0, (total_in - total_out) / total_in)
        else:
            retention = 1.0
        dim_low_retention = retention < max_retention

        # 维度 3: 夜间交易占比
        night_count = 0
        for t in txns:
            ts = t.get("timestamp", "")
            if ts:
                try:
                    hour = int(ts[11:13])
                    if hour < 6 or hour >= 22:
                        night_count += 1
                except (ValueError, IndexError):
                    pass
        night_ratio = night_count / len(txns) if len(txns) > 0 else 0
        dim_night = night_ratio >= night_threshold

        # 维度 4: 快进快出特征
        in_txns = [t for t in txns if t.get("to_account") == account]
        out_txns = [t for t in txns if t.get("from_account") == account]
        has_big_in = any(t.get("amount", 0) >= big_threshold for t in in_txns)
        has_big_out = any(t.get("amount", 0) >= big_threshold for t in out_txns)
        dim_turnover = has_big_in and has_big_out and len(in_txns) > 0 and len(out_txns) > 0

        dims_met = sum([dim_diverse, dim_low_retention, dim_night, dim_turnover])
        if dims_met < required_dims:
            continue

        evidence_parts = [f"空壳公司特征（{dims_met}/4 维度满足）:"]
        if dim_diverse:
            evidence_parts.append(f"- 交易对手分散: {cp_count} 个对手账户")
        if dim_low_retention:
            evidence_parts.append(f"- 资金留存率低: {retention*100:.1f}%（低于 {max_retention*100:.0f}%）")
        if dim_night:
            evidence_parts.append(f"- 夜间交易占比高: {night_ratio*100:.1f}%")
        if dim_turnover:
            evidence_parts.append(f"- 快进快出特征: 大额进出并存")
        evidence = "；".join(evidence_parts)

        structured_evidence = [{
            "rule": "空壳公司",
            "details": {
                "account": account, "dimensions_met": dims_met,
                "counterparty_count": cp_count, "retention_rate": retention,
                "night_ratio": night_ratio, "has_turnover": dim_turnover,
            }
        }]
        for txn in txns:
            suspicious.append(_make_suspicious(
                txn, "空壳公司", evidence, risk_score=risk_score,
                structured_evidence=structured_evidence))

    return suspicious
