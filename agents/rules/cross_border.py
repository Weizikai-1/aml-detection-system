"""
规则9: 跨境交易检测 (Cross-Border Transaction)

检测逻辑: 频繁跨境汇款、跨境分拆、大额换汇、高风险地区跨境、地理风险评分加成
"""
from datetime import timedelta
from collections import defaultdict
from typing import Dict, List
from graph.state import SuspiciousTransaction, Transaction
from utils import parse_timestamp, get_logger
from config import AML_CONFIG

logger = get_logger("rules.cross_border")


def _make_suspicious(txn, rule_name, evidence, risk_score=50, structured_evidence=None):
    return {
        "transaction": txn, "rule_hits": [rule_name], "risk_score": risk_score,
        "evidence": [evidence], "structured_evidence": structured_evidence or [],
        "graph_evidence": None, "llm_analysis": None, "llm_confidence": None,
        "is_false_positive": None, "community_id": None,
    }


def detect(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    cfg = AML_CONFIG["rules"].get("cross_border", {})
    if not cfg.get("enabled", True):
        return []

    min_amount = cfg.get("min_amount", 50000)
    frequent_count = cfg.get("frequent_count", 5)
    frequent_days = cfg.get("frequent_days", 7)
    split_threshold = cfg.get("split_threshold", 200000)
    split_count = cfg.get("split_count", 3)
    risk_score = cfg.get("risk_score", 65)
    high_risk_score = cfg.get("high_risk_score", 80)

    high_risk_regions = cfg.get("high_risk_regions", [
        "AE", "SG", "HK", "MO", "PA", "KY", "VG", "BVI",
    ])

    suspicious = []
    cross_border_by_account: Dict[str, List[Transaction]] = defaultdict(list)
    fx_by_account: Dict[str, List[Transaction]] = defaultdict(list)

    for txn in transactions:
        txn_type = str(txn.get("transaction_type", "")).lower()
        currency = str(txn.get("currency", "CNY")).upper()
        counterparty_country = str(txn.get("counterparty_country", "")).upper()

        is_cross_border = (
            currency not in ("CNY", "RMB", "") or
            (counterparty_country and counterparty_country != "CN") or
            txn_type in ("cross_border", "international", "remittance")
        )
        is_fx = txn_type in ("fx", "currency_exchange", "foreign_exchange", "换汇")

        if not is_cross_border and not is_fx:
            continue

        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        if not from_acc and not to_acc:
            continue

        if is_cross_border:
            for acc in [from_acc, to_acc]:
                if acc:
                    cross_border_by_account[acc].append(txn)
        if is_fx:
            for acc in [from_acc, to_acc]:
                if acc:
                    fx_by_account[acc].append(txn)

    # 检测1: 频繁跨境汇款
    for account, txns in cross_border_by_account.items():
        if len(txns) < frequent_count:
            continue

        txns_sorted = sorted(txns, key=lambda x: x.get("timestamp", ""))
        window_start = None
        window_count = 0

        for txn in txns_sorted:
            ts = parse_timestamp(txn.get("timestamp"))
            if ts is None:
                continue
            if window_start is None:
                window_start = ts
                window_count = 1
            elif (ts - window_start).days <= frequent_days:
                window_count += 1
            else:
                window_start = ts
                window_count = 1

            if window_count >= frequent_count:
                total_amount = sum(t.get("amount", 0) for t in txns_sorted)
                for t in txns_sorted:
                    evidence = (
                        f"频繁跨境交易: 账户[{account}]在{frequent_days}天内发生"
                        f"{window_count}笔跨境交易，总金额{total_amount:,.2f}元，超过频率阈值{frequent_count}笔"
                    )
                    structured_evidence = [{
                        "rule": "跨境频繁交易",
                        "details": {
                            "account": account, "window_count": window_count,
                            "frequent_count": frequent_count, "frequent_days": frequent_days,
                            "total_amount": total_amount,
                        }
                    }]
                    suspicious.append(_make_suspicious(
                        t, "跨境频繁交易", evidence, risk_score=risk_score,
                        structured_evidence=structured_evidence))
                break

    # 检测2: 跨境分拆
    for account, txns in cross_border_by_account.items():
        total_cross_border = sum(t.get("amount", 0) for t in txns)
        if total_cross_border < split_threshold:
            continue
        if len(txns) < split_count:
            continue

        amounts = [t.get("amount", 0) for t in txns]
        if amounts:
            avg_amount = sum(amounts) / len(amounts)
            if avg_amount > 0:
                variance = sum((a - avg_amount) ** 2 for a in amounts) / len(amounts)
                cv = (variance ** 0.5) / avg_amount if avg_amount > 0 else 1.0
                if cv < 0.3:
                    for t in txns:
                        evidence = (
                            f"跨境分拆: 账户[{account}]跨境交易{len(txns)}笔，"
                            f"总金额{total_cross_border:,.2f}元，单笔均额{avg_amount:,.2f}元"
                            f"(变异系数{cv:.2f})，疑似将大额资金拆分为多笔跨境转账"
                        )
                        structured_evidence = [{
                            "rule": "跨境分拆",
                            "details": {
                                "account": account, "txn_count": len(txns),
                                "total_amount": total_cross_border, "avg_amount": avg_amount,
                                "cv": cv, "split_threshold": split_threshold,
                            }
                        }]
                        suspicious.append(_make_suspicious(
                            t, "跨境分拆", evidence, risk_score=high_risk_score,
                            structured_evidence=structured_evidence))

    # 检测3: 大额换汇交易
    for account, txns in fx_by_account.items():
        for t in txns:
            amount = t.get("amount", 0)
            if amount < min_amount:
                continue
            evidence = f"大额换汇: 账户[{account}]进行换汇交易，金额{amount:,.2f}元，超过阈值{min_amount:,.2f}元"
            structured_evidence = [{
                "rule": "跨境大额换汇",
                "details": {"account": account, "amount": amount, "min_amount": min_amount}
            }]
            suspicious.append(_make_suspicious(
                t, "跨境大额换汇", evidence, risk_score=risk_score,
                structured_evidence=structured_evidence))

    # 检测4: 高风险地区跨境交易
    for txn in transactions:
        counterparty_country = str(txn.get("counterparty_country", "")).upper()
        if not counterparty_country or counterparty_country == "CN":
            continue
        if counterparty_country not in high_risk_regions:
            continue
        amount = txn.get("amount", 0)
        if amount < min_amount:
            continue
        from_acc = txn.get("from_account", "")
        evidence = f"高风险地区跨境交易: 账户[{from_acc}]与高风险地区[{counterparty_country}]发生交易，金额{amount:,.2f}元"
        structured_evidence = [{
            "rule": "跨境高风险地区",
            "details": {
                "account": from_acc, "counterparty_country": counterparty_country,
                "amount": amount, "min_amount": min_amount,
            }
        }]
        suspicious.append(_make_suspicious(
            txn, "跨境高风险地区", evidence, risk_score=high_risk_score,
            structured_evidence=structured_evidence))

    # 检测5: 地理风险评分加成
    for txn in transactions:
        geo_score = txn.get("geo_risk_score", 0)
        if not isinstance(geo_score, (int, float)) or geo_score < 65:
            continue
        geo_reasons = txn.get("geo_risk_reasons", [])
        from_acc = txn.get("from_account", "")
        to_acc = txn.get("to_account", "")
        if from_acc and to_acc and from_acc == to_acc:
            continue
        amount = txn.get("amount", 0)
        reason_text = "；".join(geo_reasons) if geo_reasons else "地理风险评分异常"
        evidence = f"地理风险加成: 交易地理风险评分{geo_score}分，金额{amount:,.2f}元，理由: {reason_text}"
        structured_evidence = [{
            "rule": "跨境地理风险",
            "details": {"geo_score": geo_score, "amount": amount, "geo_reasons": geo_reasons}
        }]
        suspicious.append(_make_suspicious(
            txn, "跨境地理风险", evidence, risk_score=min(100, int(geo_score)),
            structured_evidence=structured_evidence))

    return suspicious
