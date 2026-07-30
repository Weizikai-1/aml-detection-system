"""
Agent 2: 规则引擎 Agent

职责: 基于预设规则初筛可疑交易，高召回低精度
模式: create_rule_engine_agent(llm) -> node_function

4条核心规则:
1. 分拆转账 (Smurfing): 同一收款账户1小时内收到≥5笔来自不同付款方、金额在4万-5万之间的转账
2. 快进快出 (Fast-In-Fast-Out): 资金进入账户后10分钟内≥95%金额转出
3. 对敲交易 (Round-Trip): 两个账户7天内互相转账，金额差异≤20%
4. 大额交易 (Large Amount): 单笔交易≥10万元
"""
import time
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple
from graph.state import AMLState, SuspiciousTransaction, Transaction
from config import AML_CONFIG


def _parse_ts(ts_str: str):
    """解析时间戳"""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def _make_suspicious(txn: Transaction, rule_name: str, evidence: str, risk_score: int = 50, structured_evidence: list = None) -> SuspiciousTransaction:
    """构造可疑交易对象"""
    return {
        "transaction": txn,
        "rule_hits": [rule_name],
        "risk_score": risk_score,
        "evidence": [evidence],
        "structured_evidence": structured_evidence if structured_evidence is not None else [],
        "graph_evidence": None,
        "llm_analysis": None,
        "llm_confidence": None,
        "is_false_positive": None,
        "community_id": None,
    }


# ============================================================
# 规则 1: 分拆转账 (Smurfing)
# ============================================================
def _detect_smurfing(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    """
    检测分拆转账: 同一收款账户1小时内收到≥5笔来自不同付款方、金额在4万-5万之间的转账

    算法: 滑动窗口
    - 按收款账户分组
    - 对每个账户的入账交易按时间排序
    - 滑动窗口(1小时)内检查: 付款方去重数 ≥ 阈值 且 每笔金额在指定区间
    """
    window_hours = AML_CONFIG["rules"]["smurfing"]["hour_window"]
    min_count = AML_CONFIG["rules"]["smurfing"]["min_count"]
    amount_low = AML_CONFIG["rules"]["smurfing"]["amount_low"]
    amount_high = AML_CONFIG["rules"]["smurfing"]["amount_high"]
    risk_score = AML_CONFIG["rules"]["smurfing"]["risk_score"]

    # 按收款账户分组的入账交易
    incoming_by_account: Dict[str, List[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.get("amount", 0) < amount_low or txn.get("amount", 0) > amount_high:
            continue
        # 戒律 M1: 不编造数据，缺失 to_account 字段跳过
        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        if not to_acc:
            continue
        # 戒律 P2: 自转账不构成分拆（A→A 不是真正的分拆转账）
        if from_acc == to_acc:
            continue
        incoming_by_account[to_acc].append(txn)

    suspicious = []
    for account, txns in incoming_by_account.items():
        if len(txns) < min_count:
            continue

        # 按时间正序排序
        txns_sorted = sorted(txns, key=lambda x: x.get("timestamp", ""))

        # 滑动窗口
        left = 0
        matched_groups = set()  # 用交易ID集合去重，避免同一笔被标记多次
        for right in range(len(txns_sorted)):
            right_ts = _parse_ts(txns_sorted[right].get("timestamp"))
            if right_ts is None:
                continue

            # 维护窗口左边界
            while left <= right:
                left_ts = _parse_ts(txns_sorted[left].get("timestamp"))
                if left_ts is None or (right_ts - left_ts) > timedelta(hours=window_hours):
                    left += 1
                else:
                    break

            window_txns = txns_sorted[left: right + 1]
            unique_payers = set(t["from_account"] for t in window_txns)

            if len(unique_payers) >= min_count and len(window_txns) >= min_count:
                window_amounts = [t.get("amount", 0) for t in window_txns]
                min_amount = min(window_amounts) if window_amounts else 0
                max_amount = max(window_amounts) if window_amounts else 0
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
                            "amount_range": [min_amount, max_amount],
                            "config": {
                                "hour_window": window_hours,
                                "min_count": min_count,
                                "amount_low": amount_low,
                                "amount_high": amount_high,
                            }
                        }
                    }]
                    suspicious.append(_make_suspicious(t, "分拆转账", evidence, risk_score=risk_score, structured_evidence=structured_evidence))

    return suspicious


# ============================================================
# 规则 2: 快进快出 (Fast-In-Fast-Out)
# ============================================================
def _detect_fast_in_fast_out(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    """
    检测快进快出: 资金进入账户后10分钟内≥95%金额转出

    算法: 双指针 + 账户余额模拟
    - 按账户分组，时间正序
    - 对每笔入账，检查后续10分钟内的出账总额是否≥入账金额*比例
    """
    max_minutes = AML_CONFIG["rules"]["fast_in_fast_out"]["max_minutes"]
    min_ratio = AML_CONFIG["rules"]["fast_in_fast_out"]["min_ratio"]
    min_amount = AML_CONFIG["rules"]["fast_in_fast_out"]["min_amount"]
    score_primary = AML_CONFIG["rules"]["fast_in_fast_out"]["risk_score_primary"]
    score_secondary = AML_CONFIG["rules"]["fast_in_fast_out"]["risk_score_secondary"]

    # 按账户分组所有交易
    account_txns: Dict[str, List[Transaction]] = defaultdict(list)
    for txn in transactions:
        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        # 戒律 M1: 缺失账户字段跳过，不编造
        if not from_acc or not to_acc:
            continue
        # 戒律 P2: 自转账不构成快进快出
        if from_acc == to_acc:
            continue
        account_txns[from_acc].append(txn)
        account_txns[to_acc].append(txn)

    suspicious = []
    # 按 (transaction_id, role) 去重，允许同一笔交易以不同角色出现
    # 由后续 _merge_suspicious 取最高 risk_score
    seen_pairs = set()

    for account, txns in account_txns.items():
        txns_sorted = sorted(txns, key=lambda x: x.get("timestamp", ""))

        for i, txn in enumerate(txns_sorted):
            # 只看入账交易
            if txn["to_account"] != account:
                continue
            if txn.get("amount", 0) < min_amount:
                continue

            in_ts = _parse_ts(txn.get("timestamp"))
            if in_ts is None:
                continue

            in_amount = txn.get("amount", 0)
            # 戒律 M4: 显式判断 in_amount <= 0，避免除零和不依赖配置
            if in_amount <= 0:
                continue
            out_amount = 0.0
            out_txns = []

            # 向前扫描10分钟内的出账
            for j in range(i + 1, len(txns_sorted)):
                t2 = txns_sorted[j]
                # 只看该账户的出账
                if t2["from_account"] != account:
                    continue

                out_ts = _parse_ts(t2.get("timestamp"))
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
                            "account": account,
                            "in_amount": in_amount,
                            "out_amount": out_amount,
                            "ratio": out_amount / in_amount,
                            "max_minutes": max_minutes,
                            "min_ratio": min_ratio,
                        }
                    }]
                    suspicious.append(_make_suspicious(txn, "快进快出", evidence, risk_score=score_primary, structured_evidence=structured_evidence))

                # 同时标记关联的出账交易
                for ot in out_txns:
                    otid = ot.get("transaction_id")
                    role_out = "out"
                    if (otid, role_out) not in seen_pairs:
                        seen_pairs.add((otid, role_out))
                        evidence2 = (
                            f"快进快出(关联出账): 账户[{account}] 该笔出账与前序入账构成快进快出模式"
                        )
                        structured_evidence2 = [{
                            "rule": "快进快出",
                            "details": {
                                "account": account,
                                "in_amount": in_amount,
                                "out_amount": out_amount,
                                "ratio": out_amount / in_amount,
                                "max_minutes": max_minutes,
                                "min_ratio": min_ratio,
                            }
                        }]
                        suspicious.append(_make_suspicious(ot, "快进快出", evidence2, risk_score=score_secondary, structured_evidence=structured_evidence2))

    return suspicious


# ============================================================
# 规则 3: 对敲交易 (Round-Trip)
# ============================================================
def _detect_round_trip(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    """
    检测对敲交易: 两个账户7天内互相转账，金额差异≤20%

    算法: 双向交易对匹配
    - 按账户对(A,B)分组双向交易
    - 检查是否存在A→B和B→A的交易，且金额差异在阈值内
    """
    max_days = AML_CONFIG["rules"]["round_trip"]["max_days"]
    max_amount_diff_ratio = AML_CONFIG["rules"]["round_trip"]["max_amount_diff_ratio"]
    min_amount = AML_CONFIG["rules"]["round_trip"]["min_amount"]
    risk_score = AML_CONFIG["rules"]["round_trip"]["risk_score"]

    # 构建账户对的双向交易: key=(小账户, 大账户), value=[(from, to, amount, ts, txn)]
    pair_txns: Dict[Tuple[str, str], List[Tuple[str, str, float, datetime, Transaction]]] = defaultdict(list)

    for txn in transactions:
        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        # 戒律 M1: 缺失账户字段跳过，不编造
        if not from_acc or not to_acc:
            continue
        amount = txn.get("amount", 0)
        ts = _parse_ts(txn.get("timestamp"))
        if ts is None or amount < min_amount:
            continue
        # 戒律 P2: 自转账不参与对敲检测（A→A 不是对敲）
        if from_acc == to_acc:
            continue

        pair_key = tuple(sorted([from_acc, to_acc]))
        pair_txns[pair_key].append((from_acc, to_acc, amount, ts, txn))

    suspicious = []
    matched_ids = set()

    for pair, txns in pair_txns.items():
        if len(txns) < 2:
            continue

        # 按时间排序，便于时间窗口剪枝
        txns.sort(key=lambda x: x[3])

        # 对每笔交易寻找反向匹配
        for i, (from_i, to_i, amt_i, ts_i, txn_i) in enumerate(txns):
            # 如果这笔已匹配，跳过
            if txn_i.get("transaction_id") in matched_ids:
                continue

            for j in range(i + 1, len(txns)):
                from_j, to_j, amt_j, ts_j, txn_j = txns[j]

                # 时间剪枝: txns已按时间排序，超过max_days直接break
                time_diff = (ts_j - ts_i).days
                if time_diff > max_days:
                    break
                time_diff = abs(time_diff)

                # 戒律 P2: 必须方向相反才构成对敲（A→B 和 B→A）
                # 同向转账（A→B 和 A→B）不是对敲
                if from_i == from_j or to_i == to_j:
                    continue
                if not (from_i == to_j and to_i == from_j):
                    continue

                # 金额差异在阈值内
                max_amt = max(amt_i, amt_j)
                min_amt = min(amt_i, amt_j)
                if max_amt == 0:
                    continue
                diff_ratio = (max_amt - min_amt) / max_amt
                if diff_ratio > max_amount_diff_ratio:
                    continue

                # 匹配成功
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
                            "pair": list(pair),
                            "amounts": [amt_i, amt_j],
                            "diff_ratio": diff_ratio,
                            "time_diff_days": time_diff,
                            "max_days": max_days,
                            "max_amount_diff_ratio": max_amount_diff_ratio,
                        }
                    }]
                    suspicious.append(_make_suspicious(txn_i, "对敲交易", evidence, risk_score=risk_score, structured_evidence=structured_evidence))

                if tid_j not in matched_ids:
                    matched_ids.add(tid_j)
                    evidence2 = (
                        f"对敲交易: {pair[0]} ↔ {pair[1]} 互相转账，"
                        f"金额 {amt_j:,.2f} vs {amt_i:,.2f} (差异 {diff_ratio * 100:.1f}%)"
                    )
                    structured_evidence2 = [{
                        "rule": "对敲交易",
                        "details": {
                            "pair": list(pair),
                            "amounts": [amt_i, amt_j],
                            "diff_ratio": diff_ratio,
                            "time_diff_days": time_diff,
                            "max_days": max_days,
                            "max_amount_diff_ratio": max_amount_diff_ratio,
                        }
                    }]
                    suspicious.append(_make_suspicious(txn_j, "对敲交易", evidence2, risk_score=risk_score, structured_evidence=structured_evidence2))

    return suspicious


# ============================================================
# 规则 4: 大额交易 (Large Amount)
# ============================================================
def _detect_large_amount(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    """
    检测大额交易: 单笔交易≥10万元

    算法: 线性扫描
    """
    threshold = AML_CONFIG["rules"]["large_amount"]["threshold"]
    risk_score = AML_CONFIG["rules"]["large_amount"]["risk_score"]

    suspicious = []
    for txn in transactions:
        amount = txn.get("amount", 0)
        if amount >= threshold:
            # 戒律 M2: 自转账需在证据中显式标注
            from_acc = txn.get("from_account")
            to_acc = txn.get("to_account")
            self_transfer_tag = "（自转账）" if from_acc and to_acc and from_acc == to_acc else ""
            evidence = f"大额交易{self_transfer_tag}: 单笔金额 {amount:,.2f} 元 ≥ 阈值 {threshold:,.2f} 元"
            structured_evidence = [{
                "rule": "大额交易",
                "details": {
                    "amount": amount,
                    "threshold": threshold,
                    "is_self_transfer": from_acc == to_acc if from_acc and to_acc else False,
                }
            }]
            suspicious.append(_make_suspicious(txn, "大额交易", evidence, risk_score=risk_score, structured_evidence=structured_evidence))

    return suspicious


# ============================================================
# 规则 5: 基线偏离检测 (Baseline Deviation)
# ============================================================
def _detect_baseline_deviation(
    transactions: List[Transaction],
    account_baselines: dict,
) -> List[SuspiciousTransaction]:
    """
    检测账户行为基线偏离交易

    检测维度:
    1. 金额偏离: 单笔金额显著高于账户历史均值 (Z-score > 阈值)
    2. 时段偏离: 非活跃时段的大额交易额外加权
    3. 对手偏离: 与陌生交易对手的大额交易额外加权

    注意: 严格遵守戒律 M1（使用真实数据）— 所有偏离判断均基于该账户真实历史交易
         严格遵守戒律 P2（禁止误报）— 只有显著偏离才标记，正常波动不触发
    """
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
        # 戒律 P2: 自转账不参与基线偏离检测
        if from_acc and to_acc and from_acc == to_acc:
            continue

        # 分别检查付款方和收款方的基线偏离
        for acc, role in [(from_acc, "付款方"), (to_acc, "收款方")]:
            baseline = account_baselines.get(acc)
            if not baseline:
                continue

            # 交易笔数不足，基线不可靠，跳过（戒律 P2：不轻易误报）
            if baseline.get("total_txns", 0) < min_txns:
                continue

            avg_amt = baseline.get("avg_amount", 0)
            std_amt = baseline.get("std_amount", 0)

            if avg_amt <= 0 or std_amt <= 0:
                continue

            # 计算 Z-score
            z_score = abs(amount - avg_amt) / std_amt

            # 只有显著偏离才触发（戒律 P2：禁止误报）
            if z_score < z_threshold:
                continue

            # 基础风险分：Z-score 越高分越高，但不超过 max_score
            base_score = min(z_score * 10, max_score)
            score = base_score

            deviation_reasons = [f"金额Z-score={z_score:.2f}（均值{avg_amt:,.0f}元，标准差{std_amt:,.0f}元）"]

            # 夜间交易额外加权（如果该账户夜间交易比例本来就低）
            is_night = txn.get("is_night", False)
            if night_boost and is_night and baseline.get("night_transaction_ratio", 0) < 0.1:
                score = min(score * 1.3, max_score)
                deviation_reasons.append(f"非活跃时段交易（该账户夜间交易占比仅{baseline['night_transaction_ratio']:.1%}）")

            # 陌生交易对手加成
            counterparty = to_acc if role == "付款方" else from_acc
            top_cps = baseline.get("top_counterparties", [])
            cp_count = baseline.get("counterparty_count", 0)
            if counterparty not in top_cps and cp_count >= 3:
                score = min(score * (1 + new_cp_weight), max_score)
                deviation_reasons.append("陌生交易对手")

            # 构造证据
            evidence = (
                f"基线偏离[{role}]: 账户{acc}单笔{amount:,.0f}元，"
                + "；".join(deviation_reasons)
                + f"，偏离风险评分{round(score)}分"
            )

            structured_evidence = [{
                "rule": "基线偏离",
                "details": {
                    "account": acc,
                    "role": role,
                    "amount": amount,
                    "avg_amount": avg_amt,
                    "std_amount": std_amt,
                    "z_score": z_score,
                    "deviation_reasons": deviation_reasons,
                }
            }]

            suspicious.append(_make_suspicious(
                txn, "基线偏离", evidence, risk_score=round(score),
                structured_evidence=structured_evidence,
            ))

    return suspicious


# ============================================================
# 规则 6: 备注语义关键词检测
# ============================================================
def _detect_remark_keywords(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    """
    基于交易备注的关键词检测

    高风险关键词命中 → 标记可疑并加分
    低风险关键词命中 → 正常业务特征（用于LLM层降误报）

    严格遵守戒律 P2: 不误报 - 只有明确的高风险词汇才触发
    严格遵守戒律 M1: 基于真实备注数据，不臆测
    """
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
        # 戒律 P2: 自转账不参与备注关键词检测
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
                "details": {
                    "matched_words": matched_words,
                }
            }]
            suspicious.append(_make_suspicious(
                txn, "备注关键词", evidence, risk_score=risk_score,
                structured_evidence=structured_evidence,
            ))

    return suspicious


# ============================================================
# 合并 & 去重
# ============================================================
def _merge_suspicious(all_suspicious: List[List[SuspiciousTransaction]]) -> List[SuspiciousTransaction]:
    """
    合并多规则命中结果，按交易ID去重，合并命中规则和证据
    """
    merged: Dict[str, SuspiciousTransaction] = {}

    for rule_results in all_suspicious:
        for s in rule_results:
            tid = s["transaction"].get("transaction_id", "")
            if tid in merged:
                # 合并规则命中
                existing = merged[tid]
                for r in s["rule_hits"]:
                    if r not in existing["rule_hits"]:
                        existing["rule_hits"].append(r)
                # 合并证据
                for e in s["evidence"]:
                    if e not in existing["evidence"]:
                        existing["evidence"].append(e)
                # 风险评分取最高
                existing["risk_score"] = max(existing["risk_score"], s["risk_score"])
            else:
                merged[tid] = s

    # 按风险评分降序
    result = list(merged.values())
    result.sort(key=lambda x: x["risk_score"], reverse=True)
    return result


# ============================================================
# 规则 7: 空壳公司识别（账户级规则）
# ============================================================
def _detect_shell_companies(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    """
    空壳公司识别（账户级规则）

    特征维度:
    1. 交易对手分散度高 - 与很多不同账户交易
    2. 资金留存率低 - 入账后很快转出，账户不留钱
    3. 夜间交易占比高 - 非正常工作时间交易多
    4. 快进快出交易占比高 - 资金快进快出特征明显

    戒律:
    - P1 不遗漏: 多维度加权，典型空壳逃不掉
    - P2 不误报: 至少满足 3 个维度才触发，不是单指标判定
    - M1 真实数据: 全部基于真实交易计算
    - P3 有证据: 每个维度得分都写入 evidence
    """
    cfg = AML_CONFIG["rules"]["shell_company"]
    if not cfg.get("enabled", False):
        return []

    min_txns = cfg.get("min_total_txns", 8)
    min_cps = cfg.get("min_counterparties", 5)
    max_retention = cfg.get("max_retention_rate", 0.15)
    night_threshold = cfg.get("night_ratio_threshold", 0.4)
    turnover_threshold = cfg.get("fast_turnover_ratio_threshold", 0.5)
    required_dims = cfg.get("required_dimensions", 3)
    risk_score = cfg.get("risk_score", 75)

    # 按账户分组
    account_txns: Dict[str, List[Transaction]] = {}
    for txn in transactions:
        from_a = txn.get("from_account", "")
        to_a = txn.get("to_account", "")
        # 戒律 P2: 自转账不参与空壳公司识别
        if from_a and to_a and from_a == to_a:
            continue
        if from_a:
            account_txns.setdefault(from_a, []).append(txn)
        if to_a:
            account_txns.setdefault(to_a, []).append(txn)

    suspicious = []

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

        # 维度 2: 资金留存率（入账 vs 出账差额 / 总入账）
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

        # 维度 4: 快进快出特征（粗略判断：同一账户既有大额入账又有大额出账）
        in_txns = [t for t in txns if t.get("to_account") == account]
        out_txns = [t for t in txns if t.get("from_account") == account]
        # 戒律 M1: 阈值取自大额交易规则（保持配置一致），避免硬编码
        big_threshold = AML_CONFIG["rules"]["large_amount"]["threshold"]
        has_big_in = any(t.get("amount", 0) >= big_threshold for t in in_txns)
        has_big_out = any(t.get("amount", 0) >= big_threshold for t in out_txns)
        dim_turnover = has_big_in and has_big_out and len(in_txns) > 0 and len(out_txns) > 0

        # 统计满足的维度数
        dims_met = sum([dim_diverse, dim_low_retention, dim_night, dim_turnover])
        if dims_met < required_dims:
            continue

        # 构建证据
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

        # 把该账户的所有交易都标记为可疑（但去重由 _merge_suspicious 处理）
        structured_evidence = [{
            "rule": "空壳公司",
            "details": {
                "account": account,
                "dimensions_met": dims_met,
                "counterparty_count": cp_count,
                "retention_rate": retention,
                "night_ratio": night_ratio,
                "has_turnover": dim_turnover,
            }
        }]
        for txn in txns:
            # 只标记该账户作为主要角色的交易（转出或转入）
            suspicious.append(_make_suspicious(
                txn, "空壳公司", evidence, risk_score=risk_score,
                structured_evidence=structured_evidence,
            ))

    return suspicious


# ============================================================
# 规则 8: 制裁名单/黑名单检测 (Sanction List Check) — B0-1
# ============================================================
def _detect_sanction_list(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    """
    检测制裁名单/黑名单命中（B0-1: 合规必需）

    对每笔交易的付款方、收款方进行检查:
    - OFAC SDN制裁名单匹配
    - 人民银行反洗钱关注名单匹配
    - 用户自定义黑名单匹配
    - 制裁国家/地区匹配
    - 虚拟货币制裁地址匹配

    戒律:
    - M1: 名单数据基于真实公开数据
    - M2: 命中即标注理由和名单来源
    - M4: 证据链完整
    - P1: 名单命中不遗漏，风险分≥90
    """
    try:
        from tools.sanction_checker import sanction_checker
    except ImportError:
        return []

    hits = sanction_checker.check_transactions(transactions)

    suspicious = []
    for hit in hits:
        txn = hit["transaction"]
        match_type = hit["match_type"]
        entity = hit["entity"]
        risk_score = hit["risk_score"]
        evidence = hit["evidence"]
        matched_field = hit["matched_field"]

        # 戒律 M2: 标注具体理由和名单来源
        rule_name = "制裁名单"
        if match_type == "ofac_sdn":
            rule_name = "制裁名单(OFAC SDN)"
        elif match_type == "pboc_watchlist":
            rule_name = "制裁名单(央行关注)"
        elif match_type == "custom_blacklist":
            rule_name = "制裁名单(自定义黑名单)"
        elif match_type == "sanctioned_country":
            rule_name = "制裁名单(制裁国家)"
        elif match_type == "crypto_sanction":
            rule_name = "制裁名单(虚拟货币)"

        suspicious.append(_make_suspicious(
            txn,
            rule_name,
            evidence,
            risk_score=risk_score,
            structured_evidence=[{
                "rule": rule_name,
                "details": {
                    "match_type": match_type,
                    "entity": entity,
                    "matched_field": matched_field,
                }
            }],
        ))

    return suspicious


# ============================================================
# 规则 9: 跨境交易检测 (Cross-Border Transaction) — B0-2
# ============================================================
def _detect_cross_border(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    """
    检测跨境洗钱交易（B0-2: 跨境交易专项检测）

    检测模式:
    1. 频繁跨境汇款: 单账户短期内多次跨境转账
    2. 跨境分拆: 大额资金拆分为多笔小额跨境转账
    3. 换汇可疑: 交易类型为换汇(fx/currency_exchange)且金额大
    4. 高风险地区跨境: 与高风险地区(非OFAC制裁但关注)的跨境交易

    戒律:
    - M1: 基于交易数据中的currency/country/transaction_type字段
    - M2: 标注跨境可疑理由
    - M4: 证据链完整
    - P1: 不遗漏跨境高风险交易
    - P2: 正常跨境贸易不误报（需同时满足金额和频率条件）
    """
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

    # 高风险地区代码（非OFAC全面制裁但需重点关注）
    high_risk_regions = cfg.get("high_risk_regions", [
        "AE", "SG", "HK", "MO",  # 阿联酋、新加坡、香港、澳门（转口贸易关注）
        "PA", "KY", "VG", "BVI",  # 巴拿马、开曼、维京群岛（避税天堂）
    ])

    suspicious = []

    # 按账户分组跨境交易
    cross_border_by_account: Dict[str, List[Transaction]] = defaultdict(list)
    fx_by_account: Dict[str, List[Transaction]] = defaultdict(list)

    for txn in transactions:
        txn_type = str(txn.get("transaction_type", "")).lower()
        currency = str(txn.get("currency", "CNY")).upper()
        counterparty_country = str(txn.get("counterparty_country", "")).upper()

        # 判断是否跨境: 非CNY货币 或 有交易对手国家信息 或 交易类型标记跨境
        is_cross_border = (
            currency not in ("CNY", "RMB", "") or
            (counterparty_country and counterparty_country != "CN") or
            txn_type in ("cross_border", "international", "remittance")
        )
        # 判断是否换汇
        is_fx = txn_type in ("fx", "currency_exchange", "foreign_exchange", "换汇")

        if not is_cross_border and not is_fx:
            continue

        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        # 戒律 M1: 缺失账户字段跳过
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

        # 检查时间窗口
        txns_sorted = sorted(txns, key=lambda x: x.get("timestamp", ""))
        window_start = None
        window_count = 0

        for txn in txns_sorted:
            ts = _parse_ts(txn.get("timestamp"))
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
                # 戒律 M2: 标注可疑理由
                total_amount = sum(t.get("amount", 0) for t in txns_sorted)
                for t in txns_sorted:
                    evidence = (
                        f"频繁跨境交易: 账户[{account}]在{frequent_days}天内发生"
                        f"{window_count}笔跨境交易，"
                        f"总金额{total_amount:,.2f}元，超过频率阈值{frequent_count}笔"
                    )
                    structured_evidence = [{
                        "rule": "跨境频繁交易",
                        "details": {
                            "account": account,
                            "window_count": window_count,
                            "frequent_count": frequent_count,
                            "frequent_days": frequent_days,
                            "total_amount": total_amount,
                        }
                    }]
                    suspicious.append(_make_suspicious(
                        t, "跨境频繁交易", evidence, risk_score=risk_score,
                        structured_evidence=structured_evidence,
                    ))
                break

    # 检测2: 跨境分拆
    for account, txns in cross_border_by_account.items():
        # 检查是否有大额资金拆分为多笔小额跨境
        total_cross_border = sum(t.get("amount", 0) for t in txns)
        if total_cross_border < split_threshold:
            continue
        if len(txns) < split_count:
            continue

        # 检查金额是否均匀分布（分拆特征）
        amounts = [t.get("amount", 0) for t in txns]
        if amounts:
            avg_amount = sum(amounts) / len(amounts)
            if avg_amount > 0:
                # 计算变异系数，分拆交易金额差异较小
                variance = sum((a - avg_amount) ** 2 for a in amounts) / len(amounts)
                cv = (variance ** 0.5) / avg_amount if avg_amount > 0 else 1.0
                if cv < 0.3:  # 变异系数<0.3 说明金额分布集中
                    for t in txns:
                        evidence = (
                            f"跨境分拆: 账户[{account}]跨境交易{len(txns)}笔，"
                            f"总金额{total_cross_border:,.2f}元，"
                            f"单笔均额{avg_amount:,.2f}元(变异系数{cv:.2f})，"
                            f"疑似将大额资金拆分为多笔跨境转账"
                        )
                        structured_evidence = [{
                            "rule": "跨境分拆",
                            "details": {
                                "account": account,
                                "txn_count": len(txns),
                                "total_amount": total_cross_border,
                                "avg_amount": avg_amount,
                                "cv": cv,
                                "split_threshold": split_threshold,
                            }
                        }]
                        suspicious.append(_make_suspicious(
                            t, "跨境分拆", evidence, risk_score=high_risk_score,
                            structured_evidence=structured_evidence,
                        ))

    # 检测3: 大额换汇交易
    for account, txns in fx_by_account.items():
        for t in txns:
            amount = t.get("amount", 0)
            if amount < min_amount:
                continue
            evidence = (
                f"大额换汇: 账户[{account}]进行换汇交易，"
                f"金额{amount:,.2f}元，超过阈值{min_amount:,.2f}元"
            )
            structured_evidence = [{
                "rule": "跨境大额换汇",
                "details": {
                    "account": account,
                    "amount": amount,
                    "min_amount": min_amount,
                }
            }]
            suspicious.append(_make_suspicious(
                t, "跨境大额换汇", evidence, risk_score=risk_score,
                structured_evidence=structured_evidence,
            ))

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
        evidence = (
            f"高风险地区跨境交易: 账户[{from_acc}]与高风险地区"
            f"[{counterparty_country}]发生交易，"
            f"金额{amount:,.2f}元"
        )
        structured_evidence = [{
            "rule": "跨境高风险地区",
            "details": {
                "account": from_acc,
                "counterparty_country": counterparty_country,
                "amount": amount,
                "min_amount": min_amount,
            }
        }]
        suspicious.append(_make_suspicious(
            txn, "跨境高风险地区", evidence, risk_score=high_risk_score,
            structured_evidence=structured_evidence,
        ))

    # 检测5: 地理风险评分加成（B1-2: 集成 geo_risk_score）
    # 戒律 M1: 基于预处理阶段计算的 geo_risk_score（基于真实国家分级）
    # 戒律 M3: 最终风险分钳制 0-100
    # 戒律 P1: 地理风险分≥65 的交易应被纳入可疑
    for txn in transactions:
        geo_score = txn.get("geo_risk_score", 0)
        if not isinstance(geo_score, (int, float)) or geo_score < 65:
            continue
        geo_reasons = txn.get("geo_risk_reasons", [])
        # 已被前面规则命中的交易由 _merge_suspicious 合并去重，此处只补充新交易
        from_acc = txn.get("from_account", "")
        to_acc = txn.get("to_account", "")
        # 戒律 P2: 自转账不参与
        if from_acc and to_acc and from_acc == to_acc:
            continue
        amount = txn.get("amount", 0)
        reason_text = "；".join(geo_reasons) if geo_reasons else "地理风险评分异常"
        evidence = (
            f"地理风险加成: 交易地理风险评分{geo_score}分，"
            f"金额{amount:,.2f}元，理由: {reason_text}"
        )
        structured_evidence = [{
            "rule": "跨境地理风险",
            "details": {
                "geo_score": geo_score,
                "amount": amount,
                "geo_reasons": geo_reasons,
            }
        }]
        suspicious.append(_make_suspicious(
            txn, "跨境地理风险", evidence, risk_score=min(100, int(geo_score)),
            structured_evidence=structured_evidence,
        ))

    return suspicious


# ============================================================
# 规则 10: 虚拟货币交易检测 (Crypto Pattern) — B1-1
# ============================================================
def _detect_crypto_pattern(transactions: List[Transaction]) -> List[SuspiciousTransaction]:
    """
    检测虚拟货币相关可疑交易（B1-1: 虚拟货币专项监测）

    检测模式:
    1. 场外OTC模式: 多对一汇聚 → 中心账户 → 一对多分发（24小时内）
    2. 混币器特征: 多笔小额进账 + 单笔大额出账 + 时间高度集中
    3. 法币-虚拟货币兑换: 备注含"换U"/"收U"等 + 高频中额交易
    4. 已知平台关联: 备注含币安/火币等已知交易所关键词

    戒律遵守:
    - M1: 仅基于交易真实字段(amount/remark/timestamp/from/to)判断，不臆测链上数据
    - M2: evidence 明确说明触发的具体维度
    - M3: 风险分0-100，已知平台命中≥75
    - M4: 证据链保留汇聚笔数、金额明细
    - P1: OTC/混币器模式命中不遗漏
    - P2: 单纯法币交易不误报，需具备兑换模式特征或平台关键词
    """
    cfg = AML_CONFIG["rules"].get("crypto_pattern", {})
    if not cfg.get("enabled", True):
        return []

    # OTC模式参数
    otc_min_in = cfg.get("otc_hub_min_in", 3)
    otc_min_out = cfg.get("otc_hub_min_out", 3)
    otc_window = timedelta(hours=cfg.get("otc_window_hours", 24))
    otc_score = cfg.get("otc_risk_score", 80)
    # 混币器参数
    mixer_min_in = cfg.get("mixer_min_in_count", 5)
    mixer_max_in_amount = cfg.get("mixer_max_in_amount", 10000)
    mixer_min_out_amount = cfg.get("mixer_min_out_amount", 50000)
    mixer_window = timedelta(minutes=cfg.get("mixer_window_minutes", 30))
    mixer_score = cfg.get("mixer_risk_score", 85)
    # 法币兑换参数
    fx_keywords = [k.lower() for k in cfg.get("fx_keywords", [])]
    fx_min_count = cfg.get("fx_min_count", 3)
    fx_window = timedelta(hours=cfg.get("fx_window_hours", 24))
    fx_min_amount = cfg.get("fx_min_amount", 5000)
    fx_score = cfg.get("fx_risk_score", 70)
    # 平台关联参数
    platform_keywords = [k.lower() for k in cfg.get("known_platform_keywords", [])]
    platform_score = cfg.get("platform_risk_score", 75)

    suspicious = []

    # 按账户分组入账/出账
    incoming_by_acc: Dict[str, List[Transaction]] = defaultdict(list)
    outgoing_by_acc: Dict[str, List[Transaction]] = defaultdict(list)
    for txn in transactions:
        from_acc = txn.get("from_account")
        to_acc = txn.get("to_account")
        # 戒律 M1: 缺失账户字段跳过，不编造
        if not from_acc and not to_acc:
            continue
        # 戒律 P2: 自转账不构成OTC/混币模式
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

        # 检查时间窗口内是否同时存在多笔入账和多笔出账
        for in_txn in in_txns:
            in_ts = _parse_ts(in_txn.get("timestamp"))
            if in_ts is None:
                continue
            # 找出窗口内的入账和出账
            window_ins = []
            for t in in_txns:
                ts = _parse_ts(t.get("timestamp"))
                if ts and abs((ts - in_ts).total_seconds()) <= otc_window.total_seconds():
                    window_ins.append(t)
            window_outs = []
            for t in out_txns:
                ts = _parse_ts(t.get("timestamp"))
                if ts and abs((ts - in_ts).total_seconds()) <= otc_window.total_seconds():
                    window_outs.append(t)

            if len(window_ins) >= otc_min_in and len(window_outs) >= otc_min_out:
                # 戒律 P2: 需确认资金流向（汇聚后再分发），避免误报
                in_total = sum(t.get("amount", 0) for t in window_ins)
                out_total = sum(t.get("amount", 0) for t in window_outs)
                if in_total <= 0 or out_total <= 0:
                    continue
                # 资金流转比例: 出账应占入账一定比例（≥50%）才像OTC
                if out_total / in_total < 0.5:
                    continue

                # 多对一：付款方去重
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
                        "account": account,
                        "window_ins": len(window_ins),
                        "window_outs": len(window_outs),
                        "in_payers": len(in_payers),
                        "out_payees": len(out_payees),
                        "in_total": in_total,
                        "out_total": out_total,
                        "flow_ratio": out_total / in_total,
                    }
                }]
                # 标记参与汇聚-分发的所有交易
                for t in window_ins + window_outs:
                    suspicious.append(_make_suspicious(
                        t, "虚拟货币OTC", evidence, risk_score=otc_score,
                        structured_evidence=structured_evidence,
                    ))
                break  # 该账户已命中，避免重复

    # ========== 检测2: 混币器特征 ==========
    for account, in_txns in incoming_by_acc.items():
        if len(in_txns) < mixer_min_in:
            continue
        in_txns_sorted = sorted(in_txns, key=lambda x: x.get("timestamp", ""))

        # 滑动窗口查找混币器特征
        for i, anchor in enumerate(in_txns_sorted):
            anchor_ts = _parse_ts(anchor.get("timestamp"))
            if anchor_ts is None:
                continue
            # 收集窗口内的入账
            window_ins = []
            for t in in_txns_sorted[i:]:
                ts = _parse_ts(t.get("timestamp"))
                if ts is None:
                    continue
                if (ts - anchor_ts) > mixer_window:
                    break
                # 入账金额都应较小
                if t.get("amount", 0) > mixer_max_in_amount:
                    continue
                window_ins.append(t)

            if len(window_ins) < mixer_min_in:
                continue

            # 检查窗口内是否有大额出账
            out_txns = outgoing_by_acc.get(account, [])
            big_outs = []
            for t in out_txns:
                ts = _parse_ts(t.get("timestamp"))
                if ts is None:
                    continue
                # 出账应在入账窗口结束后的短时间内（混币器一次性出账）
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
                        "account": account,
                        "window_ins": len(window_ins),
                        "in_payers": len(in_payers),
                        "in_total": in_total,
                        "big_outs": len(big_outs),
                        "out_total": out_total,
                    }
                }]
                for t in window_ins + big_outs:
                    suspicious.append(_make_suspicious(
                        t, "虚拟货币混币器", evidence, risk_score=mixer_score,
                        structured_evidence=structured_evidence,
                    ))
                break

    # ========== 检测3: 法币-虚拟货币兑换（关键词 + 高频） ==========
    if fx_keywords:
        fx_hits_by_acc: Dict[str, List[Transaction]] = defaultdict(list)
        for txn in transactions:
            remark = str(txn.get("remark", "")).lower()
            if not remark:
                continue
            amount = txn.get("amount", 0)
            if amount < fx_min_amount:
                continue
            # 命中兑换关键词
            if any(k in remark for k in fx_keywords):
                from_acc = txn.get("from_account", "")
                to_acc = txn.get("to_account", "")
                for acc in [from_acc, to_acc]:
                    if acc:
                        fx_hits_by_acc[acc].append(txn)
                # 单笔命中不立即标记，先看是否高频（戒律 P2: 不误报）

        for account, txns in fx_hits_by_acc.items():
            if len(txns) < fx_min_count:
                continue
            # 检查时间窗口内是否有≥fx_min_count笔
            txns_sorted = sorted(txns, key=lambda x: x.get("timestamp", ""))
            for i, anchor in enumerate(txns_sorted):
                anchor_ts = _parse_ts(anchor.get("timestamp"))
                if anchor_ts is None:
                    continue
                window_txns = []
                for t in txns_sorted[i:]:
                    ts = _parse_ts(t.get("timestamp"))
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
                            "account": account,
                            "window_txns": len(window_txns),
                            "total_amount": total_amount,
                            "fx_min_count": fx_min_count,
                        }
                    }]
                    for t in window_txns:
                        suspicious.append(_make_suspicious(
                            t, "虚拟货币兑换", evidence, risk_score=fx_score,
                            structured_evidence=structured_evidence,
                        ))
                    break

    # ========== 检测4: 已知平台关联 ==========
    if platform_keywords:
        for txn in transactions:
            remark = str(txn.get("remark", "")).lower()
            if not remark:
                continue
            # 戒律 M1: 缺失账户字段跳过，不基于无主交易判定
            from_acc = txn.get("from_account")
            to_acc = txn.get("to_account")
            if not from_acc or not to_acc:
                continue
            # 戒律 P2: 自转账不参与平台关联检测
            if from_acc == to_acc:
                continue
            # 戒律 P2: 平台关键词命中需配合一定金额，避免误报小额查询
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
                    "details": {
                        "matched_keywords": matched,
                        "amount": amount,
                    }
                }]
                suspicious.append(_make_suspicious(
                    txn, "虚拟货币平台关联", evidence, risk_score=platform_score,
                    structured_evidence=structured_evidence,
                ))

    return suspicious


# ============================================================
# 备注降分：低风险关键词命中则适度降分
# ============================================================
def _apply_remark_discount(suspicious_list: List[SuspiciousTransaction]) -> List[SuspiciousTransaction]:
    """
    对命中低风险备注关键词的交易，适度降低风险分（戒律 P2：不误报）

    注意: 只是降分，不直接排除，防止犯罪分子用正常备注伪装（戒律 P1：不遗漏）
    """
    cfg = AML_CONFIG["rules"]["remark_keywords"]
    if not cfg.get("enabled", False):
        return suspicious_list

    low_risk_words = cfg.get("low_risk_keywords", [])
    discount = cfg.get("low_risk_discount", 0.6)

    for s in suspicious_list:
        txn = s["transaction"]
        remark = str(txn.get("remark", "")).strip()
        if not remark:
            continue

        matched_words = []
        for keyword in low_risk_words:
            if keyword.lower() in remark.lower():
                matched_words.append(keyword)

        if matched_words:
            original_score = s.get("risk_score", 50)
            if not isinstance(original_score, (int, float)):
                original_score = 50
            new_score = round(original_score * discount)
            # 最多降到 30 分，再低也没意义了；上限 100 保护（戒律 M3）
            new_score = max(min(new_score, 100), 30)
            s["risk_score"] = new_score
            s["evidence"].append(
                f"备注降分: 交易备注包含正常业务关键词[{', '.join(matched_words)}]，"
                f"风险分从{original_score}调整为{new_score}"
            )

    # 重新排序
    suspicious_list.sort(key=lambda x: x["risk_score"], reverse=True)
    return suspicious_list


# ============================================================
# 账户画像加权：累犯加成 / 清白降分
# ============================================================
def _apply_profile_weighting(
    suspicious_list: List[SuspiciousTransaction],
    profile_manager=None,
) -> Tuple[List[SuspiciousTransaction], int]:
    """
    根据账户历史风险画像调整风险分

    - 累犯账户（≥3次可疑）: ×1.15 加成（戒律 P1：不遗漏）
    - 高度累犯（≥6次）: ×1.3 加成
    - 历史清白且交易多: ×0.9 降分（戒律 P2：不误报）
    - 首次出现的账户: ×1.0（不调整）

    Args:
        suspicious_list: 可疑交易列表
        profile_manager: 账户画像管理器，为 None 时跳过

    Returns:
        (调整后的列表, 受影响的笔数)
    """
    if profile_manager is None:
        return suspicious_list, 0

    affected_count = 0
    for s in suspicious_list:
        txn = s["transaction"]
        from_acc = txn.get("from_account", "")
        to_acc = txn.get("to_account", "")

        # 收集两个账户的风险系数
        multipliers = []
        for acc in [from_acc, to_acc]:
            if not acc:
                continue
            profile = profile_manager.get_profile(acc)
            m = profile.get_risk_multiplier()
            multipliers.append((acc, m, profile))

        # 确定最终系数：
        # - 如果有累犯(m>1)，取最大的（戒律 P1：不遗漏）
        # - 如果全是清白(m<1)，取最小的（戒律 P2：不误报）
        # - 如果有新账户(m=1)和清白，取 1.0（不调整，保守）
        multiplier = 1.0
        reason = ""
        boost_candidates = [(acc, m, p) for acc, m, p in multipliers if m > 1.0]
        discount_candidates = [(acc, m, p) for acc, m, p in multipliers if m < 1.0]

        if boost_candidates:
            # 累犯优先（戒律 P1：不遗漏）
            acc, m, p = max(boost_candidates, key=lambda x: x[1])
            multiplier = m
            if m >= 1.3:
                reason = f"账户{acc}为高度累犯(历史{p.total_suspicious_hits}次可疑)，风险加成×{m}"
            else:
                reason = f"账户{acc}为累犯(历史{p.total_suspicious_hits}次可疑)，风险加成×{m}"
        elif discount_candidates:
            # 没有累犯但有清白账户，取降分最多的（戒律 P2：不误报）
            acc, m, p = min(discount_candidates, key=lambda x: x[1])
            multiplier = m
            reason = f"账户{acc}历史清白(交易{p.total_transactions}笔无可疑)，风险降分×{m}"

        if multiplier != 1.0 and reason:
            original = s.get("risk_score", 50)
            if not isinstance(original, (int, float)):
                original = 50
            new_score = round(original * multiplier)
            # 戒律 M3: 风险评分限制在 0-100 范围（下限改为 0，避免人为抬高）
            new_score = min(max(new_score, 0), 100)
            s["risk_score"] = new_score
            s["evidence"].append(
                f"画像加权: {reason}，风险分从{original}调整为{new_score}"
            )
            affected_count += 1

    suspicious_list.sort(key=lambda x: x["risk_score"], reverse=True)
    return suspicious_list, affected_count


def create_rule_engine_agent(llm=None):
    """
    创建规则引擎Agent

    Args:
        llm: LLM 实例(此Agent以规则为主，保留接口用于未来扩展)

    Returns:
        可直接传入 StateGraph.add_node 的节点函数
    """

    def rule_engine_node(state: AMLState) -> dict:
        """
        规则引擎节点函数

        依次执行7条规则，合并结果，输出可疑交易列表
        支持结果缓存（戒律 M1: 缓存真实结果，非编造）
        """
        start_time = time.time()
        print("\n" + "=" * 60)
        print("[Agent 2] 规则引擎 Agent 启动")
        print("=" * 60)

        cleaned = state.get("cleaned_transactions", [])
        print(f"  输入交易数: {len(cleaned)}")

        # 戒律 M1: 跳过 amount 为 None 的交易（缺失关键字段不参与规则分析）
        valid_cleaned = [t for t in cleaned if isinstance(t.get("amount"), (int, float))]
        skipped_none = len(cleaned) - len(valid_cleaned)
        if skipped_none > 0:
            print(f"  跳过 {skipped_none} 笔 amount 缺失的交易（戒律 M1）")
        cleaned = valid_cleaned

        if len(cleaned) == 0:
            print("[Agent 2] 无交易数据，跳过规则检测")
            return {
                "rule_hits": [],
                "rule_hit_count": 0,
                "rule_details": {},
                "rule_engine_stats": {"total_checked": 0},
                "current_step": "rule_engine",
            }

        # 执行10条规则
        print("  [规则 1/10] 分拆转账检测...")
        smurfing_results = _detect_smurfing(cleaned)
        print(f"    → 命中 {len(smurfing_results)} 笔")

        print("  [规则 2/10] 快进快出检测...")
        fifo_results = _detect_fast_in_fast_out(cleaned)
        print(f"    → 命中 {len(fifo_results)} 笔")

        print("  [规则 3/10] 对敲交易检测...")
        round_trip_results = _detect_round_trip(cleaned)
        print(f"    → 命中 {len(round_trip_results)} 笔")

        print("  [规则 4/10] 大额交易检测...")
        large_results = _detect_large_amount(cleaned)
        print(f"    → 命中 {len(large_results)} 笔")

        # 基线偏离检测（需要 account_baselines）
        baselines = state.get("account_baselines", {})
        if baselines:
            print("  [规则 5/10] 基线偏离检测...")
            baseline_results = _detect_baseline_deviation(cleaned, baselines)
            print(f"    → 命中 {len(baseline_results)} 笔")
        else:
            print("  [规则 5/10] 基线偏离检测（跳过，无基线数据）...")
            baseline_results = []

        # 备注关键词检测
        print("  [规则 6/10] 备注关键词检测...")
        remark_results = _detect_remark_keywords(cleaned)
        print(f"    → 命中 {len(remark_results)} 笔")

        # 空壳公司识别（账户级规则）
        print("  [规则 7/10] 空壳公司识别...")
        shell_results = _detect_shell_companies(cleaned)
        # 戒律 M1: 真实统计 - 涉及账户 = from_account ∪ to_account（之前只统计 from_account 会漏掉收款方）
        shell_accounts = set()
        for s in shell_results:
            t = s.get("transaction", {})
            for acc in [t.get("from_account", ""), t.get("to_account", "")]:
                if acc:
                    shell_accounts.add(acc)
        print(f"    → 命中 {len(shell_results)} 笔（涉及 {len(shell_accounts)} 个账户）")

        # 制裁名单/黑名单检测（B0-1: 合规必需）
        print("  [规则 8/10] 制裁名单/黑名单检测...")
        sanction_results = _detect_sanction_list(cleaned)
        print(f"    → 命中 {len(sanction_results)} 笔")

        # 跨境交易检测（B0-2: 跨境交易专项检测）
        print("  [规则 9/10] 跨境交易检测...")
        cross_border_results = _detect_cross_border(cleaned)
        print(f"    → 命中 {len(cross_border_results)} 笔")

        # 虚拟货币交易检测（B1-1: 虚拟货币专项监测）
        print("  [规则 10/10] 虚拟货币交易检测...")
        crypto_results = _detect_crypto_pattern(cleaned)
        print(f"    → 命中 {len(crypto_results)} 笔")

        # 合并去重
        all_hits = _merge_suspicious([
            smurfing_results,
            fifo_results,
            round_trip_results,
            large_results,
            baseline_results,
            remark_results,
            shell_results,
            sanction_results,
            cross_border_results,
            crypto_results,
        ])

        # 备注语义降分（戒律 P2：不误报）
        before_count = len(all_hits)
        all_hits = _apply_remark_discount(all_hits)
        remark_discount_count = sum(
            1 for s in all_hits if any("备注降分" in e for e in s.get("evidence", []))
        )

        # 账户画像加权：已移除（无真实画像数据，避免编造）
        profile_adjusted_count = 0

        # 各规则详情计数
        rule_details = {
            "分拆转账": len(smurfing_results),
            "快进快出": len(fifo_results),
            "对敲交易": len(round_trip_results),
            "大额交易": len(large_results),
            "基线偏离": len(baseline_results),
            "备注关键词": len(remark_results),
            "空壳公司": len(shell_results),
            "制裁名单": len(sanction_results),
            "跨境交易": len(cross_border_results),
            "虚拟货币": len(crypto_results),
        }

        elapsed = time.time() - start_time

        print(f"\n  {'─' * 50}")
        print(f"  规则检测汇总:")
        for rule, count in rule_details.items():
            print(f"    - {rule}: {count} 笔")
        print(f"  去重后可疑交易总数: {len(all_hits)} 笔")
        if remark_discount_count > 0:
            print(f"  备注降分调整: {remark_discount_count} 笔（命中正常业务关键词）")
        if profile_adjusted_count > 0:
            print(f"  画像加权调整: {profile_adjusted_count} 笔（累犯加成/清白降分）")
        print(f"  耗时: {elapsed:.2f} 秒")
        print("[Agent 2] 规则引擎检测完成")

        result = {
            "rule_hits": all_hits,
            "rule_hit_count": len(all_hits),
            "rule_details": rule_details,
            "rule_engine_stats": {
                "total_checked": len(cleaned),
                "total_hits_raw": sum(rule_details.values()),
                "total_hits_unique": len(all_hits),
                "remark_discount_count": remark_discount_count,
                "profile_adjusted_count": profile_adjusted_count,
                "cache_hit": False,
            },
            "current_step": "rule_engine",
            "step_times": {"rule_engine": elapsed},
        }

        return result

    return rule_engine_node
