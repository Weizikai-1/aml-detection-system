"""
反洗钱检测规则 — 20条规则，每条是纯函数
输入: List[dict]  输出: List[dict]
"""
import yaml
import os
from collections import defaultdict
from typing import List, Dict


def _load_yaml():
    path = os.path.join(os.path.dirname(__file__), "config", "rules", "aml_rules.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


CFG = _load_yaml()


def _hits(evidence: List[str], risk: int, txn: dict, rule: str) -> dict:
    return {"transaction": txn, "evidence": evidence, "risk_score": risk, "rule": rule}


# ============================================================
# 规则 1: 分拆转账
# ============================================================
def smurfing(txns: List[dict], cfg=None) -> List[dict]:
    """同收款方 1h 内 ≥5 笔来自不同付款方的小额转账"""
    if cfg is None:
        cfg = CFG["smurfing"]
    window_sec = cfg["hour_window"] * 3600
    results = []
    by_dest = defaultdict(list)
    for t in txns:
        if t.get("type") in ("TRANSFER", "CASH_OUT"):
            by_dest[t.get("nameDest", "")].append(t)

    for dest, group in by_dest.items():
        if len(group) < cfg["min_count"]:
            continue
        group_sorted = sorted(group, key=lambda x: str(x.get("timestamp", x.get("step", ""))))
        for i in range(len(group_sorted)):
            window = [group_sorted[i]]
            for j in range(i + 1, len(group_sorted)):
                try:
                    dt = (pd_to_seconds(group_sorted[j].get("timestamp", group_sorted[j].get("step", "")))
                          - pd_to_seconds(group_sorted[i].get("timestamp", group_sorted[i].get("step", ""))))
                except Exception:
                    continue
                if abs(dt) <= window_sec:
                    window.append(group_sorted[j])
            if len(window) >= cfg["min_count"]:
                payers = {t.get("nameOrig") for t in window}
                amounts = [t.get("amount", 0) for t in window]
                if (len(payers) >= cfg["min_count"]
                        and all(cfg["amount_low"] <= a <= cfg["amount_high"] for a in amounts)):
                    results.append(_hits(
                        [f"分拆转账: {len(window)}笔交易给{dest}, 付款方{len(payers)}个, 总金额{sum(amounts):.0f}"],
                        cfg["risk_score"], window[0], "smurfing"))
                    break
    return results


# ============================================================
# 规则 2: 快进快出
# ============================================================
def fast_in_fast_out(txns: List[dict], cfg=None) -> List[dict]:
    """10分钟内 ≥95% 入账金额转出"""
    if cfg is None:
        cfg = CFG["fast_in_fast_out"]
    results = []
    by_account = defaultdict(lambda: {"in": [], "out": []})
    for t in txns:
        name = t.get("nameOrig", "")
        if not name:
            continue
        by_account[name]["in" if t.get("type") in ("CASH_IN", "TRANSFER") and
                      t.get("nameDest") == name else "out"].append(t)

    for name, flows in by_account.items():
        if not flows["in"] or not flows["out"]:
            continue
        for tin in flows["in"]:
            in_amt = tin.get("amount", 0)
            if in_amt < cfg["min_amount"]:
                continue
            out_total = 0
            for tout in flows["out"]:
                try:
                    dt = abs(pd_to_seconds(tout.get("timestamp", tout.get("step", "")))
                             - pd_to_seconds(tin.get("timestamp", tin.get("step", ""))))
                except Exception:
                    continue
                if dt <= cfg["max_minutes"] * 60:
                    out_total += tout.get("amount", 0)
            if in_amt > 0 and out_total / in_amt >= cfg["min_ratio"]:
                results.append(_hits(
                    [f"快进快出: {name} 入账{in_amt:.0f}, {cfg['max_minutes']}min内转出{out_total:.0f} ({out_total/in_amt*100:.0f}%)"],
                    cfg["risk_score_primary"], tin, "fast_in_fast_out"))
    return results


# ============================================================
# 规则 3: 对敲交易
# ============================================================
def round_trip(txns: List[dict], cfg=None) -> List[dict]:
    """两个账户 7天内互相转账，金额差异 ≤20%"""
    if cfg is None:
        cfg = CFG["round_trip"]
    results = []
    pairs = defaultdict(list)
    for t in txns:
        if t.get("type") != "TRANSFER":
            continue
        a, b = t.get("nameOrig", ""), t.get("nameDest", "")
        if not a or not b:
            continue
        pair = tuple(sorted([a, b]))
        pairs[pair].append(t)

    for (a, b), group in pairs.items():
        if len(group) < 2:
            continue
        a_to_b = [t for t in group if t["nameOrig"] == a]
        b_to_a = [t for t in group if t["nameOrig"] == b]
        if not a_to_b or not b_to_a:
            continue
        for t1 in a_to_b:
            for t2 in b_to_a:
                try:
                    dt = abs(pd_to_seconds(t1.get("timestamp", t1.get("step", "")))
                             - pd_to_seconds(t2.get("timestamp", t2.get("step", ""))))
                except Exception:
                    continue
                if dt > cfg["max_days"] * 86400:
                    continue
                amt1, amt2 = t1.get("amount", 0), t2.get("amount", 0)
                if min(amt1, amt2) < cfg["min_amount"]:
                    continue
                if abs(amt1 - amt2) / max(amt1, amt2) <= cfg["max_amount_diff_ratio"]:
                    results.append(_hits(
                        [f"对敲交易: {a}<->{b}, {amt1:.0f}↔{amt2:.0f}"],
                        cfg["risk_score"], t1, "round_trip"))
    return results


# ============================================================
# 规则 4: 大额交易
# ============================================================
def large_amount(txns: List[dict], cfg=None) -> List[dict]:
    if cfg is None:
        cfg = CFG["large_amount"]
    return [_hits([f"大额交易: {t.get('amount', 0):.0f}"], cfg["risk_score"], t, "large_amount")
            for t in txns if t.get("amount", 0) >= cfg["threshold"]]


# ============================================================
# 规则 5: 基线偏离
# ============================================================
def baseline_deviation(txns: List[dict], cfg=None) -> List[dict]:
    if cfg is None:
        cfg = CFG["baseline_deviation"]
    results = []
    by_account = defaultdict(list)
    for t in txns:
        by_account[t.get("nameOrig", "")].append(t)

    for name, group in by_account.items():
        amounts = [t.get("amount", 0) for t in group]
        if len(amounts) < cfg["min_txns_for_baseline"]:
            continue
        mean_a = sum(amounts) / len(amounts)
        std_a = (sum((a - mean_a) ** 2 for a in amounts) / len(amounts)) ** 0.5
        if std_a == 0:
            continue
        for t in group:
            z = (t.get("amount", 0) - mean_a) / std_a
            if z > cfg["amount_zscore_threshold"]:
                results.append(_hits(
                    [f"基线偏离: {name} Z-score={z:.1f}"],
                    min(int(z * 20), cfg["max_risk_score"]), t, "baseline_deviation"))
    return results


# ============================================================
# 规则 6: 备注关键词
# ============================================================
def remark_keywords(txns: List[dict], cfg=None) -> List[dict]:
    if cfg is None:
        cfg = CFG["remark_keywords"]
    if not cfg.get("enabled"):
        return []
    results = []
    for t in txns:
        remark = str(t.get("remark", "")).strip()
        if not remark:
            continue
        matched_high = [k for k in cfg["high_risk_keywords"] if k in remark]
        matched_low = [k for k in cfg["low_risk_keywords"] if k in remark]
        if matched_high:
            results.append(_hits(
                [f"高风险备注: {', '.join(matched_high)}"], cfg["risk_score"], t, "remark_keywords"))
        elif matched_low:
            results.append(_hits(
                [f"低风险备注: {', '.join(matched_low)}"], int(cfg["risk_score"] * cfg["low_risk_discount"]),
                t, "remark_keywords"))
    return results


# ============================================================
# 规则 7: 空壳公司
# ============================================================
def shell_company(txns: List[dict], cfg=None) -> List[dict]:
    if cfg is None:
        cfg = CFG["shell_company"]
    if not cfg.get("enabled"):
        return []
    results = []
    by_account = defaultdict(list)
    for t in txns:
        by_account[t.get("nameOrig", "")].append(t)

    for name, group in by_account.items():
        if len(group) < cfg["min_total_txns"]:
            continue
        counterparties = len({t.get("nameDest") for t in group})
        if counterparties < cfg["min_counterparties"]:
            continue
        in_amt = sum(t.get("amount", 0) for t in group if t.get("type") == "CASH_IN"
                     or (t.get("type") == "TRANSFER" and t.get("nameDest") == name))
        out_amt = sum(t.get("amount", 0) for t in group if t.get("type") in ("CASH_OUT", "PAYMENT", "DEBIT")
                       or (t.get("type") == "TRANSFER" and t.get("nameOrig") == name))
        total = in_amt + out_amt
        retention = max(in_amt - out_amt, 0) / max(in_amt, 1)
        if total > 0 and retention <= cfg["max_retention_rate"]:
            results.append(_hits(
                [f"空壳公司: {name}, 对手{counterparties}个, 资金留存率{retention:.1%}"],
                cfg["risk_score"], group[0], "shell_company"))
    return results


# ============================================================
# 规则 8: 制裁名单
# ============================================================
def sanction_list(txns: List[dict], cfg=None) -> List[dict]:
    if cfg is None:
        cfg = CFG["sanction_list"]
    watchlist = cfg.get("watchlist", [])
    results = []
    for t in txns:
        for party in [t.get("nameOrig", ""), t.get("nameDest", "")]:
            if any(s.lower() in str(party).lower() for s in watchlist):
                results.append(_hits(
                    [f"制裁名单命中: {party}"], cfg["ofac_risk_score"], t, "sanction_list"))
                break
    return results


# ============================================================
# 规则 9: 跨境交易
# ============================================================
def cross_border(txns: List[dict], cfg=None) -> List[dict]:
    if cfg is None:
        cfg = CFG["cross_border"]
    high_risk = set(cfg.get("high_risk_regions", []))
    results = []
    for t in txns:
        currency = str(t.get("currency", "")).upper()
        region = str(t.get("region", "")).upper()
        if currency not in ("CNY", "RMB", "") or region in high_risk:
            results.append(_hits(
                [f"跨境: 币种={currency}, 地区={region}"],
                cfg["risk_score"], t, "cross_border"))
    return results


# ============================================================
# 规则 10: 虚拟货币
# ============================================================
def crypto_pattern(txns: List[dict], cfg=None) -> List[dict]:
    if cfg is None:
        cfg = CFG["crypto_pattern"]
    keywords = cfg.get("generic_keywords", [])
    results = []
    for t in txns:
        remark = str(t.get("remark", "")).upper()
        matched = [k for k in keywords if k.upper() in remark]
        if matched:
            results.append(_hits(
                [f"虚拟货币关联: {', '.join(matched)}"], cfg["risk_score"], t, "crypto_pattern"))
    return results


# ============================================================
# 工具函数
# ============================================================
def pd_to_seconds(ts) -> float:
    """各种时间格式 → 秒数。PaySim step(小时)自动×3600"""
    import pandas as pd
    try:
        result = pd.Timestamp(ts).timestamp()
    except Exception:
        try:
            result = float(ts)
        except Exception:
            return 0.0
    # PaySim step 值 1-744（小时），需转为秒；Unix 时间戳 > 1e9
    if result < 1e5:
        result *= 3600
    return result


# ============================================================
# 规则 11: 循环转账
# ============================================================
def circular_flow(txns: List[dict], cfg=None) -> List[dict]:
    """资金通过多个账户循环回到原点 A→B→C→A"""
    if cfg is None:
        cfg = CFG["circular_flow"]
    if not cfg.get("enabled"):
        return []

    graph = defaultdict(list)
    for t in txns:
        if t.get("type") == "TRANSFER" and t.get("amount", 0) >= cfg["min_amount"]:
            graph[t.get("nameOrig", "")].append((t.get("nameDest", ""), t))

    results = []
    visited_cycles = set()

    def dfs(start, current, path, depth):
        if depth > cfg["max_depth"]:
            return
        for nxt, txn in graph.get(current, []):
            if nxt == start and len(path) >= 2:
                cycle_key = tuple(sorted(path + [current]))
                if cycle_key not in visited_cycles:
                    visited_cycles.add(cycle_key)
                    chain = " → ".join(path + [current, start])
                    results.append(_hits(
                        [f"循环转账: {chain}, {len(path)+1}层"], cfg["risk_score"],
                        path[0] if isinstance(path[0], dict) else txn, "circular_flow"))
                return
            if nxt not in path:
                dfs(start, nxt, path + [current], depth + 1)

    for node in list(graph.keys())[:200]:
        dfs(node, node, [], 0)

    return results


# ============================================================
# 规则 12: 整数金额
# ============================================================
def round_amount(txns: List[dict], cfg=None) -> List[dict]:
    """频繁的整数/整齐金额交易（洗钱特征：避免零头引起注意）"""
    if cfg is None:
        cfg = CFG["round_amount"]
    if not cfg.get("enabled"):
        return []

    by_account = defaultdict(list)
    for t in txns:
        amt = t.get("amount", 0)
        if amt >= cfg["min_amount"] and amt % 10000 == 0:
            by_account[t.get("nameOrig", "")].append(t)

    results = []
    for name, group in by_account.items():
        if len(group) >= cfg["min_txns"]:
            results.append(_hits(
                [f"整数金额: {name} {len(group)}笔整齐金额交易, 总计{sum(t.get('amount',0) for t in group):.0f}"],
                cfg["risk_score"], group[0], "round_amount"))
    return results


# ============================================================
# 规则 13: 高频交易
# ============================================================
def high_frequency(txns: List[dict], cfg=None) -> List[dict]:
    """短时间大量交易"""
    if cfg is None:
        cfg = CFG["high_frequency"]
    if not cfg.get("enabled"):
        return []

    by_account = defaultdict(list)
    for t in txns:
        by_account[t.get("nameOrig", "")].append(t)

    results = []
    window_steps = cfg["window_minutes"] / 60.0  # PaySim step=小时
    for name, group in by_account.items():
        if len(group) < cfg["min_txns"]:
            continue
        group_sorted = sorted(group, key=lambda x: float(x.get("step", 0)))
        for i in range(len(group_sorted)):
            window = []
            for j in range(i, len(group_sorted)):
                if float(group_sorted[j].get("step", 0)) - float(group_sorted[i].get("step", 0)) <= window_steps:
                    window.append(group_sorted[j])
            if len(window) >= cfg["min_txns"]:
                total = sum(t.get("amount", 0) for t in window)
                if total >= cfg["min_total_amount"]:
                    results.append(_hits(
                        [f"高频交易: {name} {len(window)}笔/{cfg['window_minutes']}min, 总额{total:.0f}"],
                        cfg["risk_score"], window[0], "high_frequency"))
                    break
    return results


# ============================================================
# 规则 14: 余额清空
# ============================================================
def balance_drain(txns: List[dict], cfg=None) -> List[dict]:
    """交易后原账户余额接近归零（资金转移特征）"""
    if cfg is None:
        cfg = CFG["balance_drain"]
    if not cfg.get("enabled"):
        return []

    results = []
    for t in txns:
        amt = t.get("amount", 0)
        old_bal = t.get("oldbalanceOrg", 0)
        new_bal = t.get("newbalanceOrig", 0)
        if amt >= cfg["min_amount"] and old_bal > 0:
            ratio = (old_bal - new_bal) / old_bal
            if ratio >= cfg["drain_ratio"] and new_bal <= amt * 0.1:
                results.append(_hits(
                    [f"余额清空: {t.get('nameOrig','')} 余额{old_bal:.0f}→{new_bal:.0f} ({ratio:.0%})"],
                    cfg["risk_score"], t, "balance_drain"))
    return results


# ============================================================
# 规则 15: 交易类型跳跃
# ============================================================
def type_jump(txns: List[dict], cfg=None) -> List[dict]:
    """同一账户从低风险类型突变为高风险类型（CASH_OUT/TRANSFER）"""
    if cfg is None:
        cfg = CFG["type_jump"]
    if not cfg.get("enabled"):
        return []

    high_risk_types = set(cfg.get("high_risk_types", []))
    by_account = defaultdict(list)
    for t in txns:
        by_account[t.get("nameOrig", "")].append(t)

    results = []
    for name, group in by_account.items():
        if len(group) < 3:
            continue
        group_sorted = sorted(group, key=lambda x: float(x.get("step", 0)))
        safe_txns = [t for t in group_sorted if t.get("type") not in high_risk_types]
        high_txns = [t for t in group_sorted if t.get("type") in high_risk_types
                     and t.get("amount", 0) >= cfg["min_amount"]]
        if safe_txns and high_txns:
            ts_safe = float(safe_txns[-1].get("step", 0))
            for ht in high_txns:
                if float(ht.get("step", 0)) - ts_safe <= cfg["max_minutes"] / 60.0:
                    results.append(_hits(
                        [f"类型跳跃: {name} {safe_txns[-1].get('type')}→{ht.get('type')} "
                         f"金额{ht.get('amount',0):.0f}"],
                        cfg["risk_score"], ht, "type_jump"))
                    break
    return results


# ============================================================
# 规则 16: 夜间异常
# ============================================================
def night_activity(txns: List[dict], cfg=None) -> List[dict]:
    """夜间非营业时间高频交易（PaySim step=小时, 0-23循环）"""
    if cfg is None:
        cfg = CFG["night_activity"]
    if not cfg.get("enabled"):
        return []
    night_start = cfg["night_start"]
    night_end = cfg["night_end"]

    def is_night(step):
        hour = float(step) % 24
        if night_start > night_end:  # 跨午夜: 22-6
            return hour >= night_start or hour < night_end
        return night_start <= hour < night_end

    night_txns = [t for t in txns if is_night(t.get("step", 0))]
    if not night_txns:
        return []

    by_account = defaultdict(list)
    for t in night_txns:
        if t.get("amount", 0) >= cfg["min_amount"]:
            by_account[t.get("nameOrig", "")].append(t)

    results = []
    for name, group in by_account.items():
        if len(group) >= cfg["min_txns"]:
            total = sum(t.get("amount", 0) for t in group)
            results.append(_hits(
                [f"夜间异常: {name} 夜间{len(group)}笔, 总额{total:.0f}"],
                cfg["risk_score"], group[0], "night_activity"))
    return results


# ============================================================
# 规则 17: 中介账户
# ============================================================
def intermediary(txns: List[dict], cfg=None) -> List[dict]:
    """频繁的 A→X→B 中介模式（资金过桥）"""
    if cfg is None:
        cfg = CFG["intermediary"]
    if not cfg.get("enabled"):
        return []

    incoming = defaultdict(set)
    outgoing = defaultdict(set)
    for t in txns:
        if t.get("type") != "TRANSFER" or t.get("amount", 0) < cfg["min_amount"]:
            continue
        outgoing[t.get("nameOrig", "")].add(t.get("nameDest", ""))
        incoming[t.get("nameDest", "")].add(t.get("nameOrig", ""))

    results = []
    for account in set(outgoing.keys()) & set(incoming.keys()):
        if (len(incoming[account]) >= cfg["min_counterparties"]
                and len(outgoing[account]) >= cfg["min_counterparties"]):
            results.append(_hits(
                [f"中介账户: {account} 入{len(incoming[account])}方/出{len(outgoing[account])}方"],
                cfg["risk_score"],
                {"nameOrig": account, "nameDest": list(outgoing[account])[0]}, "intermediary"))
    return results


# ============================================================
# 规则 18: 金额聚类
# ============================================================
def amount_clustering(txns: List[dict], cfg=None) -> List[dict]:
    """多笔交易金额高度相似（规避阈值检测的结构化交易）"""
    if cfg is None:
        cfg = CFG["amount_clustering"]
    if not cfg.get("enabled"):
        return []

    by_account = defaultdict(list)
    for t in txns:
        if t.get("amount", 0) > 0:
            by_account[t.get("nameOrig", "")].append(t)

    results = []
    for name, group in by_account.items():
        if len(group) < cfg["min_cluster_size"]:
            continue
        amounts = sorted(t.get("amount", 0) for t in group)
        # 滑窗检测集中金额区间
        for i in range(len(amounts) - cfg["min_cluster_size"] + 1):
            a0 = amounts[i]
            if a0 == 0:
                continue
            cluster = [a for a in amounts[i:i+cfg["min_cluster_size"]]
                       if abs(a - a0) / a0 <= cfg["tolerance"]]
            if len(cluster) >= cfg["min_cluster_size"]:
                results.append(_hits(
                    [f"金额聚类: {name} {len(cluster)}笔金额≈{a0:.0f} (±{cfg['tolerance']:.0%})"],
                    cfg["risk_score"], group[0], "amount_clustering"))
                break
    return results


# ============================================================
# 规则 19: 交易量突变
# ============================================================
def volume_spike(txns: List[dict], cfg=None) -> List[dict]:
    """交易量突然暴增（可能是洗钱活动启动信号）"""
    if cfg is None:
        cfg = CFG["volume_spike"]
    if not cfg.get("enabled"):
        return []

    by_account = defaultdict(list)
    for t in txns:
        by_account[t.get("nameOrig", "")].append(float(t.get("step", 0)))

    results = []
    window_hours = cfg["window_hours"]
    for name, steps in by_account.items():
        if len(steps) < cfg["min_baseline_txns"] + cfg["min_spike_txns"]:
            continue
        steps_sorted = sorted(steps)
        mid = len(steps_sorted) // 2
        baseline_rate = mid / max(steps_sorted[mid - 1] - steps_sorted[0], 1)
        recent = [s for s in steps_sorted if s >= steps_sorted[-1] - window_hours]
        if len(recent) >= cfg["min_spike_txns"]:
            recent_rate = len(recent) / window_hours
            if baseline_rate > 0 and recent_rate / baseline_rate >= cfg["spike_factor"]:
                results.append(_hits(
                    [f"交易量突变: {name} 近{window_hours}h {len(recent)}笔 "
                     f"(x{recent_rate/baseline_rate:.1f}基准)"],
                    cfg["risk_score"], {"nameOrig": name}, "volume_spike"))
    return results


# ============================================================
# 规则 20: 账户异常结构
# ============================================================
def structuring(txns: List[dict], cfg=None) -> List[dict]:
    """多笔小额交易合起来接近/超过大额报告阈值（规避报告）"""
    if cfg is None:
        cfg = CFG["structuring"]
    if not cfg.get("enabled"):
        return []

    by_account = defaultdict(list)
    for t in txns:
        if t.get("amount", 0) < cfg["threshold"]:
            by_account[t.get("nameOrig", "")].append(t)

    results = []
    for name, group in by_account.items():
        if len(group) < cfg["min_txns"]:
            continue
        # PaySim step=小时, 约24内的小额汇总
        window_steps = 24
        group_sorted = sorted(group, key=lambda x: float(x.get("step", 0)))
        for i in range(len(group_sorted)):
            window = []
            for j in range(i, len(group_sorted)):
                if float(group_sorted[j].get("step", 0)) - float(group_sorted[i].get("step", 0)) <= window_steps:
                    window.append(group_sorted[j])
            if len(window) >= cfg["min_txns"]:
                total = sum(t.get("amount", 0) for t in window)
                if total >= cfg["max_daily"]:
                    results.append(_hits(
                        [f"结构化交易: {name} {len(window)}笔小额汇总{total:.0f} "
                         f"(超{cfg['max_daily']:.0f}阈值, 规避{cfg['threshold']:.0f}报告线)"],
                        cfg["risk_score"], window[0], "structuring"))
                    break
    return results


# ============================================================
# 规则注册表
# ============================================================
ALL_RULES = [
    ("smurfing", smurfing, "分拆转账"),
    ("fast_in_fast_out", fast_in_fast_out, "快进快出"),
    ("round_trip", round_trip, "对敲交易"),
    ("large_amount", large_amount, "大额交易"),
    ("baseline_deviation", baseline_deviation, "基线偏离"),
    ("remark_keywords", remark_keywords, "备注关键词"),
    ("shell_company", shell_company, "空壳公司"),
    ("sanction_list", sanction_list, "制裁名单"),
    ("cross_border", cross_border, "跨境交易"),
    ("crypto_pattern", crypto_pattern, "虚拟货币"),
    ("circular_flow", circular_flow, "循环转账"),
    ("round_amount", round_amount, "整数金额"),
    ("high_frequency", high_frequency, "高频交易"),
    ("balance_drain", balance_drain, "余额清空"),
    ("type_jump", type_jump, "交易类型跳跃"),
    ("night_activity", night_activity, "夜间异常"),
    ("intermediary", intermediary, "中介账户"),
    ("amount_clustering", amount_clustering, "金额聚类"),
    ("volume_spike", volume_spike, "交易量突变"),
    ("structuring", structuring, "结构化交易"),
]

CORE_RULES = ["smurfing", "fast_in_fast_out", "round_trip", "large_amount",
              "circular_flow", "balance_drain", "structuring", "night_activity"]
