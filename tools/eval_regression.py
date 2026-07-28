"""
回归对比脚本 (Regression Evaluation)

职责:
- 离线回放：用历史真值集运行当前规则引擎，评估当前版本表现
- 增量评估：对比新旧版本的指标差异
- 回归基线：保存基线指标，检测当前是否退化
- 退化报警：F1/Precision/Recall 下降超过阈值时报警

设计原则:
- M1: 基于真实真值数据回放，不编造
- P1/P2: 同时监控召回率和准确率，防止为了修复一个而牺牲另一个
"""
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from config import EVALUATIONS_DIR, AML_CONFIG
from tools.ground_truth_builder import GroundTruthDataset, load_latest_ground_truth
from tools.evaluator import (
    evaluate_predictions,
    EvaluationResult,
    ConfusionMatrix,
    save_evaluation,
    format_evaluation_report,
)


# ============================================================
# 基线管理
# ============================================================

BASELINE_FILE = os.path.join(EVALUATIONS_DIR, "_baseline.json")


class EvaluationBaseline:
    """评估基线管理"""

    def __init__(self):
        self.baseline: Optional[EvaluationResult] = None
        self.created_at: str = ""
        self.notes: str = ""

    def set_baseline(self, result: EvaluationResult, notes: str = ""):
        self.baseline = result
        self.created_at = datetime.now().isoformat()
        self.notes = notes
        self.save()

    def save(self):
        os.makedirs(EVALUATIONS_DIR, exist_ok=True)
        data = {
            "created_at": self.created_at,
            "notes": self.notes,
            "eval_id": self.baseline.eval_id if self.baseline else "",
            "baseline": self.baseline.to_dict() if self.baseline else {},
        }
        with open(BASELINE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls) -> Optional["EvaluationBaseline"]:
        if not os.path.exists(BASELINE_FILE):
            return None
        with open(BASELINE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        eb = cls()
        eb.created_at = data.get("created_at", "")
        eb.notes = data.get("notes", "")
        baseline_dict = data.get("baseline", {})
        if baseline_dict:
            eb.baseline = EvaluationResult.from_dict(baseline_dict)
        return eb


# ============================================================
# 离线回放
# ============================================================

def run_offline_evaluation(
    ground_truth: GroundTruthDataset = None,
    use_llm: bool = False,
    save: bool = True,
    scan_thresholds: List[float] = None,
) -> Tuple[EvaluationResult, Dict]:
    """
    离线回放评估

    用真值集中的交易数据作为输入，运行当前规则引擎，
    然后将预测结果与真值标注对比，计算评估指标。

    Args:
        ground_truth: 真值数据集（None则自动加载最新）
        use_llm: 是否启用LLM深审（默认False，离线评估通常只用规则引擎）
        save: 是否保存评估结果
        scan_thresholds: 阈值扫描列表

    Returns:
        (EvaluationResult, 工作流最终状态)
    """
    if ground_truth is None:
        ground_truth = load_latest_ground_truth()
        if ground_truth is None:
            raise FileNotFoundError("未找到真值集，请先运行 ground_truth_builder 构建真值集")

    print(f"[离线回放] 使用真值集: {ground_truth.name}")
    print(f"  总记录: {ground_truth.stats.get('total_records', 0)}")
    print(f"  可疑: {ground_truth.stats.get('suspicious_count', 0)}")
    print(f"  正常: {ground_truth.stats.get('normal_count', 0)}")

    # 从真值集中重建交易数据
    # 注意：真值集只包含标注信息，不包含完整交易字段
    # 需要从原始数据文件或重新生成数据来重建交易
    transactions = _rebuild_transactions_from_ground_truth(ground_truth)

    if not transactions:
        raise ValueError("无法从真值集重建交易数据，请确保真值集对应的原始数据存在")

    print(f"[离线回放] 重建交易数据: {len(transactions)} 笔")

    # 运行规则引擎
    from agents.rule_engine import create_rule_engine_agent
    from graph.state import AMLState

    init_state: AMLState = {
        "transactions": transactions,
        "cleaned_transactions": transactions,
        "analysis_date": datetime.now().strftime("%Y-%m-%d"),
        "analysis_params": {},
        "messages": [],
        "current_step": "rule_engine",
        "error": "",
        "execution_id": f"offline_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    }

    # 构建账户行为基线（如果有足够历史数据）
    from agents.data_preprocessor import create_data_preprocessor_agent
    preprocessor = create_data_preprocessor_agent()
    preprocessed = preprocessor(init_state)
    init_state.update(preprocessed)

    # 运行规则引擎
    rule_engine = create_rule_engine_agent(llm=None)
    rule_result = rule_engine(init_state)
    init_state.update(rule_result)

    print(f"[离线回放] 规则引擎命中: {rule_result.get('rule_hit_count', 0)} 笔")

    # 如启用LLM，继续运行LLM深审
    final_state = dict(init_state)
    if use_llm:
        print("[离线回放] 启用LLM深审...")
        try:
            from agents.llm_reviewer import create_llm_reviewer_agent
            from tools.llm_client import create_llm
            llm = create_llm()
            if llm is not None:
                llm_reviewer = create_llm_reviewer_agent(llm)
                llm_result = llm_reviewer(init_state)
                final_state.update(llm_result)
                print(f"  LLM确认: {len(llm_result.get('llm_confirmed', []))} 笔")
            else:
                print("  LLM不可用，跳过")
        except Exception as e:
            print(f"  LLM深审失败: {e}")

    # 评估：使用规则引擎结果
    predictions = final_state.get("rule_hits", [])
    result = evaluate_predictions(
        ground_truth=ground_truth,
        predictions=predictions,
        scan_thresholds=scan_thresholds or [30, 40, 50, 60, 70, 80],
    )

    # 添加元数据
    result.metadata = {
        "mode": "offline_replay",
        "use_llm": use_llm,
        "rule_hit_count": rule_result.get("rule_hit_count", 0),
        "rule_details": rule_result.get("rule_details", {}),
        "ground_truth_stats": ground_truth.stats,
    }

    if save:
        save_evaluation(result, name=f"regression_{result.eval_id}")

    return result, final_state


def _rebuild_transactions_from_ground_truth(ground_truth: GroundTruthDataset) -> List[dict]:
    """
    从真值集重建交易数据

    策略：
    1. 查找 data/ground_truth/ 下同名或相关的原始交易数据文件
    2. 如果没有，尝试查找 data/sample_transactions.json
    3. 如果都没有，返回空列表（需要人工提供原始数据）
    """
    from config import DATA_DIR, GROUND_TRUTH_DIR

    # 尝试查找与真值集同名的原始数据文件
    # 真值集文件名如 gt_v1_120n_28s.json，原始数据可能在同一目录或 data/ 下
    gt_name = ground_truth.name

    # 尝试路径：ground_truth 目录下 .raw.json
    raw_candidates = [
        os.path.join(GROUND_TRUTH_DIR, f"{gt_name}.raw.json"),
        os.path.join(GROUND_TRUTH_DIR, f"{gt_name}_transactions.json"),
        os.path.join(DATA_DIR, "sample_transactions.json"),
        os.path.join(DATA_DIR, "transactions.json"),
    ]

    for path in raw_candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                transactions = json.load(f)
            # 验证交易ID是否与真值集匹配
            gt_ids = set(ground_truth.records.keys())
            txn_ids = {t.get("transaction_id", "") for t in transactions}
            if gt_ids.issubset(txn_ids):
                return transactions
            else:
                print(f"  警告: {path} 中的交易ID与真值集不完全匹配，继续查找...")

    # 如果找不到匹配的原始数据，返回空列表
    return []


# ============================================================
# 增量评估与回归检测
# ============================================================

@dataclass
class RegressionDelta:
    """指标变化量"""
    metric: str
    baseline: float
    current: float
    delta: float
    delta_pct: float
    is_degradation: bool


@dataclass
class RegressionReport:
    """回归检测报告"""
    baseline_eval_id: str
    current_eval_id: str
    baseline_time: str
    current_time: str
    deltas: List[RegressionDelta] = field(default_factory=list)
    degraded: List[RegressionDelta] = field(default_factory=list)
    improved: List[RegressionDelta] = field(default_factory=list)
    is_pass: bool = True
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "baseline_eval_id": self.baseline_eval_id,
            "current_eval_id": self.current_eval_id,
            "baseline_time": self.baseline_time,
            "current_time": self.current_time,
            "is_pass": self.is_pass,
            "summary": self.summary,
            "deltas": [
                {
                    "metric": d.metric,
                    "baseline": d.baseline,
                    "current": d.current,
                    "delta": d.delta,
                    "delta_pct": d.delta_pct,
                    "is_degradation": d.is_degradation,
                }
                for d in self.deltas
            ],
            "degraded": [
                {
                    "metric": d.metric,
                    "baseline": d.baseline,
                    "current": d.current,
                    "delta": d.delta,
                    "delta_pct": d.delta_pct,
                }
                for d in self.degraded
            ],
            "improved": [
                {
                    "metric": d.metric,
                    "baseline": d.baseline,
                    "current": d.current,
                    "delta": d.delta,
                    "delta_pct": d.delta_pct,
                }
                for d in self.improved
            ],
        }


def compare_evaluations(
    baseline: EvaluationResult,
    current: EvaluationResult,
    f1_degradation_threshold: float = 0.05,
    pr_degradation_threshold: float = 0.10,
) -> RegressionReport:
    """
    对比两次评估结果，检测是否退化

    Args:
        baseline: 基线评估结果
        current: 当前评估结果
        f1_degradation_threshold: F1下降超过此比例视为退化（默认5%）
        pr_degradation_threshold: Precision/Recall下降超过此比例视为退化（默认10%）

    Returns:
        RegressionReport
    """
    report = RegressionReport(
        baseline_eval_id=baseline.eval_id,
        current_eval_id=current.eval_id,
        baseline_time=baseline.eval_time,
        current_time=current.eval_time,
    )

    # 定义要对比的指标
    metrics = [
        ("precision", baseline.overall.precision, current.overall.precision, pr_degradation_threshold),
        ("recall", baseline.overall.recall, current.overall.recall, pr_degradation_threshold),
        ("f1_score", baseline.overall.f1_score, current.overall.f1_score, f1_degradation_threshold),
        ("accuracy", baseline.overall.accuracy, current.overall.accuracy, pr_degradation_threshold),
        ("specificity", baseline.overall.specificity, current.overall.specificity, pr_degradation_threshold),
    ]

    for metric_name, base_val, curr_val, threshold in metrics:
        delta = curr_val - base_val
        delta_pct = delta / base_val if base_val != 0 else 0.0
        # 退化定义：下降超过阈值（注意是比例下降，不是绝对值）
        is_degradation = delta_pct < -threshold

        rd = RegressionDelta(
            metric=metric_name,
            baseline=base_val,
            current=curr_val,
            delta=delta,
            delta_pct=delta_pct,
            is_degradation=is_degradation,
        )
        report.deltas.append(rd)

        if is_degradation:
            report.degraded.append(rd)
        elif delta_pct > threshold:
            report.improved.append(rd)

    report.is_pass = len(report.degraded) == 0

    if report.is_pass:
        if report.improved:
            report.summary = f"回归通过，{len(report.improved)} 项指标提升"
        else:
            report.summary = "回归通过，指标稳定"
    else:
        degraded_names = [d.metric for d in report.degraded]
        report.summary = f"回归失败: {', '.join(degraded_names)} 出现退化"

    return report


def run_regression_check(
    current_result: EvaluationResult = None,
    ground_truth: GroundTruthDataset = None,
    auto_update_baseline: bool = False,
) -> RegressionReport:
    """
    执行回归检测（主要入口）

    Args:
        current_result: 当前评估结果（None则自动运行离线评估）
        ground_truth: 真值数据集
        auto_update_baseline: 通过后是否自动更新基线

    Returns:
        RegressionReport
    """
    # 加载基线
    baseline_mgr = EvaluationBaseline.load()
    if baseline_mgr is None or baseline_mgr.baseline is None:
        print("[回归检测] 未找到基线，将当前结果设为基线")
        if current_result is None:
            current_result, _ = run_offline_evaluation(ground_truth=ground_truth)
        baseline_mgr = EvaluationBaseline()
        baseline_mgr.set_baseline(current_result, notes="自动创建初始基线")
        return RegressionReport(
            baseline_eval_id=current_result.eval_id,
            current_eval_id=current_result.eval_id,
            baseline_time=current_result.eval_time,
            current_time=current_result.eval_time,
            summary="初始基线已创建，无对比数据",
            is_pass=True,
        )

    baseline = baseline_mgr.baseline

    # 获取当前结果
    if current_result is None:
        current_result, _ = run_offline_evaluation(ground_truth=ground_truth)

    # 对比
    report = compare_evaluations(baseline, current_result)

    print("\n" + "=" * 60)
    print("[回归检测] 结果")
    print("=" * 60)
    print(f"  基线: {baseline.eval_id} ({baseline.eval_time})")
    print(f"  当前: {current_result.eval_id} ({current_result.eval_time})")
    print("")
    for d in report.deltas:
        status = "⚠️ 退化" if d.is_degradation else ("🆙 提升" if d.delta > 0 else "➡️ 持平")
        print(f"  {d.metric}: {d.baseline} -> {d.current} ({d.delta:+.4f}, {d.delta_pct:+.1%}) {status}")
    print("")
    print(f"  结论: {report.summary}")
    print("=" * 60)

    # 保存报告
    os.makedirs(EVALUATIONS_DIR, exist_ok=True)
    report_path = os.path.join(EVALUATIONS_DIR, f"regression_report_{current_result.eval_id}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    print(f"[回归检测] 报告已保存到 {report_path}")

    # 自动更新基线
    if report.is_pass and auto_update_baseline:
        baseline_mgr.set_baseline(current_result, notes="自动更新基线（回归通过）")
        print("[回归检测] 基线已自动更新")

    return report


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="回归对比脚本")
    subparsers = parser.add_subparsers(dest="command")

    # offline 子命令
    offline_parser = subparsers.add_parser("offline", help="运行离线评估")
    offline_parser.add_argument("--use-llm", action="store_true", help="启用LLM深审")
    offline_parser.add_argument("--no-save", action="store_true", help="不保存结果")

    # baseline 子命令
    baseline_parser = subparsers.add_parser("baseline", help="设置当前最新评估为基线")
    baseline_parser.add_argument("--notes", type=str, default="", help="基线备注")

    # regression 子命令
    reg_parser = subparsers.add_parser("regression", help="执行回归检测")
    reg_parser.add_argument("--auto-update", action="store_true", help="通过后自动更新基线")

    # compare 子命令
    compare_parser = subparsers.add_parser("compare", help="对比两次指定评估")
    compare_parser.add_argument("--baseline", type=str, required=True, help="基线评估ID")
    compare_parser.add_argument("--current", type=str, required=True, help="当前评估ID")

    args = parser.parse_args()

    if args.command == "offline":
        result, state = run_offline_evaluation(
            use_llm=args.use_llm,
            save=not args.no_save,
        )
        print("\n" + format_evaluation_report(result))
    elif args.command == "baseline":
        evs = [
            f for f in os.listdir(EVALUATIONS_DIR)
            if f.endswith(".json") and not f.startswith("_")
        ]
        if not evs:
            print("未找到评估结果")
        else:
            # 按修改时间取最新
            evs.sort(key=lambda f: os.path.getmtime(os.path.join(EVALUATIONS_DIR, f)), reverse=True)
            latest = os.path.join(EVALUATIONS_DIR, evs[0])
            result = EvaluationResult.load(latest)
            mgr = EvaluationBaseline()
            mgr.set_baseline(result, notes=args.notes or "手动设置基线")
            print(f"基线已设置: {result.eval_id}")
    elif args.command == "regression":
        report = run_regression_check(auto_update_baseline=args.auto_update)
        print(f"\n回归检测结论: {'通过' if report.is_pass else '失败'}")
    elif args.command == "compare":
        baseline_path = os.path.join(EVALUATIONS_DIR, f"{args.baseline}.json")
        current_path = os.path.join(EVALUATIONS_DIR, f"{args.current}.json")
        if not os.path.exists(baseline_path):
            print(f"未找到基线: {baseline_path}")
        elif not os.path.exists(current_path):
            print(f"未找到当前: {current_path}")
        else:
            baseline = EvaluationResult.load(baseline_path)
            current = EvaluationResult.load(current_path)
            report = compare_evaluations(baseline, current)
            print(f"\n对比结果: {report.summary}")
            for d in report.deltas:
                print(f"  {d.metric}: {d.baseline} -> {d.current} ({d.delta:+.4f})")
    else:
        parser.print_help()
