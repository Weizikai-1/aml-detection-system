"""
Agent 1: 数据预处理 Agent

职责: 清洗交易数据，提取特征，为后续分析做准备
模式: create_data_preprocessor_agent(llm) -> node_function

输入: state["transactions"] (原始交易流水)
输出: state["cleaned_transactions"], state["transaction_features"], state["preprocessing_stats"]
"""
import time
from datetime import datetime
from collections import Counter
from graph.state import AMLState, Transaction


def _parse_timestamp(ts_str: str):
    """解析时间戳字符串，兼容多种格式"""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def _amount_level(amount: float) -> str:
    """根据金额划分等级"""
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

            # 补充缺失字段
            original_keys = set(t.keys())
            t.setdefault("from_account", "UNKNOWN")
            t.setdefault("to_account", "UNKNOWN")
            t.setdefault("amount", 0.0)
            t.setdefault("timestamp", datetime.now().isoformat())
            t.setdefault("transaction_type", "unknown")
            t.setdefault("remark", "")
            t.setdefault("is_suspicious", False)
            t.setdefault("suspicious_reason", "")
            t.setdefault("risk_score", None)
            if len(set(t.keys()) - original_keys) > 0:
                missing_filled += 1

            # 确保金额为数值
            try:
                t["amount"] = float(t["amount"])
            except (ValueError, TypeError):
                t["amount"] = 0.0

            # 解析时间戳
            parsed_ts = _parse_timestamp(t.get("timestamp"))

            # 提取交易级特征
            t["amount_level"] = _amount_level(t["amount"])
            t["is_weekend"] = _is_weekend(parsed_ts)
            t["is_night"] = _is_night_transaction(parsed_ts)
            t["from_account_risk"] = "normal"  # 可后续扩展
            t["to_account_risk"] = "normal"

            cleaned.append(t)

        # 按时间倒序排列
        cleaned.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        print(f"  - 缺失值填充: {missing_filled} 条")
        print(f"  - 清洗后交易数: {len(cleaned)}")

        # ---- 3. 全局统计特征 ----
        amounts = [t["amount"] for t in cleaned if t["amount"] > 0]
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

        elapsed = time.time() - start_time
        print(f"  - 交易总金额: {features['total_amount']:,.2f} 元")
        print(f"  - 平均金额: {features['avg_amount']:,.2f} 元")
        print(f"  - 活跃账户数(≥3笔): {features['active_accounts_count']}")
        print(f"  - 夜间交易: {night_count} 笔 ({features['night_ratio']:.1%})")
        print(f"  - 周末交易: {weekend_count} 笔 ({features['weekend_ratio']:.1%})")
        print(f"  - 已标注可疑: {labeled_suspicious} 笔")
        print(f"  耗时: {elapsed:.2f} 秒")
        print("[Agent 1] 数据预处理完成")

        return {
            "cleaned_transactions": cleaned,
            "transaction_features": features,
            "preprocessing_stats": stats,
            "current_step": "data_preprocessor",
            "step_times": {"data_preprocessor": elapsed},
        }

    return data_preprocessor_node
