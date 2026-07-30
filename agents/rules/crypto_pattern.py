"""
规则10: 虚拟货币交易检测 (Crypto Pattern)

检测逻辑: OTC模式(多对一汇聚→分发)、混币器特征(小进大出)、法币兑换(关键词+高频)、已知平台关联
"""
from datetime import timedelta
from collections import defaultdict
from typing import Dict, List
from graph.state import SuspiciousTransaction, Transaction
from utils import parse_timestamp, get_logger
from config import AML_CONFIG

logger = get_logger("rules.crypto_pattern")


def _make_suspicious(txn, rule_name, evidence, risk_score=50, structured_evidence=None):
    return {
        "transaction": txn, "rule_hits": [rule_name], "risk_score": risk_score,
        "evidence": [evidence], "structured_evidence": structured_evidence or [],
        "graph_evidence": None, "llm_analysis": None, "llm_confidence": None,
        "is_false_positive": None, "community_id": None,
    }


def detect(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    cfg = AML_CONFIG["rules"].get("crypto_pattern", {})
    if not cfg.get("enabled", True):
        return []

    otc_min_in = cfg.get("otc_hub_min_in", 3)
    otc_min_out = cfg.get("otc_hub_min_out", 3)
    otc_window = timedelta(hours=cfg.get("otc_window_hours", 24))
    otc_score = cfg.get("otc_risk_score", 80)
    mixer_min_in = cfg.get("mixer_min_in_count", 5)
    mixer_max_in_amount = cfg.get("mixer_max_in_amount", 10000)
    mixer_min_out_amount = cfg.get("mixer_min_out_amount", 50000)
    mixer_window = timedelta(minutes=cfg.get("mixer_window_minutes", 30))
    mixer_score = cfg.get("mixer_risk_score", 85)
    fx_keywords = [k.lower() for k in cfg.get("fx_keywords", [])]
    fx_min_count = cfg.get("fx_min_count", 3)
    fx_window = timedelta(hours=cfg.get("fx_window_hours", 24))
    fx_min_amount = cfg.get("fx_min_amount", 5000)
    fx_score = cfg.get("fx_risk_score", 70)
    platform_keywords = [k.lower() for k in cfg.get("known_platform_keywords", [])]
    platform_score = cfg.get("platform_risk_score", 75)

    suspicious = []

    incoming_by_acc: Dict[str, List[Transaction]] = defaultdict(list)
    outgoing_by_acc: Dict[str, List[Transaction]] = defaultdict(list)
    for txn in transactions:
        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        if not from_acc and not to_acc:
            continue
        if from_acc and to_acc and from_acc == to_acc:
            continue
        if from_acc:
            outgoing_by_acc[from_acc].append(txn)
        if to_acc:
            incoming_by_acc[to_acc].append(txn)

    # ========== 检测1: 场外OTC模式 ==========
    all_accounts = set(incoming_by_acc.keys()) | set(outgoing_by_acc.keys())
    for account in all_accounts:
        in_txns = sorted(incoming_by_acc.get(account, []), key=lambda x: x.get("timestamp", ""))
        out_txns = sorted(outgoing_by_acc.get(account, []), key=lambda x: x.get("timestamp", ""))
        if len(in_txns) < otc_min_in or len(out_txns) < otc_min_out:
            continue

        for in_txn in in_txns:
            in_ts = parse_timestamp(in_txn.get("timestamp"))
            if in_ts is None:
                continue
            window_ins = []
            for t in in_txns:
                ts = parse_timestamp(t.get("timestamp"))
                if ts and abs((ts - in_ts).total_seconds()) <= otc_window.total_seconds():
                    window_ins.append(t)
            window_outs = []
            for t in out_txns:
                ts = parse_timestamp(t.get("timestamp"))
                if ts and abs((ts - in_ts).total_seconds()) <= otc_window.total_seconds():
                    window_outs.append(t)

            if len(window_ins) >= otc_min_in and len(window_outs) >= otc_min_out:
                in_total = sum(t.get("amount", 0) for t in window_ins)
                out_total = sum(t.get("amount", 0) for t in window_outs)
                if in_total <= 0 or out_total <= 0:
                    continue
                if out_total / in_total < 0.5:
                    continue

                in_payers = set(t.get("from_account", "") for t in window_ins)
                out_payees = set(t.get("to_account", "") for t in window_outs)
                evidence = (
                    f"虚拟货币OTC模式: 账户[{account}]在{int(otc_window.total_seconds()/3600)}小时内"
                    f"收到{len(window_ins)}笔来自{len(in_payers)}个付款方的转账，"
                    f"随后转出{len(window_outs)}笔至{len(out_payees)}个收款方，"
                    f"入账总额{in_total:,.2f}元，出账总额{out_total:,.2f}元"
                    f"(流转比例{out_total/in_total*100:.1f}%)"
                )
                structured_evidence = [{
                    "rule": "虚拟货币OTC",
                    "details": {
                        "account": account, "window_ins": len(window_ins),
                        "window_outs": len(window_outs), "in_payers": len(in_payers),
                        "out_payees": len(out_payees), "in_total": in_total,
                        "out_total": out_total, "flow_ratio": out_total / in_total,
                    }
                }]
                for t in window_ins + window_outs:
                    suspicious.append(_make_suspicious(
                        t, "虚拟货币OTC", evidence, risk_score=otc_score,
                        structured_evidence=structured_evidence))
                break

    # ========== 检测2: 混币器特征 ==========
    for account, in_txns in incoming_by_acc.items():
        if len(in_txns) < mixer_min_in:
            continue
        in_txns_sorted = sorted(in_txns, key=lambda x: x.get("timestamp", ""))

        for i, anchor in enumerate(in_txns_sorted):
            anchor_ts = parse_timestamp(anchor.get("timestamp"))
            if anchor_ts is None:
                continue
            window_ins = []
            for t in in_txns_sorted[i:]:
                ts = parse_timestamp(t.get("timestamp"))
                if ts is None:
                    continue
                if (ts - anchor_ts) > mixer_window:
                    break
                if t.get("amount", 0) > mixer_max_in_amount:
                    continue
                window_ins.append(t)

            if len(window_ins) < mixer_min_in:
                continue

            out_txns = outgoing_by_acc.get(account, [])
            big_outs = []
            for t in out_txns:
                ts = parse_timestamp(t.get("timestamp"))
                if ts is None:
                    continue
                if ts < anchor_ts:
                    continue
                if (ts - anchor_ts) > mixer_window * 2:
                    continue
                if t.get("amount", 0) < mixer_min_out_amount:
                    continue
                big_outs.append(t)

            if big_outs:
                in_total = sum(t.get("amount", 0) for t in window_ins)
                in_payers = set(t.get("from_account", "") for t in window_ins)
                out_total = sum(t.get("amount", 0) for t in big_outs)
                evidence = (
                    f"混币器特征: 账户[{account}]在{int(mixer_window.total_seconds()/60)}分钟内"
                    f"收到{len(window_ins)}笔来自{len(in_payers)}个付款方的小额转账"
                    f"(单笔≤{mixer_max_in_amount:,.0f}元，总额{in_total:,.2f}元)，"
                    f"随后发生{len(big_outs)}笔大额出账(单笔≥{mixer_min_out_amount:,.0f}元，"
                    f"总额{out_total:,.2f}元)，符合混币器汇聚-打散模式"
                )
                structured_evidence = [{
                    "rule": "虚拟货币混币器",
                    "details": {
                        "account": account, "window_ins": len(window_ins),
                        "in_payers": len(in_payers), "in_total": in_total,
                        "big_outs": len(big_outs), "out_total": out_total,
                    }
                }]
                for t in window_ins + big_outs:
                    suspicious.append(_make_suspicious(
                        t, "虚拟货币混币器", evidence, risk_score=mixer_score,
                        structured_evidence=structured_evidence))
                break

    # ========== 检测3: 法币-虚拟货币兑换 ==========
    if fx_keywords:
        fx_hits_by_acc: Dict[str, List[Transaction]] = defaultdict(list)
        for txn in transactions:
            remark = str(txn.get("remark", "")).lower()
            if not remark:
                continue
            amount = txn.get("amount", 0)
            if amount < fx_min_amount:
                continue
            if any(k in remark for k in fx_keywords):
                from_acc = txn.get("from_account", "")
                to_acc = txn.get("to_account", "")
                for acc in [from_acc, to_acc]:
                    if acc:
                        fx_hits_by_acc[acc].append(txn)

        for account, txns in fx_hits_by_acc.items():
            if len(txns) < fx_min_count:
                continue
            txns_sorted = sorted(txns, key=lambda x: x.get("timestamp", ""))
            for i, anchor in enumerate(txns_sorted):
                anchor_ts = parse_timestamp(anchor.get("timestamp"))
                if anchor_ts is None:
                    continue
                window_txns = []
                for t in txns_sorted[i:]:
                    ts = parse_timestamp(t.get("timestamp"))
                    if ts is None:
                        continue
                    if (ts - anchor_ts) > fx_window:
                        break
                    window_txns.append(t)
                if len(window_txns) >= fx_min_count:
                    total_amount = sum(t.get("amount", 0) for t in window_txns)
                    evidence = (
                        f"虚拟货币兑换: 账户[{account}]在{int(fx_window.total_seconds()/3600)}小时内"
                        f"发生{len(window_txns)}笔含兑换关键词的交易，"
                        f"总金额{total_amount:,.2f}元，符合法币-虚拟货币场外兑换特征"
                    )
                    structured_evidence = [{
                        "rule": "虚拟货币兑换",
                        "details": {
                            "account": account, "window_txns": len(window_txns),
                            "total_amount": total_amount, "fx_min_count": fx_min_count,
                        }
                    }]
                    for t in window_txns:
                        suspicious.append(_make_suspicious(
                            t, "虚拟货币兑换", evidence, risk_score=fx_score,
                            structured_evidence=structured_evidence))
                    break

    # ========== 检测4: 已知平台关联 ==========
    if platform_keywords:
        for txn in transactions:
            remark = str(txn.get("remark", "")).lower()
            if not remark:
                continue
            from_acc = txn.get("from_account")
            to_acc = txn.get("to_account")
            if not from_acc or not to_acc:
                continue
            if from_acc == to_acc:
                continue
            amount = txn.get("amount", 0)
            if amount < fx_min_amount:
                continue
            matched = [k for k in platform_keywords if k in remark]
            if matched:
                evidence = (
                    f"虚拟货币平台关联: 交易备注包含已知平台关键词[{', '.join(matched)}]，"
                    f"金额{amount:,.2f}元，疑似与虚拟货币交易所发生资金往来"
                )
                structured_evidence = [{
                    "rule": "虚拟货币平台关联",
                    "details": {"matched_keywords": matched, "amount": amount}
                }]
                suspicious.append(_make_suspicious(
                    txn, "虚拟货币平台关联", evidence, risk_score=platform_score,
                    structured_evidence=structured_evidence))

    return suspicious
