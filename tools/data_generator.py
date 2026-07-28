"""
模拟交易数据生成器
当没有 PaySim 数据时，使用此脚本生成模拟数据

生成的数据包含:
- 正常交易(占 ~80%)
- 分拆转账(占 ~5%)
- 快进快出(占 ~5%)
- 对敲交易(占 ~5%)
- 大额报告交易(占 ~5%，仅触发报告，非可疑)

输出: data/sample_transactions.json
"""
import random
import json
import os
from datetime import datetime, timedelta


def generate_normal_transactions(count: int = 120) -> list:
    """生成正常交易"""
    # 按 remark 分组设定真实金额范围，避免出现"工资 30000 元"等不真实组合
    remark_amount_ranges = {
        "工资": (5000, 15000),
        "货款": (1000, 30000),
        "餐费": (50, 500),
        "转账": (100, 10000),
        "退款": (50, 5000),
        "报销": (100, 5000),
        "购物": (100, 8000),
        "房租": (1500, 8000),
        "学费": (2000, 20000),
        "医疗": (100, 15000),
        "理财": (1000, 50000),
        "还款": (500, 30000),
    }
    remarks = list(remark_amount_ranges.keys())

    # 常见账户名前缀
    first_names = ["张", "王", "李", "赵", "刘", "陈", "杨", "黄", "周", "吴",
                   "徐", "孙", "胡", "朱", "高", "林", "何", "郭", "马", "罗"]
    second_names = ["伟", "芳", "娜", "敏", "静", "丽", "强", "磊", "洋", "勇",
                    "艳", "杰", "军", "娟", "涛", "明", "超", "秀英", "霞", "平"]

    accounts = [f"62220212345678{i:04d}" for i in range(1, 31)]
    names = [f"{random.choice(first_names)}{random.choice(second_names)}" for _ in range(30)]

    transactions = []
    base_time = datetime.now() - timedelta(days=7)

    for i in range(count):
        remark = random.choice(remarks)
        amount_range = remark_amount_ranges[remark]
        amount = round(random.uniform(*amount_range), 2)

        from_idx = random.randint(0, len(accounts) - 1)
        to_idx = random.randint(0, len(accounts) - 1)
        while to_idx == from_idx:
            to_idx = random.randint(0, len(accounts) - 1)

        txn_time = base_time + timedelta(
            days=random.randint(0, 6),
            hours=random.randint(8, 20),
            minutes=random.randint(0, 59)
        )

        transactions.append({
            "transaction_id": f"TXN-{i+1:03d}",
            "from_account": accounts[from_idx],
            "from_name": names[from_idx],
            "to_account": accounts[to_idx],
            "to_name": names[to_idx],
            "amount": amount,
            "currency": "CNY",
            "timestamp": txn_time.strftime("%Y-%m-%d %H:%M:%S"),
            "remark": remark,
            "channel": random.choice(["网银", "手机银行", "ATM", "柜台"]),
            "status": "success",
            "is_suspicious": False,
            "suspicious_reason": "",
        })

    return transactions


def generate_smurfing_transactions() -> list:
    """生成分拆转账模式（小额多笔给同一个人）"""
    transactions = []
    smurf_account = "6222021234567890001"
    smurf_name = "张三"
    target_account = "6222021234567890002"
    target_name = "李四"
    base_time = datetime.now() - timedelta(days=3)

    # 8 笔转账，每笔都低于 5 万，总额 38 万
    amounts = [48000, 49000, 47500, 48500, 46000, 49500, 47000, 44500]
    for i, amount in enumerate(amounts):
        txn_time = base_time + timedelta(minutes=i * 15)
        transactions.append({
            "transaction_id": f"TXN-SMURF-{i+1:03d}",
            "from_account": smurf_account,
            "from_name": smurf_name,
            "to_account": target_account,
            "to_name": target_name,
            "amount": float(amount),
            "currency": "CNY",
            "timestamp": txn_time.strftime("%Y-%m-%d %H:%M:%S"),
            "remark": "还款",
            "channel": "手机银行",
            "status": "success",
            "is_suspicious": True,
            "suspicious_reason": "smurfing",
        })

    return transactions


def generate_fast_in_fast_out_transactions() -> list:
    """生成快进快出模式（大额进，很快又转出）"""
    transactions = []
    account = "6222021234567890003"
    name = "王五"
    from_account = "6222021234567890004"
    from_name = "赵六"
    to_account = "6222021234567890005"
    to_name = "孙七"
    base_time = datetime.now() - timedelta(days=2)

    # 入账 20 万
    transactions.append({
        "transaction_id": "TXN-FIFO-001",
        "from_account": from_account,
        "from_name": from_name,
        "to_account": account,
        "to_name": name,
        "amount": 200000.0,
        "currency": "CNY",
        "timestamp": base_time.strftime("%Y-%m-%d %H:%M:%S"),
        "remark": "货款",
        "channel": "网银",
        "status": "success",
        "is_suspicious": True,
        "suspicious_reason": "fast_in_fast_out",
    })

    # 5 分钟后转出 19.5 万
    txn_time = base_time + timedelta(minutes=5)
    transactions.append({
        "transaction_id": "TXN-FIFO-002",
        "from_account": account,
        "from_name": name,
        "to_account": to_account,
        "to_name": to_name,
        "amount": 195000.0,
        "currency": "CNY",
        "timestamp": txn_time.strftime("%Y-%m-%d %H:%M:%S"),
        "remark": "投资",
        "channel": "网银",
        "status": "success",
        "is_suspicious": True,
        "suspicious_reason": "fast_in_fast_out",
    })

    return transactions


def generate_round_tripping_transactions() -> list:
    """生成对敲交易（互相转账，金额相近）"""
    transactions = []
    account_a = "6222021234567890006"
    name_a = "周八"
    account_b = "6222021234567890007"
    name_b = "吴九"
    base_time = datetime.now() - timedelta(days=5)

    # A 转给 B 15 万
    transactions.append({
        "transaction_id": "TXN-RT-001",
        "from_account": account_a,
        "from_name": name_a,
        "to_account": account_b,
        "to_name": name_b,
        "amount": 150000.0,
        "currency": "CNY",
        "timestamp": base_time.strftime("%Y-%m-%d %H:%M:%S"),
        "remark": "货款",
        "channel": "网银",
        "status": "success",
        "is_suspicious": True,
        "suspicious_reason": "round_trip",
    })

    # 3 天后 B 转给 A 14.8 万（相差约 1.3%）
    txn_time = base_time + timedelta(days=3)
    transactions.append({
        "transaction_id": "TXN-RT-002",
        "from_account": account_b,
        "from_name": name_b,
        "to_account": account_a,
        "to_name": name_a,
        "amount": 148000.0,
        "currency": "CNY",
        "timestamp": txn_time.strftime("%Y-%m-%d %H:%M:%S"),
        "remark": "退款",
        "channel": "网银",
        "status": "success",
        "is_suspicious": True,
        "suspicious_reason": "round_trip",
    })

    return transactions


def generate_large_transactions() -> list:
    """生成大额交易（仅触发报告阈值，不一定可疑）"""
    transactions = []
    base_time = datetime.now() - timedelta(days=1)

    large_txn = [
        ("6222021234567890008", "郑十", "6222021234567890009", "冯一", 120000, "货款"),
        ("6222021234567890010", "陈二", "6222021234567890011", "褚三", 250000, "投资"),
        ("6222021234567890012", "卫四", "6222021234567890013", "蒋五", 80000, "理财"),
    ]

    for i, (from_acc, from_name, to_acc, to_name, amount, remark) in enumerate(large_txn):
        txn_time = base_time + timedelta(hours=i * 2)
        transactions.append({
            "transaction_id": f"TXN-LARGE-{i+1:03d}",
            "from_account": from_acc,
            "from_name": from_name,
            "to_account": to_acc,
            "to_name": to_name,
            "amount": float(amount),
            "currency": "CNY",
            "timestamp": txn_time.strftime("%Y-%m-%d %H:%M:%S"),
            "remark": remark,
            "channel": "网银",
            "status": "success",
        })

    return transactions


def generate_test_data(normal_count: int = 120, suspicious_modes: list = None, saved_path: str = None) -> list:
    """生成测试交易数据

    Args:
        normal_count: 正常交易数量
        suspicious_modes: 可疑模式列表，可选值：smurfing, fast_in_fast_out, round_trip, large_amount
        saved_path: 保存路径（为None时不保存）
    """
    random.seed(42)  # 固定种子，保证可重现

    if suspicious_modes is None:
        suspicious_modes = ["smurfing", "fast_in_fast_out", "round_trip", "large_amount"]

    transactions = []
    transactions.extend(generate_normal_transactions(normal_count))

    mode_map = {
        "smurfing": generate_smurfing_transactions,
        "fast_in_fast_out": generate_fast_in_fast_out_transactions,
        "round_trip": generate_round_tripping_transactions,
        "large_amount": generate_large_transactions,
    }

    for mode in suspicious_modes:
        if mode in mode_map:
            transactions.extend(mode_map[mode]())

    # 按时间排序
    transactions.sort(key=lambda x: x["timestamp"])

    # 重新编号 transaction_id
    for i, txn in enumerate(transactions, 1):
        txn["transaction_id"] = f"TXN-{i:03d}"

    if saved_path:
        os.makedirs(os.path.dirname(saved_path), exist_ok=True)
        with open(saved_path, "w", encoding="utf-8") as f:
            json.dump(transactions, f, ensure_ascii=False, indent=2)
        print(f"已生成 {len(transactions)} 条测试数据，保存到: {saved_path}")

    return transactions


def save_test_data(filepath: str = None):
    """保存测试数据到文件"""
    if filepath is None:
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        filepath = os.path.join(data_dir, "sample_transactions.json")

    transactions = generate_test_data()

    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(transactions, f, ensure_ascii=False, indent=2)

    print(f"已生成 {len(transactions)} 条测试数据，保存到: {filepath}")
    return filepath


if __name__ == "__main__":
    save_test_data()
