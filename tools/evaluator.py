"""
评估器 (Evaluator)

职责:
- 计算系统预测结果与真值集的差异
- 输出 Precision / Recall / F1 / 混淆矩阵
- 支持规则级评估和阈值调参分析
- 评估结果持久化，支持历史对比

设计原则:
- M1: 评估基于真实真值数据，不编造
- P1/P2: 同时关注召回率和准确率，避免只看单一指标
- 只评估 is_suspicious 明确的记录（跳过 pending）
"""
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from config import EVALUATIONS_DIR
from tools.ground_truth_builder import GroundTruthDataset, load_latest_ground_truth


# ============================================================
# 评估结果数据结构
# ============================================================

@dataclass
class ConfusionMatrix:
    tp: int = 0  # 真阳性: 预测可疑，实际可疑
    fp: int = 0  # 假阳性: 预测可疑，实际正常
    tn: int = 0  # 真阴性: 预测正常，实际正常
    fn: int = 0  # 假阴性: 预测正常，实际可疑

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    @property
    def precision(self) -> float:
        """精确率 = TP / (TP + FP)"""
        denom = self.tp + self.fp
        return round(self.tp / denom, 4) if denom > 0 else 0.0

    @property
    def recall(self) -> float:
        """召回率 = TP / (TP + FN)"""
        denom = self.tp + self.fn
        return round(self.tp / denom, 4) if denom > 0 else 0.0

    @property
    def f1_score(self) -> float:
        """F1 = 2 * P * R / (P + R)"""
        p, r = self.precision, self.recall
        return round(2 * p * r / (p + r), 4) if (p + r) > 0 else 0.0

    @property
    def accuracy(self) -> float:
        """准确率 = (TP + TN) / Total"""
        return round((self.tp + self.tn) / self.total, 4) if self.total > 0 else 0.0

    @property
    def specificity(self) -> float:
        """特异度 = TN / (TN + FP)"""
        denom = self.tn + self.fp
        return round(self.tn / denom, 4) if denom > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "tn": self.tn,
            "fn": self.fn,
            "total": self.total,
            "precision": self.precision,
            "recall": self.recall,
            "f1_score": self.f1_score,
            "accuracy": self.accuracy,
            "specificity": self.specificity,
        }


@dataclass
class RuleEvaluation:
    """单条规则的评估结果"""
    rule_name: str
    matrix: ConfusionMatrix = field(default_factory=ConfusionMatrix)

    def to_dict(self) -> dict:
        return {
            "rule_name": self.rule_name,
            "matrix": self.matrix.to_dict(),
        }


@dataclass
class ThresholdScan:
    """阈值扫描结果"""
    threshold: float
    matrix: ConfusionMatrix = field(default_factory=ConfusionMatrix)

    def to_dict(self) -> dict:
        return {
            "threshold": self.threshold,
            "matrix": self.matrix.to_dict(),
        }


@dataclass
class EvaluationResult:
    """完整评估结果"""
    eval_id: str
    eval_time: str
    ground_truth_name: str
    ground_truth_version: str
    total_evaluated: int
    pending_skipped: int
    overall: ConfusionMatrix = field(default_factory=ConfusionMatrix)
    by_rule: Dict[str, RuleEvaluation] = field(default_factory=dict)
    threshold_scan: List[ThresholdScan] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "eval_id": self.eval_id,
            "eval_time": self.eval_time,
            "ground_truth_name": self.ground_truth_name,
            "ground_truth_version": self.ground_truth_version,
            "total_evaluated": self.total_evaluated,
            "pending_skipped": self.pending_skipped,
            "overall": self.overall.to_dict(),
            "by_rule": {k: v.to_dict() for k, v in self.by_rule.items()},
            "threshold_scan": [t.to_dict() for t in self.threshold_scan],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvaluationResult":
        result = cls(
            eval_id=data.get("eval_id", ""),
            eval_time=data.get("eval_time", ""),
            ground_truth_name=data.get("ground_truth_name", ""),
            ground_truth_version=data.get("ground_truth_version", ""),
            total_evaluated=data.get("total_evaluated", 0),
            pending_skipped=data.get("pending_skipped", 0),
            metadata=data.get("metadata", {}),
        )
        if "overall" in data:
            o = data["overall"]
            result.overall = ConfusionMatrix(
                tp=o.get("tp", 0),
                fp=o.get("fp", 0),
                tn=o.get("tn", 0),
                fn=o.get("fn", 0),
            )
        for rule_name, rd in data.get("by_rule", {}).items():
            m = rd.get("matrix", {})
            result.by_rule[rule_name] = RuleEvaluation(
                rule_name=rule_name,
                matrix=ConfusionMatrix(
                    tp=m.get("tp", 0),
                    fp=m.get("fp", 0),
                    tn=m.get("tn", 0),
                    fn=m.get("fn", 0),
                ),
            )
        for td in data.get("threshold_scan", []):
            m = td.get("matrix", {})
            result.threshold_scan.append(ThresholdScan(
                threshold=td.get("threshold", 0),
                matrix=ConfusionMatrix(
                    tp=m.get("tp", 0),
                    fp=m.get("fp", 0),
                    tn=m.get("tn", 0),
                    fn=m.get("fn", 0),
                ),
            ))
        return result

    def save(self, filepath: str):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, filepath: str) -> "EvaluationResult":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


# ============================================================
# 核心评估逻辑
# ============================================================

def _extract_predicted_ids(predictions: List[dict]) -> set:
    """从预测结果中提取预测为可疑的交易ID集合"""
    predicted_ids = set()
    for p in predictions:
        tid = p.get("transaction_id")
        if tid is None and "transaction" in p:
            tid = p["transaction"].get("transaction_id")
        if tid:
            predicted_ids.add(tid)
    return predicted_ids


def evaluate_predictions(
    ground_truth: GroundTruthDataset,
    predictions: List[dict],
    prediction_rule_field: str = "rule_hits",
    prediction_score_field: str = "risk_score",
    score_threshold: float = 0.0,
    scan_thresholds: List[float] = None,
) -> EvaluationResult:
    """
    评估系统预测结果

    Args:
        ground_truth: 真值数据集
        predictions: 系统预测结果列表（SuspiciousTransaction 列表或简化 dict 列表）
        prediction_rule_field: 预测结果中规则命中的字段名
        prediction_score_field: 预测结果中风险分的字段名
        score_threshold: 判定为可疑的分数阈值（0-100）
        scan_thresholds: 阈值扫描列表（如 [30, 40, 50, 60, 70]）

    Returns:
        EvaluationResult
    """
    eval_id = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # 筛选有效真值记录（排除 pending）
    valid_gt = {
        tid: rec for tid, rec in ground_truth.records.items()
        if rec.is_suspicious is not None
    }
    pending_skipped = len(ground_truth.records) - len(valid_gt)

    # 提取预测集合
    predicted_ids = _extract_predicted_ids(predictions)

    # 计算总体混淆矩阵
    overall = ConfusionMatrix()
    for tid, rec in valid_gt.items():
        actual = rec.is_suspicious  # True/False
        predicted = tid in predicted_ids
        if actual and predicted:
            overall.tp += 1
        elif not actual and predicted:
            overall.fp += 1
        elif not actual and not predicted:
            overall.tn += 1
        elif actual and not predicted:
            overall.fn += 1

    result = EvaluationResult(
        eval_id=eval_id,
        eval_time=datetime.now().isoformat(),
        ground_truth_name=ground_truth.name,
        ground_truth_version=ground_truth.version,
        total_evaluated=len(valid_gt),
        pending_skipped=pending_skipped,
        overall=overall,
    )

    # 规则级评估
    # 从 predictions 中提取每条规则命中的交易
    rule_predictions: Dict[str, set] = {}
    for p in predictions:
        tid = p.get("transaction_id")
        if tid is None and "transaction" in p:
            tid = p["transaction"].get("transaction_id")
        if not tid:
            continue
        rules = []
        if prediction_rule_field in p:
            rule_val = p[prediction_rule_field]
            if isinstance(rule_val, list):
                rules = rule_val
            elif isinstance(rule_val, str):
                rules = [rule_val]
        for rule in rules:
            rule_predictions.setdefault(rule, set()).add(tid)

    # 对每个规则单独计算混淆矩阵
    # 注意：规则级评估的"预测"只考虑该规则命中的交易
    for rule_name, rule_pred_ids in rule_predictions.items():
        rule_matrix = ConfusionMatrix()
        for tid, rec in valid_gt.items():
            actual = rec.is_suspicious
            predicted = tid in rule_pred_ids
            if actual and predicted:
                rule_matrix.tp += 1
            elif not actual and predicted:
                rule_matrix.fp += 1
            elif not actual and not predicted:
                rule_matrix.tn += 1
            elif actual and not predicted:
                rule_matrix.fn += 1
        result.by_rule[rule_name] = RuleEvaluation(
            rule_name=rule_name,
            matrix=rule_matrix,
        )

    # 阈值扫描
    if scan_thresholds:
        # 需要 predictions 中有 risk_score
        scored_predictions = []
        for p in predictions:
            tid = p.get("transaction_id")
            if tid is None and "transaction" in p:
                tid = p["transaction"].get("transaction_id")
            score = p.get(prediction_score_field, 0)
            if isinstance(score, (int, float)) and tid:
                scored_predictions.append((tid, float(score)))

        for thresh in scan_thresholds:
            thresh_ids = {tid for tid, score in scored_predictions if score >= thresh}
            scan_matrix = ConfusionMatrix()
            for tid, rec in valid_gt.items():
                actual = rec.is_suspicious
                predicted = tid in thresh_ids
                if actual and predicted:
                    scan_matrix.tp += 1
                elif not actual and predicted:
                    scan_matrix.fp += 1
                elif not actual and not predicted:
                    scan_matrix.tn += 1
                elif actual and not predicted:
                    scan_matrix.fn += 1
            result.threshold_scan.append(ThresholdScan(
                threshold=thresh,
                matrix=scan_matrix,
            ))

    return result


def evaluate_workflow_state(
    ground_truth: GroundTruthDataset,
    state: dict,
    scan_thresholds: List[float] = None,
) -> EvaluationResult:
    """
    直接从工作流 state 评估（便利函数）

    优先使用 llm_reviewed， fallback 到 rule_hits
    """
    predictions = state.get("llm_reviewed", [])
    if not predictions:
        predictions = state.get("rule_hits", [])

    return evaluate_predictions(
        ground_truth=ground_truth,
        predictions=predictions,
        scan_thresholds=scan_thresholds,
    )


# ============================================================
# 报告与持久化
# ============================================================

def format_evaluation_report(result: EvaluationResult) -> str:
    """格式化评估报告为 Markdown"""
    lines = []
    lines.append("# 反洗钱系统评估报告")
    lines.append("")
    lines.append(f"- **评估ID**: `{result.eval_id}`")
    lines.append(f"- **评估时间**: {result.eval_time}")
    lines.append(f"- **真值集**: {result.ground_truth_name} (v{result.ground_truth_version})")
    lines.append(f"- **评估样本数**: {result.total_evaluated} (跳过 {result.pending_skipped} 条待定)")
    lines.append("")

    # 总体指标
    o = result.overall
    lines.append("## 总体指标")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| Precision (精确率) | {o.precision} |")
    lines.append(f"| Recall (召回率) | {o.recall} |")
    lines.append(f"| F1 Score | {o.f1_score} |")
    lines.append(f"| Accuracy (准确率) | {o.accuracy} |")
    lines.append(f"| Specificity (特异度) | {o.specificity} |")
    lines.append("")

    # 混淆矩阵
    lines.append("## 混淆矩阵")
    lines.append("")
    lines.append("| 实际 \\ 预测 | 预测可疑 | 预测正常 |")
    lines.append("|------------|----------|----------|")
    lines.append(f"| **实际可疑** | TP={o.tp} | FN={o.fn} |")
    lines.append(f"| **实际正常** | FP={o.fp} | TN={o.tn} |")
    lines.append("")

    # 规则级评估
    if result.by_rule:
        lines.append("## 规则级评估")
        lines.append("")
        lines.append("| 规则 | Precision | Recall | F1 | TP | FP | FN |")
        lines.append("|------|-----------|--------|----|----|----|----|")
        for rule_name, rev in sorted(result.by_rule.items()):
            m = rev.matrix
            lines.append(
                f"| {rule_name} | {m.precision} | {m.recall} | {m.f1_score} | "
                f"{m.tp} | {m.fp} | {m.fn} |"
            )
        lines.append("")

    # 阈值扫描
    if result.threshold_scan:
        lines.append("## 阈值扫描")
        lines.append("")
        lines.append("| 阈值 | Precision | Recall | F1 | Accuracy |")
        lines.append("|------|-----------|--------|----|----------|")
        for ts in sorted(result.threshold_scan, key=lambda x: x.threshold):
            m = ts.matrix
            lines.append(
                f"| {ts.threshold} | {m.precision} | {m.recall} | {m.f1_score} | {m.accuracy} |"
            )
        lines.append("")

    return "\n".join(lines)


def save_evaluation(result: EvaluationResult, name: str = None) -> str:
    """保存评估结果到文件"""
    os.makedirs(EVALUATIONS_DIR, exist_ok=True)
    filename = f"{name or result.eval_id}.json"
    filepath = os.path.join(EVALUATIONS_DIR, filename)
    result.save(filepath)

    # 同时保存 Markdown 报告
    md_filepath = filepath.replace(".json", ".md")
    with open(md_filepath, "w", encoding="utf-8") as f:
        f.write(format_evaluation_report(result))

    print(f"[评估] 结果已保存到 {filepath}")
    print(f"[评估] 报告已保存到 {md_filepath}")
    return filepath


def list_evaluations() -> List[dict]:
    """列出所有评估结果"""
    if not os.path.exists(EVALUATIONS_DIR):
        return []

    results = []
    for f in sorted(os.listdir(EVALUATIONS_DIR)):
        if not f.endswith(".json"):
            continue
        filepath = os.path.join(EVALUATIONS_DIR, f)
        try:
            ev = EvaluationResult.load(filepath)
            results.append({
                "filename": f,
                "eval_id": ev.eval_id,
                "eval_time": ev.eval_time,
                "ground_truth_name": ev.ground_truth_name,
                "overall_f1": ev.overall.f1_score,
                "overall_precision": ev.overall.precision,
                "overall_recall": ev.overall.recall,
            })
        except Exception as e:
            results.append({"filename": f, "error": str(e)})
    return results


# ============================================================
# 快捷入口
# ============================================================

def quick_evaluate(
    predictions: List[dict],
    ground_truth: GroundTruthDataset = None,
    scan_thresholds: List[float] = None,
    save: bool = True,
) -> EvaluationResult:
    """
    快速评估入口：自动加载最新真值集并评估
    """
    if ground_truth is None:
        ground_truth = load_latest_ground_truth()
        if ground_truth is None:
            raise FileNotFoundError("未找到真值集，请先运行 ground_truth_builder 构建真值集")

    result = evaluate_predictions(
        ground_truth=ground_truth,
        predictions=predictions,
        scan_thresholds=scan_thresholds or [30, 40, 50, 60, 70, 80],
    )

    if save:
        save_evaluation(result)

    return result


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="评估器")
    subparsers = parser.add_subparsers(dest="command")

    # list 子命令
    subparsers.add_parser("list", help="列出所有评估结果")

    # report 子命令
    report_parser = subparsers.add_parser("report", help="查看指定评估报告")
    report_parser.add_argument("--eval-id", type=str, required=True, help="评估ID")

    args = parser.parse_args()

    if args.command == "list":
        evs = list_evaluations()
        print(f"共 {len(evs)} 条评估记录:")
        for ev in evs:
            if "error" in ev:
                print(f"  {ev['filename']} [错误: {ev['error']}]")
            else:
                print(
                    f"  {ev['eval_id']}: F1={ev['overall_f1']} "
                    f"P={ev['overall_precision']} R={ev['overall_recall']} "
                    f"({ev['ground_truth_name']})"
                )
    elif args.command == "report":
        filepath = os.path.join(EVALUATIONS_DIR, f"{args.eval_id}.json")
        if not os.path.exists(filepath):
            print(f"未找到评估结果: {filepath}")
        else:
            ev = EvaluationResult.load(filepath)
            print(format_evaluation_report(ev))
    else:
        parser.print_help()
