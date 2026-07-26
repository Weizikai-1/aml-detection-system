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


def _make_suspicious(txn: Transaction, rule_name: str, evidence: str, risk_score: float = 0.5) -> SuspiciousTransaction:
    """构造可疑交易对象"""
    return {
        "transaction": txn,
        "rule_hits": [rule_name],
        "risk_score": risk_score,
        "evidence": [evidence],
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

    # 按收款账户分组的入账交易
    incoming_by_account: Dict[str, List[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.get("amount", 0) < amount_low or txn.get("amount", 0) > amount_high:
            continue
        incoming_by_account[txn["to_account"]].append(txn)

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
                    suspicious.append(_make_suspicious(t, "分拆转账", evidence, risk_score=0.7))

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

    # 按账户分组所有交易
    account_txns: Dict[str, List[Transaction]] = defaultdict(list)
    for txn in transactions:
        account_txns[txn["from_account"]].append(txn)
        account_txns[txn["to_account"]].append(txn)

    suspicious = []
    matched_ids = set()

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
                if tid not in matched_ids:
                    matched_ids.add(tid)
                    evidence = (
                        f"快进快出: 账户[{account}] {in_ts.strftime('%H:%M:%S')} 入账 {in_amount:,.2f} 元，"
                        f"{max_minutes}分钟内转出 {out_amount:,.2f} 元 "
                        f"(占比 {out_amount / in_amount * 100:.1f}% ≥ {min_ratio * 100:.0f}%)"
                    )
                    suspicious.append(_make_suspicious(txn, "快进快出", evidence, risk_score=0.6))

                # 同时标记关联的出账交易
                for ot in out_txns:
                    otid = ot.get("transaction_id")
                    if otid not in matched_ids:
                        matched_ids.add(otid)
                        evidence2 = (
                            f"快进快出(关联出账): 账户[{account}] 该笔出账与前序入账构成快进快出模式"
                        )
                        suspicious.append(_make_suspicious(ot, "快进快出", evidence2, risk_score=0.5))

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

    # 构建账户对的双向交易: key=(小账户, 大账户), value=[(from, to, amount, ts, txn)]
    pair_txns: Dict[Tuple[str, str], List[Tuple[str, str, float, datetime, Transaction]]] = defaultdict(list)

    for txn in transactions:
        from_acc = txn["from_account"]
        to_acc = txn["to_account"]
        amount = txn.get("amount", 0)
        ts = _parse_ts(txn.get("timestamp"))
        if ts is None or amount < min_amount:
            continue

        pair_key = tuple(sorted([from_acc, to_acc]))
        pair_txns[pair_key].append((from_acc, to_acc, amount, ts, txn))

    suspicious = []
    matched_ids = set()

    for pair, txns in pair_txns.items():
        if len(txns) < 2:
            continue

        # 对每笔交易寻找反向匹配
        for i, (from_i, to_i, amt_i, ts_i, txn_i) in enumerate(txns):
            for j in range(i + 1, len(txns)):
                from_j, to_j, amt_j, ts_j, txn_j = txns[j]

                # 必须是不同方向
                if from_i == from_j:
                    continue

                # 时间在max_days内
                time_diff = abs((ts_i - ts_j).days)
                if time_diff > max_days:
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
                    suspicious.append(_make_suspicious(txn_i, "对敲交易", evidence, risk_score=0.65))

                if tid_j not in matched_ids:
                    matched_ids.add(tid_j)
                    evidence2 = (
                        f"对敲交易(反向): {pair[0]} ↔ {pair[1]} 互相转账，"
                        f"金额 {amt_j:,.2f} vs {amt_i:,.2f} (差异 {diff_ratio * 100:.1f}%)"
                    )
                    suspicious.append(_make_suspicious(txn_j, "对敲交易", evidence2, risk_score=0.65))

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

    suspicious = []
    for txn in transactions:
        amount = txn.get("amount", 0)
        if amount >= threshold:
            evidence = f"大额交易: 单笔金额 {amount:,.2f} 元 ≥ 阈值 {threshold:,.2f} 元"
            suspicious.append(_make_suspicious(txn, "大额交易", evidence, risk_score=0.4))

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

        依次执行4条规则，合并结果，输出可疑交易列表
        """
        start_time = time.time()
        print("\n" + "=" * 60)
        print("[Agent 2] 规则引擎 Agent 启动")
        print("=" * 60)

        cleaned = state.get("cleaned_transactions", [])
        print(f"  输入交易数: {len(cleaned)}")

        if len(cleaned) == 0:
            print("[Agent 2] 无交易数据，跳过规则检测")
            return {
                "rule_hits": [],
                "rule_hit_count": 0,
                "rule_details": {},
                "rule_engine_stats": {"total_checked": 0},
                "current_step": "rule_engine",
            }

        # 执行4条规则
        print("  [规则 1/4] 分拆转账检测...")
        smurfing_results = _detect_smurfing(cleaned)
        print(f"    → 命中 {len(smurfing_results)} 笔")

        print("  [规则 2/4] 快进快出检测...")
        fifo_results = _detect_fast_in_fast_out(cleaned)
        print(f"    → 命中 {len(fifo_results)} 笔")

        print("  [规则 3/4] 对敲交易检测...")
        round_trip_results = _detect_round_trip(cleaned)
        print(f"    → 命中 {len(round_trip_results)} 笔")

        print("  [规则 4/4] 大额交易检测...")
        large_results = _detect_large_amount(cleaned)
        print(f"    → 命中 {len(large_results)} 笔")

        # 合并去重
        all_hits = _merge_suspicious([
            smurfing_results,
            fifo_results,
            round_trip_results,
            large_results,
        ])

        # 各规则详情计数
        rule_details = {
            "分拆转账": len(smurfing_results),
            "快进快出": len(fifo_results),
            "对敲交易": len(round_trip_results),
            "大额交易": len(large_results),
        }

        elapsed = time.time() - start_time

        print(f"\n  {'─' * 50}")
        print(f"  规则检测汇总:")
        for rule, count in rule_details.items():
            print(f"    - {rule}: {count} 笔")
        print(f"  去重后可疑交易总数: {len(all_hits)} 笔")
        print(f"  耗时: {elapsed:.2f} 秒")
        print("[Agent 2] 规则引擎检测完成")

        return {
            "rule_hits": all_hits,
            "rule_hit_count": len(all_hits),
            "rule_details": rule_details,
            "rule_engine_stats": {
                "total_checked": len(cleaned),
                "total_hits_raw": sum(rule_details.values()),
                "total_hits_unique": len(all_hits),
            },
            "current_step": "rule_engine",
            "step_times": {"rule_engine": elapsed},
        }

    return rule_engine_node
