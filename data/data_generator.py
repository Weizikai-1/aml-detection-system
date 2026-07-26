"""
模拟交易数据生成器
当没有 PaySim 数据时，使用此脚本生成模拟数据

生成的数据包含:
- 正常交易(占 ~80%)
- 分拆转账(占 ~5%)
- 快进快出(占 ~5%)
- 对敲交易(占 ~5%)
- 大额可疑(占 ~5%)

输出: data/sample_transactions.json
"""
import random
import json
import os
from datetime import datetime, timedelta


def generate_normal_transactions(count: int = 120) -> list:
    """生成正常交易"""
    transactions = []
    for i in range(count):
        txn = {
            "transaction_id": f"TXN_N{i:06d}",
            "from_account": f"A{random.randint(1, 50):04d}",
            "to_account": f"A{random.randint(1, 50):04d}",
            "amount": round(random.uniform(100, 30000), 2),
            "timestamp": (datetime.now() - timedelta(days=random.randint(0, 30),
                                                       hours=random.randint(0, 23),
                                                       minutes=random.randint(0, 59))).isoformat(),
            "transaction_type": random.choice(["payment", "transfer", "cash_out"]),
            "remark": random.choice(["工资", "货款", "餐费", "转账", "退款", "报销", "购物"]),
            "is_suspicious": False,
        }
        transactions.append(txn)
    return transactions


def generate_smurfing_transactions(count: int = 8) -> list:
    """
    生成分拆转账交易(可疑)
    特征: 同一收款账户在短时间内收到多笔金额接近但略低于大额报告阈值的交易
    """
    transactions = []
    # 使用一个固定收款账户
    target_account = f"SMU_TARGET_{random.randint(100, 200):04d}"
    # 多个付款账户向同一账户转账
    base_time = datetime.now() - timedelta(days=random.randint(1, 5))

    for i in range(count):
        txn = {
            "transaction_id": f"TXN_SMU{i:04d}",
            "from_account": f"SMU_SRC_{random.randint(1, 30):04d}",
            "to_account": target_account,
            "amount": round(random.uniform(40000, 49000), 2),
            "timestamp": (base_time + timedelta(minutes=i * random.randint(3, 10))).isoformat(),
            "transaction_type": "transfer",
            "remark": "转账",
            "is_suspicious": True,
            "suspicious_reason": "分拆转账",
        }
        transactions.append(txn)
    return transactions


def generate_fast_in_fast_out_transactions(count: int = 3) -> list:
    """
    生成快进快出交易(可疑)
    特征: 资金进入账户后短时间内(10分钟内)几乎全部转出(≥95%)
    """
    transactions = []
    base_time = datetime.now() - timedelta(days=random.randint(1, 5))

    for i in range(count):
        account_in = f"FIFO_A{i:04d}"
        account_src = f"FIFO_SRC_{i:04d}"
        account_dst = f"FIFO_DST_{i:04d}"
        amount = round(random.uniform(50000, 200000), 2)
        out_amount = round(amount * random.uniform(0.95, 1.0), 2)

        # 资金转入
        txn_in = {
            "transaction_id": f"TXN_FIFO{i:04d}_IN",
            "from_account": account_src,
            "to_account": account_in,
            "amount": amount,
            "timestamp": base_time.isoformat(),
            "transaction_type": "transfer",
            "remark": "货款",
            "is_suspicious": True,
            "suspicious_reason": "快进快出",
        }

        # 资金转出(几分钟后)
        txn_out = {
            "transaction_id": f"TXN_FIFO{i:04d}_OUT",
            "from_account": account_in,
            "to_account": account_dst,
            "amount": out_amount,
            "timestamp": (base_time + timedelta(minutes=random.randint(2, 9))).isoformat(),
            "transaction_type": "transfer",
            "remark": "转账",
            "is_suspicious": True,
            "suspicious_reason": "快进快出",
        }

        transactions.extend([txn_in, txn_out])
    return transactions


def generate_round_trip_transactions(count: int = 3) -> list:
    """
    生成对敲交易(可疑)
    特征: 两个账户之间短期内互相转账，金额相近
    """
    transactions = []
    base_time = datetime.now() - timedelta(days=random.randint(1, 5))

    for i in range(count):
        acc_a = f"RT_A{i:04d}"
        acc_b = f"RT_B{i:04d}"
        amount = round(random.uniform(50000, 150000), 2)

        # A -> B
        txn_ab = {
            "transaction_id": f"TXN_RT{i:04d}_AB",
            "from_account": acc_a,
            "to_account": acc_b,
            "amount": amount,
            "timestamp": base_time.isoformat(),
            "transaction_type": "transfer",
            "remark": "转账",
            "is_suspicious": True,
            "suspicious_reason": "对敲交易",
        }

        # B -> A (几天后，金额相近)
        txn_ba = {
            "transaction_id": f"TXN_RT{i:04d}_BA",
            "from_account": acc_b,
            "to_account": acc_a,
            "amount": round(amount * random.uniform(0.85, 1.15), 2),
            "timestamp": (base_time + timedelta(days=random.randint(1, 5))).isoformat(),
            "transaction_type": "transfer",
            "remark": "转账",
            "is_suspicious": True,
            "suspicious_reason": "对敲交易",
        }

        transactions.extend([txn_ab, txn_ba])
    return transactions


def generate_large_amount_transactions(count: int = 5) -> list:
    """
    生成大额可疑交易(可疑)
    特征: 单笔交易金额超过大额报告阈值(10万)
    """
    transactions = []
    base_time = datetime.now() - timedelta(days=random.randint(1, 10))

    for i in range(count):
        txn = {
            "transaction_id": f"TXN_LRG{i:04d}",
            "from_account": f"LRG_SRC_{random.randint(1, 20):04d}",
            "to_account": f"LRG_DST_{random.randint(1, 20):04d}",
            "amount": round(random.uniform(100000, 500000), 2),
            "timestamp": (base_time + timedelta(hours=random.randint(0, 48))).isoformat(),
            "transaction_type": "transfer",
            "remark": random.choice(["投资款", "购房款", "大额转账", "商业往来"]),
            "is_suspicious": True,
            "suspicious_reason": "大额交易",
        }
        transactions.append(txn)
    return transactions


def generate_test_data(
    normal_count: int = 120,
    suspicious_modes: list = None,
    saved_path: str = None,
) -> list:
    """
    生成测试数据(主入口)

    Args:
        normal_count: 正常交易数量
        suspicious_modes: 可疑模式列表，可选: smurfing, fast_in_fast_out, round_trip, large_amount
        saved_path: 保存路径，None则不保存

    Returns:
        交易列表
    """
    if suspicious_modes is None:
        suspicious_modes = ["smurfing", "fast_in_fast_out", "round_trip", "large_amount"]

    normal = generate_normal_transactions(normal_count)
    all_suspicious = []

    mode_map = {
        "smurfing": lambda: generate_smurfing_transactions(8),
        "fast_in_fast_out": lambda: generate_fast_in_fast_out_transactions(3),
        "round_trip": lambda: generate_round_trip_transactions(3),
        "large_amount": lambda: generate_large_amount_transactions(5),
    }

    for mode in suspicious_modes:
        if mode in mode_map:
            all_suspicious.extend(mode_map[mode]())

    all_data = normal + all_suspicious
    random.shuffle(all_data)

    # 保存到文件
    if saved_path:
        with open(saved_path, "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)
        print(f"  保存到: {saved_path}")

    return all_data


def generate_all_transactions(saved_path: str = None) -> list:
    """
    生成所有交易数据(正常+各类可疑)
    返回交易列表，同时保存到 JSON 文件
    """
    normal = generate_normal_transactions(120)
    smurfing = generate_smurfing_transactions(8)
    fast_in_fast_out = generate_fast_in_fast_out_transactions(3)
    round_trip = generate_round_trip_transactions(3)
    large_amount = generate_large_amount_transactions(5)

    all_data = normal + smurfing + fast_in_fast_out + round_trip + large_amount

    # 打乱顺序
    random.shuffle(all_data)

    # 保存到文件
    if saved_path is None:
        saved_path = os.path.join(os.path.dirname(__file__), "sample_transactions.json")

    with open(saved_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print(f"[数据生成] 共生成 {len(all_data)} 条交易数据")
    print(f"  - 正常交易: {len(normal)} 条")
    print(f"  - 分拆转账(可疑): {len(smurfing)} 条")
    print(f"  - 快进快出(可疑): {len(fast_in_fast_out)} 条")
    print(f"  - 对敲交易(可疑): {len(round_trip)} 条")
    print(f"  - 大额交易(可疑): {len(large_amount)} 条")
    print(f"  保存到: {saved_path}")

    return all_data


if __name__ == "__main__":
    path = os.path.join(os.path.dirname(__file__), "sample_transactions.json")
    generate_all_transactions(saved_path=path)
