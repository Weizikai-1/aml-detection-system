"""
五阶段自动优化闭环 端到端运行示例

用途:
- 验证 OptimizationLoop 的端到端可工作性
- 产生真实的运行产物（data/optimization_loop/LOOP-*.json）
- 演示如何串联数据→评估→反馈→调参→验证五阶段

戒律:
- M1: 使用真实构造的交易数据（非编造指标）
- M4: 全过程持久化，可追溯
- P4: 非破坏性，只产生推荐，不自动应用
"""
import os
import sys
from datetime import datetime

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from graph.state import Transaction  # noqa: E402
from tools.optimization_loop import OptimizationLoop  # noqa: E402
from tools.rule_tuner import RuleTuner  # noqa: E402


def _make_txn(
    tid: str,
    from_acc: str,
    to_acc: str,
    amount: float,
    timestamp: str,
    remark: str = "",
) -> Transaction:
    return {
        "transaction_id": tid,
        "from_account": from_acc,
        "to_account": to_acc,
        "amount": amount,
        "timestamp": timestamp,
        "transaction_type": "transfer",
        "remark": remark,
    }


def build_sample_transactions() -> list:
    """构造覆盖多种可疑模式的交易数据集"""
    txns = []

    # 1. 分拆转账：6 笔 4.5 万转账，同收款人，1 小时内（真实可疑）
    for i in range(6):
        txns.append(_make_txn(
            f"SMURF_{i}", f"PAYER_{i}", "RECV_A", 45000.0,
            f"2026-07-01T10:{i:02d}:00"
        ))

    # 2. 大额交易：2 笔（真实可疑）
    txns.append(_make_txn("LARGE_1", "ACC_X", "ACC_Y", 200000.0, "2026-07-01T11:00:00"))
    txns.append(_make_txn("LARGE_2", "ACC_Z", "ACC_W", 350000.0, "2026-07-01T12:00:00"))

    # 3. 快进快出：1 笔（真实可疑）
    txns.append(_make_txn("FIFO_IN", "ACC_IN", "ACC_MID", 50000.0, "2026-07-01T13:00:00"))
    txns.append(_make_txn(
        "FIFO_OUT", "ACC_MID", "ACC_OUT", 49500.0,
        "2026-07-01T13:08:00"  # 8 分钟后，金额 99% 匹配
    ))

    # 4. 正常交易（不应命中）
    txns.append(_make_txn("NORMAL_1", "ACC_A", "ACC_B", 5000.0, "2026-07-01T14:00:00"))
    txns.append(_make_txn("NORMAL_2", "ACC_C", "ACC_D", 8000.0, "2026-07-01T15:00:00"))

    return txns


def build_sample_ground_truth(txns: list) -> dict:
    """构造真值集：标记每笔交易是否可疑"""
    suspicious_ids = {
        "SMURF_0", "SMURF_1", "SMURF_2", "SMURF_3", "SMURF_4", "SMURF_5",
        "LARGE_1", "LARGE_2",
        "FIFO_IN", "FIFO_OUT",
    }
    return {t["transaction_id"]: (t["transaction_id"] in suspicious_ids) for t in txns}


def main():
    print("=" * 70)
    print("五阶段自动优化闭环 端到端运行示例")
    print("=" * 70)
    print()

    # ===== 准备数据 =====
    txns = build_sample_transactions()
    gt = build_sample_ground_truth(txns)

    print(f"[数据准备] 交易数: {len(txns)}, 真值集数: {len(gt)}")
    suspicious_count = sum(1 for v in gt.values() if v is True)
    print(f"[数据准备] 可疑交易: {suspicious_count} 笔, 正常交易: {len(gt) - suspicious_count} 笔")
    print()

    # ===== 当前参数（来自 RuleTuner 默认） =====
    tuner = RuleTuner()
    current_params = tuner.get_defaults()
    print(f"[当前参数] 参数组: {list(current_params.keys())}")
    print()

    # ===== 运行五阶段闭环 =====
    print("[启动] 五阶段自动优化闭环...")
    loop = OptimizationLoop()
    result = loop.run_loop(
        transactions=txns,
        ground_truth=gt,
        current_params=current_params,
        dataset_name="demo_dataset_v1",
    )

    # ===== 输出结果 =====
    print()
    print("=" * 70)
    print(f"闭环 ID: {result.loop_id}")
    print(f"时间戳: {result.timestamp}")
    print(f"数据集: {result.metadata.get('dataset_name', '')}")
    print("=" * 70)

    # Stage 1
    s1 = result.stages["stage1_data_collection"]
    print()
    print("[Stage 1: 数据收集]")
    print(f"  交易数: {s1['data_summary']['transaction_count']}")
    print(f"  真值集数: {s1['data_summary']['ground_truth_count']}")
    print(f"  可疑: {s1['data_summary']['ground_truth_suspicious']}, "
          f"正常: {s1['data_summary']['ground_truth_normal']}, "
          f"待定: {s1['data_summary']['ground_truth_pending']}")
    if s1["data_warnings"]:
        for w in s1["data_warnings"]:
            print(f"  ⚠ 警告: {w}")

    # Stage 2
    s2 = result.stages["stage2_current_evaluation"]
    cm = s2["current_metrics"]
    print()
    print("[Stage 2: 当前参数评估]")
    print(f"  Precision: {cm.get('precision', 0):.4f}")
    print(f"  Recall:    {cm.get('recall', 0):.4f}")
    print(f"  F1:        {cm.get('f1', 0):.4f}")
    print(f"  TP={cm.get('tp', 0)}, FP={cm.get('fp', 0)}, FN={cm.get('fn', 0)}, TN={cm.get('tn', 0)}")
    print(f"  总命中: {cm.get('total_hits', 0)}")

    # Stage 3
    s3 = result.stages["stage3_feedback_weights"]
    print()
    print("[Stage 3: 反馈调权]")
    print(f"  基础权重: {s3['base_weights']}")
    print(f"  调整后权重: {s3['adjusted_weights']}")
    print(f"  偏移: {s3['weight_shift']:+.4f} ({s3['shift_direction']})")
    print(f"  原因: {s3['shift_reason']}")
    stats = s3["feedback_stats"]
    print(f"  加权误报: {stats['weighted_false_positive']}, "
          f"加权漏报: {stats['weighted_false_negative']}, "
          f"加权确认: {stats['weighted_confirmed']}")

    # Stage 4
    s4 = result.stages["stage4_parameter_tuning"]
    print()
    print("[Stage 4: 调参]")
    print(f"  与当前参数相同: {s4['same_as_current']}")
    if s4["best_candidate_params"]:
        print(f"  候选参数组: {list(s4['best_candidate_params'].keys())}")
        # 显示与当前参数的差异
        for group, params in s4["best_candidate_params"].items():
            curr_group = current_params.get(group, {})
            for k, v in params.items():
                old_v = curr_group.get(k)
                if old_v != v:
                    print(f"  差异: {group}.{k}: {old_v} → {v}")
    if s4["cross_impact_result"]:
        ci = s4["cross_impact_result"]
        print(f"  交叉影响分析 ID: {ci.get('analysis_id', '')}")
        print(f"  强影响数量: {len(ci.get('strong_impacts', []))}")
    if s4["tuning_warnings"]:
        for w in s4["tuning_warnings"]:
            print(f"  ⚠ 警告: {w}")

    # Stage 5
    s5 = result.stages["stage5_validation"]
    print()
    print("[Stage 5: 验证]")
    if s5["ab_test"]:
        ab = s5["ab_test"]
        print(f"  A/B 测试 ID: {ab.get('test_id', '')}")
        dec = ab.get("decision", {})
        print(f"  A/B 决策: {dec.get('recommendation', '')}")
        print(f"  决策理由: {dec.get('reason', '')}")
        if dec.get("guardrail_violations"):
            for v in dec["guardrail_violations"]:
                print(f"  ✗ 戒律违反: {v}")
        if dec.get("guardrail_warnings"):
            for w in dec["guardrail_warnings"]:
                print(f"  ⚠ 戒律警告: {w}")
    else:
        print("  无 A/B 测试（候选参数为空或与当前相同）")

    if s5["invariant_check"]:
        inv = s5["invariant_check"]
        print(f"  不变量检查: passed={inv.get('passed')}")
        if inv.get("violations"):
            for v in inv["violations"]:
                print(f"    - [{v.get('severity')}] {v.get('detail', '')}")

    # 推荐
    rec = result.recommendation
    print()
    print("=" * 70)
    print(f"[最终推荐] action = {rec['action'].upper()}")
    print(f"  理由: {rec['reason']}")
    if rec.get("expected_improvement"):
        print("  预期改进:")
        for metric, change in rec["expected_improvement"].items():
            delta = change["delta"]
            arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
            print(f"    {metric}: {change['current']:.4f} → {change['candidate']:.4f} ({arrow} {delta:+.4f})")
    print("=" * 70)
    print()

    # ===== 验证持久化 =====
    loop_id = result.loop_id
    fetched = loop.get_loop(loop_id)
    if fetched and fetched.loop_id == loop_id:
        print(f"[持久化验证] ✓ 闭环结果已成功保存并可读取 (loop_id={loop_id})")
    else:
        print(f"[持久化验证] ✗ 闭环结果读取失败")
        sys.exit(1)

    loops_list = loop.list_loops()
    print(f"[持久化验证] ✓ 索引包含 {len(loops_list)} 条闭环记录")

    # ===== 输出文件路径 =====
    file_path = os.path.join(loop.storage_dir, f"{loop_id}.json")
    if os.path.exists(file_path):
        file_size = os.path.getsize(file_path)
        print(f"[持久化验证] ✓ 文件: {file_path} ({file_size} bytes)")

    print()
    print("=" * 70)
    print("端到端运行成功！五阶段自动优化闭环工作正常。")
    print("=" * 70)


if __name__ == "__main__":
    main()
