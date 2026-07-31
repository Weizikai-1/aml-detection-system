"""
评估脚本 — 评估先行原则的核心
一键运行: python evaluate.py
输出: 规则引擎 vs GNN vs 随机基线 三重对比
"""
import sys
import os
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_loader import load_data, get_source_label
from rules import ALL_RULES, CORE_RULES
from rule_engine import run_engine, summary as rule_summary
from settings import RANDOM_SEED

np.random.seed(RANDOM_SEED)
RANDOM_BASELINE_FRAUD_RATE = 0.0013  # PaySim 真实欺诈率 ~0.13%


def calc_metrics(pred: np.ndarray, true: np.ndarray) -> dict:
    tp = int((pred & true).sum())
    fp = int((pred & (1 - true)).sum())
    fn = int(((1 - pred) & true).sum())
    tn = int(((1 - pred) & (1 - true)).sum())
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return {"precision": p, "recall": r, "f1": f, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def evaluate_rules(df, labels, rules_subset=None, name="规则引擎"):
    df = df.assign(_idx=range(len(df)))
    records = df.to_dict("records")
    hits = run_engine(records, rules_subset)
    # 标记命中的交易
    hit_ids = set()
    for h in hits:
        hit_ids.add(h["transaction"].get("_idx", -1))
    preds = np.array([1 if i in hit_ids else 0 for i in range(len(df))])
    m = calc_metrics(preds, labels)
    print(f"\n{'─' * 50}")
    print(f"  {name}: {rule_summary(hits)}")
    print(f"  Precision={m['precision']:.4f}  Recall={m['recall']:.4f}  F1={m['f1']:.4f}")
    print(f"  TP={m['tp']}  FP={m['fp']}  FN={m['fn']}  TN={m['tn']}")
    return m


def evaluate_random_baseline(labels):
    preds = np.random.binomial(1, RANDOM_BASELINE_FRAUD_RATE, len(labels))
    return calc_metrics(preds, labels)


def evaluate_gnn(df, labels):
    """GNN 评估"""
    m = {"precision": 0, "recall": 0, "f1": 0}
    try:
        from gnn_model import build_graph, train_and_eval, predict_transactions, is_available
        if not is_available():
            print("\n  GNN: PyTorch Geometric 未安装，跳过")
            return m
        print("\n  训练 GAT 模型...")
        data = build_graph(df)
        result = train_and_eval(data, epochs=60)
        m = {"precision": result["node_precision"],
             "recall": result["node_recall"],
             "f1": result["node_f1"]}
        print(f"  GNN 节点级: P={m['precision']:.4f} R={m['recall']:.4f} F1={m['f1']:.4f}")
        # 交易级预测
        txn_preds = predict_transactions(result["model"], data, df)
        txn_m = calc_metrics(txn_preds, labels)
        print(f"  GNN 交易级: P={txn_m['precision']:.4f} R={txn_m['recall']:.4f} F1={txn_m['f1']:.4f}")
        m = txn_m
    except Exception as e:
        print(f"\n  GNN 评估失败: {e}")
    return m


def main():
    print("=" * 60)
    print("  AML 反洗钱检测系统 — 评估报告")
    print("=" * 60)
    print(f"  数据来源: {get_source_label()}")
    print(f"  随机种子: {RANDOM_SEED}")

    # 1. 加载数据
    df = load_data(5000)
    labels = df["isFraud"].values.astype(int)
    n_fraud = int(labels.sum())
    print(f"\n  总交易: {len(df):,}  欺诈: {n_fraud:,} ({n_fraud/len(df)*100:.2f}%)")

    # 2. 随机基线
    rand = evaluate_random_baseline(labels)
    print(f"\n  随机基线: P={rand['precision']:.4f} R={rand['recall']:.4f} F1={rand['f1']:.4f}")

    # 3. 规则引擎 - 核心4条
    rules_all = evaluate_rules(df, labels, None, "规则引擎-全部10条")
    rules_core = evaluate_rules(df, labels, CORE_RULES, "规则引擎-核心4条")

    # 4. GNN
    gnn = evaluate_gnn(df, labels)

    # 5. 总结
    print(f"\n{'=' * 60}")
    print(f"  评估总结")
    print(f"  {'─' * 45}")
    print(f"  {'方法':<20s} {'Precision':>8s} {'Recall':>8s} {'F1':>8s}")
    print(f"  {'─' * 45}")
    for name, m in [("随机基线", rand), ("规则引擎-全部", rules_all),
                     ("规则引擎-核心", rules_core), ("GNN-交易级", gnn)]:
        print(f"  {name:<20s} {m['precision']:>8.4f} {m['recall']:>8.4f} {m['f1']:>8.4f}")
    print(f"  {'─' * 45}")

    # 诚实声明
    from settings import PAYSIM_CSV, PAYSIM_SAMPLE
    has_real = os.path.exists(PAYSIM_CSV) or os.path.exists(PAYSIM_SAMPLE)
    print(f"\n  数据来源: {'Kaggle PaySim 真实数据集' if has_real else 'PaySim 格式模拟数据'}")
    if not has_real:
        print("  ⚠ 诚实声明: 模拟数据仅验证代码逻辑。")
        print("    接入 Kaggle PaySim 后评估结果才具参考价值。")
    print("=" * 60)


if __name__ == "__main__":
    main()
