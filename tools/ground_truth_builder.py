"""
真值集构建工具 (Ground Truth Builder)

职责:
- 基于业务规则自动生成带标注的真值交易数据
- 支持半自动标注（规则自动标注 + 人工审核修正）
- 输出标准化真值集格式，供评估器使用

设计原则:
- M1: 真值基于真实交易数据和明确业务规则，不臆测
- P2: 真值标注保守，不确定的标记为待定（needs_review）
- 支持增量构建，可不断扩充真值集
"""
import json
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from config import GROUND_TRUTH_DIR


# ============================================================
# 真值集数据结构
# ============================================================

def _now_str() -> str:
    return datetime.now().isoformat()


class GroundTruthRecord:
    """
    单条真值记录

    字段:
        transaction_id: 交易ID
        is_suspicious: True/False/None（None表示待定需人工审核）
        suspicious_reasons: 可疑原因列表（规则触发原因）
        labels: 人工审核标签（覆盖自动标注）
        review_status: auto / reviewed / pending
        reviewer: 审核人
        review_time: 审核时间
        notes: 备注
    """

    def __init__(self, transaction_id: str, is_suspicious: Optional[bool],
                 suspicious_reasons: List[str] = None, labels: List[str] = None,
                 review_status: str = "auto", reviewer: str = "",
                 review_time: str = "", notes: str = ""):
        self.transaction_id = transaction_id
        self.is_suspicious = is_suspicious
        self.suspicious_reasons = suspicious_reasons or []
        self.labels = labels or []
        self.review_status = review_status
        self.reviewer = reviewer
        self.review_time = review_time
        self.notes = notes

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "is_suspicious": self.is_suspicious,
            "suspicious_reasons": self.suspicious_reasons,
            "labels": self.labels,
            "review_status": self.review_status,
            "reviewer": self.reviewer,
            "review_time": self.review_time,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GroundTruthRecord":
        return cls(
            transaction_id=data.get("transaction_id", ""),
            is_suspicious=data.get("is_suspicious"),
            suspicious_reasons=data.get("suspicious_reasons", []),
            labels=data.get("labels", []),
            review_status=data.get("review_status", "auto"),
            reviewer=data.get("reviewer", ""),
            review_time=data.get("review_time", ""),
            notes=data.get("notes", ""),
        )


class GroundTruthDataset:
    """
    真值数据集

    包含:
        records: Dict[transaction_id, GroundTruthRecord]
        metadata: 数据集元信息
    """

    def __init__(self, name: str = "", description: str = ""):
        self.name = name
        self.description = description
        self.created_at = _now_str()
        self.updated_at = _now_str()
        self.version = "1.0"
        self.records: Dict[str, GroundTruthRecord] = {}
        self.stats: Dict[str, any] = {}

    def add_record(self, record: GroundTruthRecord):
        self.records[record.transaction_id] = record
        self.updated_at = _now_str()

    def get_record(self, transaction_id: str) -> Optional[GroundTruthRecord]:
        return self.records.get(transaction_id)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "records": {tid: r.to_dict() for tid, r in self.records.items()},
            "stats": self._compute_stats(),
        }

    def _compute_stats(self) -> dict:
        total = len(self.records)
        suspicious = sum(1 for r in self.records.values() if r.is_suspicious is True)
        normal = sum(1 for r in self.records.values() if r.is_suspicious is False)
        pending = sum(1 for r in self.records.values() if r.is_suspicious is None)
        reviewed = sum(1 for r in self.records.values() if r.review_status == "reviewed")
        return {
            "total_records": total,
            "suspicious_count": suspicious,
            "normal_count": normal,
            "pending_count": pending,
            "reviewed_count": reviewed,
            "suspicious_ratio": round(suspicious / total, 4) if total > 0 else 0.0,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GroundTruthDataset":
        ds = cls(name=data.get("name", ""), description=data.get("description", ""))
        ds.created_at = data.get("created_at", _now_str())
        ds.updated_at = data.get("updated_at", _now_str())
        ds.version = data.get("version", "1.0")
        for tid, rd in data.get("records", {}).items():
            ds.records[tid] = GroundTruthRecord.from_dict(rd)
        ds.stats = data.get("stats", {})
        return ds

    def save(self, filepath: str):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, filepath: str) -> "GroundTruthDataset":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


# ============================================================
# 自动标注逻辑
# ============================================================

def auto_label_transactions(
    transactions: List[dict],
    conservative: bool = True,
) -> GroundTruthDataset:
    """
    基于交易自带标记自动构建真值集

    Args:
        transactions: 交易列表（需包含 is_suspicious 和 suspicious_reason 字段）
        conservative: 是否保守标注（True: 不确定的标记为None；False: 直接采用标记）

    Returns:
        GroundTruthDataset
    """
    ds = GroundTruthDataset(
        name=f"auto_label_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        description="基于数据生成器自动标注的真值集",
    )

    for txn in transactions:
        tid = txn.get("transaction_id", "")
        if not tid:
            continue

        is_suspicious = txn.get("is_suspicious")
        reason = txn.get("suspicious_reason", "")
        reasons = [reason] if reason else []

        # 保守模式：如果标记不明确，设为待定
        if conservative and is_suspicious is None:
            record = GroundTruthRecord(
                transaction_id=tid,
                is_suspicious=None,
                suspicious_reasons=reasons,
                review_status="pending",
                notes="标记不明确，需人工审核",
            )
        else:
            record = GroundTruthRecord(
                transaction_id=tid,
                is_suspicious=bool(is_suspicious) if is_suspicious is not None else None,
                suspicious_reasons=reasons,
                review_status="auto",
            )

        ds.add_record(record)

    ds.stats = ds._compute_stats()
    return ds


def build_ground_truth_from_generator(
    normal_count: int = 120,
    suspicious_modes: List[str] = None,
    conservative: bool = True,
    save: bool = True,
    name: str = None,
) -> Tuple[GroundTruthDataset, List[dict]]:
    """
    从数据生成器构建真值集（主要入口）

    Args:
        normal_count: 正常交易数量
        suspicious_modes: 可疑模式列表
        conservative: 是否保守标注
        save: 是否保存到文件
        name: 数据集名称（默认自动生成）

    Returns:
        (dataset, transactions)
    """
    from tools.data_generator import generate_test_data

    if suspicious_modes is None:
        suspicious_modes = ["smurfing", "fast_in_fast_out", "round_trip", "large_amount"]

    # 生成数据
    transactions = generate_test_data(
        normal_count=normal_count,
        suspicious_modes=suspicious_modes,
        saved_path=None,
    )

    # 自动标注
    ds = auto_label_transactions(transactions, conservative=conservative)

    if name:
        ds.name = name
    else:
        ds.name = f"gt_v1_{normal_count}n_{len(transactions)-normal_count}s"

    ds.description = (
        f"自动构建真值集: {normal_count}笔正常交易, "
        f"{len(transactions)-normal_count}笔可疑交易"
        f"（模式: {', '.join(suspicious_modes)}）"
    )

    # 保存
    if save:
        os.makedirs(GROUND_TRUTH_DIR, exist_ok=True)
        filepath = os.path.join(GROUND_TRUTH_DIR, f"{ds.name}.json")
        ds.save(filepath)
        # 同时保存原始交易数据，供离线回放使用
        raw_filepath = os.path.join(GROUND_TRUTH_DIR, f"{ds.name}.raw.json")
        with open(raw_filepath, "w", encoding="utf-8") as f:
            json.dump(transactions, f, ensure_ascii=False, indent=2)
        print(f"[真值集] 已保存到 {filepath}")
        print(f"[真值集] 原始数据已保存到 {raw_filepath}")
        print(f"  总记录: {ds.stats['total_records']}")
        print(f"  可疑: {ds.stats['suspicious_count']}")
        print(f"  正常: {ds.stats['normal_count']}")
        print(f"  待定: {ds.stats['pending_count']}")

    return ds, transactions


def load_latest_ground_truth() -> Optional[GroundTruthDataset]:
    """加载最新的真值集文件"""
    if not os.path.exists(GROUND_TRUTH_DIR):
        return None

    # 戒律 M1: 排除 .raw.json 文件（原始交易数据，非真值集）
    files = [
        f for f in os.listdir(GROUND_TRUTH_DIR)
        if f.endswith(".json") and not f.endswith(".raw.json")
    ]
    if not files:
        return None

    # 按修改时间排序
    files.sort(key=lambda f: os.path.getmtime(os.path.join(GROUND_TRUTH_DIR, f)), reverse=True)
    latest = os.path.join(GROUND_TRUTH_DIR, files[0])
    return GroundTruthDataset.load(latest)


def list_ground_truth_datasets() -> List[dict]:
    """列出所有真值集"""
    if not os.path.exists(GROUND_TRUTH_DIR):
        return []

    # 戒律 M1: 排除 .raw.json 文件（原始交易数据，非真值集）
    # 与 load_latest_ground_truth 保持一致
    results = []
    for f in sorted(os.listdir(GROUND_TRUTH_DIR)):
        if not f.endswith(".json") or f.endswith(".raw.json"):
            continue
        filepath = os.path.join(GROUND_TRUTH_DIR, f)
        try:
            ds = GroundTruthDataset.load(filepath)
            results.append({
                "filename": f,
                "name": ds.name,
                "created_at": ds.created_at,
                "stats": ds.stats,
            })
        except Exception as e:
            results.append({
                "filename": f,
                "error": str(e),
            })
    return results


# ============================================================
# 人工审核接口
# ============================================================

def review_transaction(
    dataset: GroundTruthDataset,
    transaction_id: str,
    is_suspicious: bool,
    reviewer: str = "admin",
    notes: str = "",
    labels: List[str] = None,
) -> bool:
    """
    人工审核单条交易真值

    Args:
        dataset: 真值数据集
        transaction_id: 交易ID
        is_suspicious: 人工判定结果
        reviewer: 审核人
        notes: 审核备注
        labels: 人工标签

    Returns:
        是否成功
    """
    record = dataset.get_record(transaction_id)
    if record is None:
        return False

    record.is_suspicious = is_suspicious
    record.review_status = "reviewed"
    record.reviewer = reviewer
    record.review_time = _now_str()
    record.notes = notes
    if labels:
        record.labels = labels

    dataset.updated_at = _now_str()
    dataset.stats = dataset._compute_stats()
    return True


def export_for_review(dataset: GroundTruthDataset, filepath: str):
    """
    导出待定记录供人工审核（CSV格式便于Excel打开）
    """
    import csv

    pending = [r for r in dataset.records.values() if r.is_suspicious is None]
    if not pending:
        print("[真值集] 没有待定记录需要审核")
        return

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["transaction_id", "current_status", "suspicious_reasons", "reviewer", "notes"])
        for r in pending:
            writer.writerow([
                r.transaction_id,
                "pending",
                ";".join(r.suspicious_reasons),
                "",
                r.notes,
            ])
    print(f"[真值集] 已导出 {len(pending)} 条待定记录到 {filepath}")


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="真值集构建工具")
    subparsers = parser.add_subparsers(dest="command")

    # build 子命令
    build_parser = subparsers.add_parser("build", help="构建新真值集")
    build_parser.add_argument("--normal", type=int, default=120, help="正常交易数量")
    build_parser.add_argument("--modes", nargs="+", default=["smurfing", "fast_in_fast_out", "round_trip", "large_amount"], help="可疑模式")
    build_parser.add_argument("--name", type=str, default=None, help="数据集名称")
    build_parser.add_argument("--no-save", action="store_true", help="不保存到文件")

    # list 子命令
    subparsers.add_parser("list", help="列出所有真值集")

    # review-export 子命令
    review_parser = subparsers.add_parser("review-export", help="导出待定记录供审核")
    review_parser.add_argument("--dataset", type=str, required=True, help="真值集文件名")
    review_parser.add_argument("--output", type=str, default="review_pending.csv", help="输出CSV路径")

    args = parser.parse_args()

    if args.command == "build":
        build_ground_truth_from_generator(
            normal_count=args.normal,
            suspicious_modes=args.modes,
            save=not args.no_save,
            name=args.name,
        )
    elif args.command == "list":
        datasets = list_ground_truth_datasets()
        print(f"共 {len(datasets)} 个真值集:")
        for ds in datasets:
            if "error" in ds:
                print(f"  {ds['filename']} [错误: {ds['error']}]")
            else:
                print(f"  {ds['filename']}: {ds['stats']}")
    elif args.command == "review-export":
        filepath = os.path.join(GROUND_TRUTH_DIR, args.dataset)
        ds = GroundTruthDataset.load(filepath)
        export_for_review(ds, args.output)
    else:
        parser.print_help()
