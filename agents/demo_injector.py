"""
高风险 Demo 数据注入
在 PaySim 数据中注入构造的高风险交易样本，触发完整检测链路:
  - 制裁名单 (sanction_list, risk=95)
  - 备注关键词 (remark_keywords, risk=55)
  - 虚拟货币 (crypto_pattern, risk=95)
  - 跨境交易 (cross_border, risk=65)
  - 分拆转账 (smurfing, risk=70)
"""
import random


def inject_demo_txns(transactions: list, seed: int = 42) -> list:
    """向交易列表注入高风险 Demo 样本，确保全链路跑通"""
    rng = random.Random(seed)
    txns = list(transactions)  # 不修改原数据

    # 1. 制裁名单命中 (risk_score=95)
    txns.append({
        "step": 1, "type": "TRANSFER",
        "amount": 500000.0,
        "nameOrig": "SDN001_TERROR_FINANCE",
        "nameDest": "C1234567",
        "oldbalanceOrg": 10000000.0, "newbalanceOrig": 9500000.0,
        "oldbalanceDest": 5000.0, "newbalanceDest": 505000.0,
        "isFraud": 1, "isFlaggedFraud": 1,
        "remark": "紧急转账-需快速处理",
        "currency": "CNY", "region": "CN",
        "_demo": True,
    })

    # 2. 虚拟货币 + 高风险备注 (risk_score=95 + 55)
    txns.append({
        "step": 1, "type": "TRANSFER",
        "amount": 200000.0,
        "nameOrig": "C8765432",
        "nameDest": "C2345678",
        "oldbalanceOrg": 5000000.0, "newbalanceOrig": 4800000.0,
        "oldbalanceDest": 10000.0, "newbalanceDest": 210000.0,
        "isFraud": 1, "isFlaggedFraud": 1,
        "remark": "USDT换汇-洗钱-跑分平台结算",
        "currency": "CNY", "region": "CN",
        "_demo": True,
    })

    # 3. 跨境高风险地区 (risk_score=80)
    txns.append({
        "step": 1, "type": "TRANSFER",
        "amount": 800000.0,
        "nameOrig": "C1111111",
        "nameDest": "C2222222",
        "oldbalanceOrg": 20000000.0, "newbalanceOrig": 19200000.0,
        "oldbalanceDest": 500000.0, "newbalanceDest": 1300000.0,
        "isFraud": 1, "isFlaggedFraud": 1,
        "remark": "跨境贸易结算",
        "currency": "USD", "region": "KP",
        "_demo": True,
    })

    # 4. 分拆转账 (5笔 FROM 不同人 TO 同一人, step=5, risk=70)
    dest = "SHELL_COMPANY_001"
    payers = [f"PAYER_{i:03d}" for i in range(5)]
    for payer in payers:
        txns.append({
            "step": 5, "type": "TRANSFER",
            "amount": rng.uniform(40000, 50000),
            "nameOrig": payer,
            "nameDest": dest,
            "oldbalanceOrg": rng.uniform(100000, 500000),
            "newbalanceOrig": rng.uniform(50000, 450000),
            "oldbalanceDest": 0.0, "newbalanceDest": 0.0,
            "isFraud": 1, "isFlaggedFraud": 1,
            "remark": "",
            "currency": "CNY", "region": "CN",
            "_demo": True,
        })

    # 5. 空壳公司特征 (多个对手 + 极低留存)
    shell = "SHELL_CORP_X"
    for i in range(10):
        txns.append({
            "step": rng.randint(1, 10), "type": rng.choice(["TRANSFER", "CASH_OUT"]),
            "amount": rng.uniform(10000, 100000),
            "nameOrig": shell,
            "nameDest": f"COUNTERPARTY_{i:02d}",
            "oldbalanceOrg": rng.uniform(1000000, 5000000),
            "newbalanceOrig": rng.uniform(900000, 4900000),
            "oldbalanceDest": 0.0, "newbalanceDest": 0.0,
            "isFraud": 1, "isFlaggedFraud": 1,
            "remark": "",
            "currency": "CNY", "region": "CN",
            "_demo": True,
        })

    rng.shuffle(txns)
    return txns
