"""
Agent 1: 数据预处理 Agent

职责: 清洗交易数据，提取特征，为后续分析做准备
模式: create_data_preprocessor_agent(llm) -> node_function

输入: state["transactions"] (原始交易流水)
输出: state["cleaned_transactions"], state["transaction_features"], state["preprocessing_stats"],
      state["account_baselines"] (账户行为基线)
"""
import time
from datetime import datetime
from collections import Counter, defaultdict
from graph.state import AMLState, Transaction


def _parse_timestamp(ts_str: str):
    """解析时间戳字符串，兼容多种格式"""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def _amount_level(amount) -> str:
    """根据金额划分等级"""
    # 戒律 M1: amount 缺失（None）时返回 unknown，不编造等级
    if amount is None:
        return "unknown"
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return "unknown"
    if amount < 10000:
        return "low"
    elif amount < 50000:
        return "medium"
    elif amount < 100000:
        return "high"
    else:
        return "very_high"


def _is_night_transaction(ts: datetime) -> bool:
    """判断是否夜间交易(22:00-06:00)"""
    if ts is None:
        return False
    hour = ts.hour
    return hour >= 22 or hour < 6


def _is_weekend(ts: datetime) -> bool:
    """判断是否周末交易"""
    if ts is None:
        return False
    return ts.weekday() >= 5


def _compute_account_baselines(transactions: list) -> dict:
    """
    计算每个账户的交易行为基线

    基线指标:
    - 总交易笔数、总金额、平均金额、中位数金额
    - 活跃天数、日均笔数、日均金额
    - 入账/出账比例
    - 主要交易时段分布(白天/夜间)
    - Top 交易对手
    - 金额标准差(波动性)
    """
    account_data = defaultdict(lambda: {
        "in_txns": [],
        "out_txns": [],
        "all_amounts": [],
        "days": set(),
        "hours": [],
        "counterparties": Counter(),
    })

    for txn in transactions:
        from_acc = txn.get("from_account", "UNKNOWN")
        to_acc = txn.get("to_account", "UNKNOWN")
        # 戒律 P2: 自转账不构成真实账户行为基线（A→A 不反映资金流转规律）
        if from_acc and to_acc and from_acc == to_acc:
            continue
        # 戒律 M1: amount 缺失（None）跳过，不编造 0
        raw_amount = txn.get("amount")
        if raw_amount is None:
            continue
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError):
            continue
        ts = _parse_timestamp(txn.get("timestamp"))

        # 付款方账户（出账）
        d_from = account_data[from_acc]
        d_from["out_txns"].append(amount)
        d_from["all_amounts"].append(amount)
        d_from["counterparties"][to_acc] += 1
        if ts:
            d_from["days"].add(ts.date().isoformat())
            d_from["hours"].append(ts.hour)

        # 收款方账户（入账）
        d_to = account_data[to_acc]
        d_to["in_txns"].append(amount)
        d_to["all_amounts"].append(amount)
        d_to["counterparties"][from_acc] += 1
        if ts:
            d_to["days"].add(ts.date().isoformat())
            d_to["hours"].append(ts.hour)

    baselines = {}
    for acc, data in account_data.items():
        all_amounts = data["all_amounts"]
        in_amounts = data["in_txns"]
        out_amounts = data["out_txns"]
        n = len(all_amounts)
        days_count = max(len(data["days"]), 1)

        # 基础统计
        total_amount = sum(all_amounts)
        avg_amount = total_amount / n if n > 0 else 0

        # 中位数
        sorted_amts = sorted(all_amounts)
        if n == 0:
            median_amount = 0
        elif n % 2 == 1:
            median_amount = sorted_amts[n // 2]
        else:
            median_amount = (sorted_amts[n // 2 - 1] + sorted_amts[n // 2]) / 2

        # 标准差(波动性)
        if n > 1:
            variance = sum((x - avg_amount) ** 2 for x in all_amounts) / (n - 1)
            std_amount = variance ** 0.5
        else:
            std_amount = 0

        # 变异系数 = 标准差/均值，衡量相对波动
        cv_amount = std_amount / avg_amount if avg_amount > 0 else 0

        # 时段分布
        hours = data["hours"]
        if hours:
            night_hours = sum(1 for h in hours if h >= 22 or h < 6)
            night_ratio = night_hours / len(hours)
        else:
            night_ratio = 0

        # 入账出账比例
        in_total = sum(in_amounts)
        out_total = sum(out_amounts)
        flow_total = in_total + out_total
        in_ratio = in_total / flow_total if flow_total > 0 else 0.5
        out_ratio = out_total / flow_total if flow_total > 0 else 0.5

        # Top 交易对手
        top_counterparties = [
            acc for acc, _ in data["counterparties"].most_common(5)
        ]

        baselines[acc] = {
            "account": acc,
            "total_txns": n,
            "total_amount": round(total_amount, 2),
            "avg_amount": round(avg_amount, 2),
            "median_amount": round(median_amount, 2),
            "std_amount": round(std_amount, 2),
            "cv_amount": round(cv_amount, 3),
            "in_txns_count": len(in_amounts),
            "out_txns_count": len(out_amounts),
            "in_amount_total": round(in_total, 2),
            "out_amount_total": round(out_total, 2),
            "in_ratio": round(in_ratio, 3),
            "out_ratio": round(out_ratio, 3),
            "active_days": len(data["days"]),
            "daily_avg_txns": round(n / days_count, 2),
            "daily_avg_amount": round(total_amount / days_count, 2),
            "night_transaction_ratio": round(night_ratio, 3),
            "top_counterparties": top_counterparties,
            "counterparty_count": len(data["counterparties"]),
        }

    return baselines


def create_data_preprocessor_agent(llm=None):
    """
    创建数据预处理Agent

    注意: 数据预处理是纯规则逻辑，不依赖LLM，但保留llm参数以保持工厂函数签名一致

    Args:
        llm: LLM 实例(此Agent不使用，仅为接口一致性)

    Returns:
        可直接传入 StateGraph.add_node 的节点函数
    """

    def data_preprocessor_node(state: AMLState) -> dict:
        """
        数据预处理节点函数

        工作内容:
        1. 去除重复交易(按 transaction_id 去重)
        2. 处理缺失值(补充默认值)
        3. 标准化时间格式
        4. 提取交易级特征(金额等级、时间模式等)
        5. 计算全局统计特征
        """
        start_time = time.time()
        print("\n" + "=" * 60)
        print("[Agent 1] 数据预处理 Agent 启动")
        print("=" * 60)

        transactions = state.get("transactions", [])
        total_count = len(transactions)
        print(f"  输入交易数: {total_count}")

        if total_count == 0:
            print("[Agent 1] 警告: 无交易数据输入")
            return {
                "cleaned_transactions": [],
                "transaction_features": {},
                "preprocessing_stats": {
                    "total": 0,
                    "cleaned": 0,
                    "duplicates_removed": 0,
                    "missing_values_filled": 0,
                },
                "account_baselines": {},
                "current_step": "data_preprocessor",
                "step_times": {"data_preprocessor": time.time() - start_time},
            }

        # ---- 1. 去重 ----
        seen_ids = set()
        deduped = []
        duplicates_removed = 0
        for txn in transactions:
            tid = txn.get("transaction_id", "")
            if tid and tid in seen_ids:
                duplicates_removed += 1
                continue
            seen_ids.add(tid)
            deduped.append(txn)

        print(f"  - 去重: 移除 {duplicates_removed} 条重复交易")

        # ---- 2. 处理缺失值 & 标准化 & 特征提取 ----
        cleaned: list[Transaction] = []
        missing_filled = 0

        for txn in deduped:
            t = dict(txn)  # 浅拷贝，避免修改原数据

            # 补充缺失字段（戒律 M1: 不编造数据，缺失关键字段标记为 invalid）
            original_keys = set(t.keys())
            t.setdefault("from_account", "UNKNOWN")
            t.setdefault("to_account", "UNKNOWN")
            # 戒律 M1: amount 缺失时不编造 0.0，标记为 None，规则引擎入口跳过
            t.setdefault("amount", None)
            # 戒律 M1: timestamp 缺失时不编造当前时间，标记为 invalid
            if not t.get("timestamp"):
                t["_ts_missing"] = True
                t["timestamp"] = ""  # 留空，规则引擎会跳过
            else:
                t["_ts_missing"] = False
            t.setdefault("transaction_type", "unknown")
            t.setdefault("remark", "")
            t.setdefault("is_suspicious", False)
            t.setdefault("suspicious_reason", "")
            t.setdefault("risk_score", None)
            # 标记缺失关键字段的交易（不参与规则分析，仅保留用于统计）
            # 戒律 M1: amount 为 None 也标记为 invalid
            if t.get("_ts_missing") or t.get("from_account") == "UNKNOWN" or t.get("to_account") == "UNKNOWN" or t.get("amount") is None:
                t["_invalid"] = True
                t["_invalid_reason"] = ",".join([
                    k for k, v in [
                        ("timestamp", t.get("_ts_missing")),
                        ("from_account", t.get("from_account") == "UNKNOWN"),
                        ("to_account", t.get("to_account") == "UNKNOWN"),
                        ("amount", t.get("amount") is None),
                    ] if v
                ])
            if len(set(t.keys()) - original_keys) > 0:
                missing_filled += 1

            # 确保金额为数值（戒律 M1: 缺失 amount 保留 None，规则引擎入口跳过）
            if t.get("amount") is None:
                pass  # 保留 None，不编造
            else:
                try:
                    t["amount"] = float(t["amount"])
                except (ValueError, TypeError):
                    t["amount"] = None  # 非法值也标记为 None

            # 解析时间戳
            parsed_ts = _parse_timestamp(t.get("timestamp"))

            # 提取交易级特征
            t["amount_level"] = _amount_level(t["amount"])
            t["is_weekend"] = _is_weekend(parsed_ts)
            t["is_night"] = _is_night_transaction(parsed_ts)
            # 戒律 M1: 不编造账户风险等级，缺失时标记为 None 并加 missing 标记
            t["from_account_risk"] = None
            t["to_account_risk"] = None
            t["_risk_missing"] = True
            # 地理风险评分初始化（B1-2: 在批量阶段统一计算，单笔先置0）
            t["geo_risk_score"] = 0
            t["geo_risk_reasons"] = []

            cleaned.append(t)

        # 按时间倒序排列
        cleaned.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        print(f"  - 缺失值填充: {missing_filled} 条")
        print(f"  - 清洗后交易数: {len(cleaned)}")

        # ---- 2.5 地理风险评分（B1-2: 地理风险因子评分） ----
        # 戒律 M1: 基于交易真实地理字段计算，不臆测
        # 戒律 M3: 评分范围 0-100
        try:
            from tools.geo_risk_scorer import geo_risk_scorer
            geo_risk_scorer.score_transactions(cleaned)
            geo_scored_count = sum(1 for t in cleaned if t.get("geo_risk_score", 0) > 0)
            if geo_scored_count > 0:
                print(f"  - 地理风险评分: {geo_scored_count} 笔交易命中地理风险")
        except Exception as e:
            # 戒律: 异常隔离，不阻塞预处理
            print(f"  - 地理风险评分跳过: {e}")

        # ---- 3. 全局统计特征 ----
        # 戒律 M1: amount 可能为 None，统计时过滤掉
        amounts = [t["amount"] for t in cleaned if isinstance(t.get("amount"), (int, float)) and t["amount"] > 0]
        accounts_from = [t["from_account"] for t in cleaned]
        accounts_to = [t["to_account"] for t in cleaned]
        all_accounts = accounts_from + accounts_to
        account_counter = Counter(all_accounts)
        active_accounts = [acc for acc, cnt in account_counter.items() if cnt >= 3]

        # 金额分级统计
        amount_levels = Counter(t.get("amount_level", "unknown") for t in cleaned)

        # 交易类型分布
        type_dist = Counter(t.get("transaction_type", "unknown") for t in cleaned)

        # 时间模式
        night_count = sum(1 for t in cleaned if t.get("is_night"))
        weekend_count = sum(1 for t in cleaned if t.get("is_weekend"))

        # 可疑标注统计
        labeled_suspicious = sum(1 for t in cleaned if t.get("is_suspicious"))

        # 计算中位数金额
        sorted_amounts = sorted(amounts) if amounts else []
        n = len(sorted_amounts)
        if n == 0:
            median_amount = 0
        elif n % 2 == 1:
            median_amount = sorted_amounts[n // 2]
        else:
            median_amount = (sorted_amounts[n // 2 - 1] + sorted_amounts[n // 2]) / 2

        features = {
            # 总量统计
            "total_transactions": len(cleaned),
            "total_amount": round(sum(amounts), 2) if amounts else 0,
            "avg_amount": round(sum(amounts) / len(amounts), 2) if amounts else 0,
            "max_amount": max(amounts) if amounts else 0,
            "min_amount": min(amounts) if amounts else 0,
            "median_amount": round(median_amount, 2),

            # 账户统计
            "unique_from_accounts": len(set(accounts_from)),
            "unique_to_accounts": len(set(accounts_to)),
            "total_unique_accounts": len(set(all_accounts)),
            "active_accounts_count": len(active_accounts),
            "top_active_accounts": active_accounts[:10],

            # 金额分布
            "amount_level_distribution": dict(amount_levels),

            # 交易类型
            "transaction_type_distribution": dict(type_dist),

            # 时间模式
            "night_transaction_count": night_count,
            "night_ratio": round(night_count / len(cleaned), 3) if cleaned else 0,
            "weekend_transaction_count": weekend_count,
            "weekend_ratio": round(weekend_count / len(cleaned), 3) if cleaned else 0,

            # 标注统计
            "pre_labeled_suspicious_count": labeled_suspicious,
        }

        stats = {
            "total": total_count,
            "cleaned": len(cleaned),
            "duplicates_removed": duplicates_removed,
            "missing_values_filled": missing_filled,
        }

        # ---- 4. 账户行为基线 ----
        account_baselines = _compute_account_baselines(cleaned)
        high_volatility_accounts = [
            acc for acc, b in account_baselines.items()
            if b["cv_amount"] > 1.5 and b["total_txns"] >= 5
        ]

        elapsed = time.time() - start_time
        print(f"  - 交易总金额: {features['total_amount']:,.2f} 元")
        print(f"  - 平均金额: {features['avg_amount']:,.2f} 元")
        print(f"  - 活跃账户数(≥3笔): {features['active_accounts_count']}")
        print(f"  - 夜间交易: {night_count} 笔 ({features['night_ratio']:.1%})")
        print(f"  - 周末交易: {weekend_count} 笔 ({features['weekend_ratio']:.1%})")
        print(f"  - 已标注可疑: {labeled_suspicious} 笔")
        print(f"  - 行为基线: {len(account_baselines)} 个账户，高波动账户 {len(high_volatility_accounts)} 个")
        print(f"  耗时: {elapsed:.2f} 秒")
        print("[Agent 1] 数据预处理完成")

        return {
            "cleaned_transactions": cleaned,
            "transaction_features": features,
            "preprocessing_stats": stats,
            "account_baselines": account_baselines,
            "current_step": "data_preprocessor",
            "step_times": {"data_preprocessor": elapsed},
        }

    return data_preprocessor_node
