"""
数据加载器 - 数据先行原则的核心模块
- 优先加载真实 PaySim CSV
- 降级生成 PaySim 格式模拟数据（带 Ground Truth 标签）
"""
import os
import numpy as np
import pandas as pd
from settings import PAYSIM_CSV, PAYSIM_SAMPLE, DATA_SOURCE, RANDOM_SEED


def load_data(n_rows: int = 5000) -> pd.DataFrame:
    """统一数据入口：真实数据优先，模拟数据降级"""
    np.random.seed(RANDOM_SEED)  # 确保可复现，仅在此处设置
    if os.path.exists(PAYSIM_CSV):
        return _load_real(n_rows)
    return generate_synthetic(n_rows)


def get_source_label() -> str:
    """返回当前数据来源（诚实标注）"""
    if os.path.exists(PAYSIM_CSV) or os.path.exists(PAYSIM_SAMPLE):
        return DATA_SOURCE["primary"]
    return DATA_SOURCE["fallback"]


def _load_real(n_rows: int) -> pd.DataFrame:
    """加载 Kaggle PaySim 真实数据集 — 优先样本文件"""
    src = PAYSIM_SAMPLE if os.path.exists(PAYSIM_SAMPLE) else PAYSIM_CSV
    try:
        df = pd.read_csv(src)
    except Exception as e:
        raise RuntimeError(f"无法读取 PaySim 数据 {src}: {e}") from e
    if n_rows and len(df) > n_rows:
        df = df.sample(n=n_rows, random_state=RANDOM_SEED)
    return df.reset_index(drop=True)


def generate_synthetic(n_rows: int) -> pd.DataFrame:
    """生成 PaySim 格式模拟数据（含已知 Ground Truth）"""
    n_fraud = int(n_rows * 0.013)  # PaySim 真实欺诈率 ~1.3%
    types = ["CASH_IN", "CASH_OUT", "DEBIT", "PAYMENT", "TRANSFER"]
    type_weights = [0.20, 0.23, 0.05, 0.34, 0.18]

    records = []
    for i in range(n_rows):
        is_fraud = 1 if i < n_fraud else 0
        amount = _gen_amount(is_fraud)
        txn_type = np.random.choice(types, p=type_weights)

        org_bal_before = max(np.random.lognormal(10, 2), 1) if not is_fraud else np.random.lognormal(8, 2)
        org_bal_after = max(org_bal_before - amount, 0) if txn_type != "CASH_IN" else org_bal_before + amount

        records.append({
            "step": np.random.randint(1, 745),
            "type": txn_type,
            "amount": round(amount, 2),
            "nameOrig": f"C{np.random.randint(1000000, 9999999)}",
            "oldbalanceOrg": round(org_bal_before, 2),
            "newbalanceOrig": round(org_bal_after, 2),
            "nameDest": f"C{np.random.randint(1000000, 9999999)}",
            "oldbalanceDest": round(np.random.lognormal(10, 2), 2),
            "newbalanceDest": round(np.random.lognormal(10, 2), 2),
            "isFraud": is_fraud,
            "isFlaggedFraud": 0,
        })
    return pd.DataFrame(records)


def _gen_amount(is_fraud: int) -> float:
    """欺诈交易金额偏高，但不过分离谱"""
    if is_fraud:
        return np.random.choice([
            np.random.lognormal(9, 2),     # ~8K
            np.random.lognormal(11, 1),    # ~60K
            np.random.exponential(50000),  # ~50K 均值
        ])
    return max(np.random.lognormal(7, 2.5), 1)


def stats(df: pd.DataFrame) -> dict:
    """快速数据统计"""
    fraud = df[df["isFraud"] == 1]
    normal = df[df["isFraud"] == 0]
    return {
        "total": len(df),
        "fraud": len(fraud),
        "fraud_rate": f"{len(fraud)/len(df)*100:.2f}%",
        "fraud_avg_amount": f"{fraud['amount'].mean():,.0f}",
        "normal_avg_amount": f"{normal['amount'].mean():,.0f}",
        "types": df["type"].value_counts().to_dict(),
        "source": get_source_label(),
    }


if __name__ == "__main__":
    df = load_data(5000)
    s = stats(df)
    print(f"数据加载完成: {s['total']} 条")
    print(f"欺诈: {s['fraud']} 条 ({s['fraud_rate']})")
    print(f"来源: {s['source']}")
    print(f"交易类型: {s['types']}")
