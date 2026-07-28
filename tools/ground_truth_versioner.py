"""
真值集版本管理器 (Ground Truth Versioner)

职责:
- 为真值集提供版本管理能力（保存版本、列出版本、对比版本、回滚）
- 每次保存创建版本快照，便于追溯变更历史
- 支持版本间差异对比（新增/删除/修改的记录）

戒律遵循:
- M1: 基于真实数据集操作，不编造版本
- M2: 每次版本变更必须标注 description
- M4: 完整记录版本历史，可追溯每次变更
- P4: 版本快照原子写入，避免半截文件

存储结构:
    data/ground_truth/
        ├── <name>.json              # 当前版本（最新）
        ├── <name>.raw.json          # 原始交易数据
        └── versions/
            ├── <name>_v1.json       # 历史版本快照
            ├── <name>_v2.json
            └── <name>_changelog.json # 变更日志
"""
import os
import json
import shutil
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

from config import GROUND_TRUTH_DIR
from tools.ground_truth_builder import GroundTruthDataset


def _now_str() -> str:
    return datetime.now().isoformat()


class GroundTruthVersioner:
    """
    真值集版本管理器

    用法:
        versioner = GroundTruthVersioner()

        # 保存新版本
        version = versioner.save_version(dataset, description="初始版本")

        # 列出所有版本
        versions = versioner.list_versions("my_dataset")

        # 对比两个版本
        diff = versioner.compare_versions("my_dataset", 1, 2)

        # 回滚到指定版本
        versioner.rollback_to_version("my_dataset", 1)
    """

    def __init__(self, base_dir: str = ""):
        """
        Args:
            base_dir: 真值集根目录，默认使用 GROUND_TRUTH_DIR
        """
        self.base_dir = base_dir or GROUND_TRUTH_DIR
        self.versions_dir = os.path.join(self.base_dir, "versions")
        os.makedirs(self.versions_dir, exist_ok=True)

    # ============================================================
    # 路径辅助
    # ============================================================
    def _dataset_path(self, name: str) -> str:
        """当前版本数据集路径"""
        return os.path.join(self.base_dir, f"{name}.json")

    def _version_path(self, name: str, version: int) -> str:
        """历史版本快照路径"""
        return os.path.join(self.versions_dir, f"{name}_v{version}.json")

    def _changelog_path(self, name: str) -> str:
        """变更日志路径"""
        return os.path.join(self.versions_dir, f"{name}_changelog.json")

    # ============================================================
    # 保存版本
    # ============================================================
    def save_version(
        self,
        dataset: GroundTruthDataset,
        description: str = "",
    ) -> int:
        """
        保存数据集为新版本

        戒律:
        - M2: description 建议填写（空则记录"无描述"）
        - M4: 创建版本快照，保留历史
        - P4: 原子写入（先写临时文件再 os.replace）

        Args:
            dataset: 真值数据集
            description: 版本描述

        Returns:
            新版本号
        """
        name = dataset.name
        if not name:
            raise ValueError("数据集名称(name)不能为空")

        # 获取当前最新版本号
        changelog = self._load_changelog(name)
        if changelog["versions"]:
            latest_version = max(v["version"] for v in changelog["versions"])
        else:
            latest_version = 0

        new_version = latest_version + 1

        # 戒律 M4: 如果当前数据集文件已存在，先保存为历史快照
        current_path = self._dataset_path(name)
        if os.path.exists(current_path):
            # 将当前版本保存为历史快照（如果不是首次）
            snapshot_path = self._version_path(name, latest_version) if latest_version > 0 else None
            if snapshot_path and not os.path.exists(snapshot_path):
                try:
                    shutil.copy2(current_path, snapshot_path)
                except OSError as e:
                    raise RuntimeError(f"版本快照保存失败: {e}") from e

        # 计算与上一版本的差异
        changes = self._compute_changes(name, dataset, latest_version)

        # 更新数据集版本字段
        dataset.version = str(new_version)
        dataset.updated_at = _now_str()

        # 戒律 P4: 原子写入当前版本
        tmp_path = current_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(dataset.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, current_path)
        except OSError as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise RuntimeError(f"数据集保存失败: {e}") from e

        # 同时保存新版本快照
        new_snapshot_path = self._version_path(name, new_version)
        tmp_snapshot = new_snapshot_path + ".tmp"
        try:
            with open(tmp_snapshot, "w", encoding="utf-8") as f:
                json.dump(dataset.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp_snapshot, new_snapshot_path)
        except OSError as e:
            try:
                if os.path.exists(tmp_snapshot):
                    os.remove(tmp_snapshot)
            except OSError:
                pass
            raise RuntimeError(f"版本快照保存失败: {e}") from e

        # 更新变更日志
        changelog_entry = {
            "version": new_version,
            "parent_version": latest_version,
            "timestamp": _now_str(),
            "description": description or "无描述",
            "stats": dataset._compute_stats(),
            "changes": changes,
        }
        changelog["dataset_name"] = name
        changelog["versions"].append(changelog_entry)
        changelog["latest_version"] = new_version
        changelog["updated_at"] = _now_str()
        self._save_changelog(name, changelog)

        return new_version

    def _compute_changes(
        self,
        name: str,
        new_dataset: GroundTruthDataset,
        parent_version: int,
    ) -> Dict[str, List[str]]:
        """
        计算与父版本的差异

        Returns:
            {"added": [transaction_ids], "removed": [transaction_ids], "modified": [transaction_ids]}
        """
        changes: Dict[str, List[str]] = {"added": [], "removed": [], "modified": []}

        if parent_version == 0:
            # 首次创建，所有记录都是新增
            changes["added"] = list(new_dataset.records.keys())
            return changes

        # 加载父版本
        parent = self.get_version(name, parent_version)
        if parent is None:
            changes["added"] = list(new_dataset.records.keys())
            return changes

        parent_ids = set(parent.records.keys())
        new_ids = set(new_dataset.records.keys())

        changes["added"] = list(new_ids - parent_ids)
        changes["removed"] = list(parent_ids - new_ids)

        # 检查修改的记录
        for tid in parent_ids & new_ids:
            parent_rec = parent.records[tid]
            new_rec = new_dataset.records[tid]
            if (parent_rec.is_suspicious != new_rec.is_suspicious
                    or parent_rec.review_status != new_rec.review_status):
                changes["modified"].append(tid)

        return changes

    # ============================================================
    # 版本查询
    # ============================================================
    def list_versions(self, name: str) -> List[Dict[str, Any]]:
        """
        列出数据集的所有版本

        Returns:
            版本信息列表（按版本号倒序）
        """
        changelog = self._load_changelog(name)
        versions = sorted(changelog["versions"], key=lambda v: v["version"], reverse=True)
        return versions

    def get_version(self, name: str, version: int) -> Optional[GroundTruthDataset]:
        """获取指定版本的数据集"""
        # 优先从版本快照加载
        snapshot_path = self._version_path(name, version)
        if os.path.exists(snapshot_path):
            try:
                return GroundTruthDataset.load(snapshot_path)
            except (OSError, json.JSONDecodeError):
                pass

        # 如果是最新版本，从当前文件加载
        changelog = self._load_changelog(name)
        if changelog.get("latest_version") == version:
            current_path = self._dataset_path(name)
            if os.path.exists(current_path):
                try:
                    return GroundTruthDataset.load(current_path)
                except (OSError, json.JSONDecodeError):
                    pass

        return None

    def get_latest_version(self, name: str) -> Optional[GroundTruthDataset]:
        """获取最新版本的数据集"""
        changelog = self._load_changelog(name)
        if not changelog["versions"]:
            return None
        latest = changelog.get("latest_version", 0)
        if latest == 0:
            return None
        return self.get_version(name, latest)

    def get_changelog(self, name: str) -> Dict[str, Any]:
        """获取完整变更日志"""
        return self._load_changelog(name)

    # ============================================================
    # 版本对比
    # ============================================================
    def compare_versions(
        self,
        name: str,
        version_a: int,
        version_b: int,
    ) -> Dict[str, Any]:
        """
        对比两个版本的差异

        Args:
            name: 数据集名称
            version_a: 版本A
            version_b: 版本B

        Returns:
            {
                "version_a": int,
                "version_b": int,
                "stats_a": {...},
                "stats_b": {...},
                "diff": {"added": [...], "removed": [...], "modified": [...]},
            }
        """
        ds_a = self.get_version(name, version_a)
        ds_b = self.get_version(name, version_b)
        if ds_a is None:
            raise ValueError(f"版本 {version_a} 不存在")
        if ds_b is None:
            raise ValueError(f"版本 {version_b} 不存在")

        ids_a = set(ds_a.records.keys())
        ids_b = set(ds_b.records.keys())

        added = list(ids_b - ids_a)
        removed = list(ids_a - ids_b)
        modified = []
        for tid in ids_a & ids_b:
            rec_a = ds_a.records[tid]
            rec_b = ds_b.records[tid]
            if (rec_a.is_suspicious != rec_b.is_suspicious
                    or rec_a.review_status != rec_b.review_status):
                modified.append(tid)

        return {
            "version_a": version_a,
            "version_b": version_b,
            "stats_a": ds_a._compute_stats(),
            "stats_b": ds_b._compute_stats(),
            "diff": {
                "added": sorted(added),
                "removed": sorted(removed),
                "modified": sorted(modified),
            },
        }

    # ============================================================
    # 版本回滚
    # ============================================================
    def rollback_to_version(
        self,
        name: str,
        target_version: int,
        description: str = "",
    ) -> int:
        """
        回滚到指定版本

        戒律:
        - M4: 回滚操作本身也创建新版本，保留回滚痕迹
        - P4: 原子写入

        Args:
            name: 数据集名称
            target_version: 目标版本
            description: 回滚说明

        Returns:
            回滚后的新版本号
        """
        target_ds = self.get_version(name, target_version)
        if target_ds is None:
            raise ValueError(f"目标版本 {target_version} 不存在")

        rollback_desc = description or f"回滚到版本 {target_version}"
        # 戒律 M4: 回滚创建新版本，保留痕迹
        new_version = self.save_version(target_ds, description=rollback_desc)
        return new_version

    # ============================================================
    # 变更日志管理
    # ============================================================
    def _load_changelog(self, name: str) -> Dict[str, Any]:
        """加载变更日志"""
        path = self._changelog_path(name)
        if not os.path.exists(path):
            return {
                "dataset_name": name,
                "versions": [],
                "latest_version": 0,
                "updated_at": _now_str(),
            }
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {
                "dataset_name": name,
                "versions": [],
                "latest_version": 0,
                "updated_at": _now_str(),
            }

    def _save_changelog(self, name: str, changelog: Dict[str, Any]) -> None:
        """保存变更日志（戒律 P4: 原子写入）"""
        path = self._changelog_path(name)
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(changelog, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except OSError as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise RuntimeError(f"变更日志保存失败: {e}") from e

    # ============================================================
    # 删除版本（谨慎操作）
    # ============================================================
    def delete_version(self, name: str, version: int) -> bool:
        """
        删除指定版本快照（不影响当前版本）

        戒律 M4: 谨慎操作，不删除当前版本和变更日志
        """
        if version <= 0:
            return False
        path = self._version_path(name, version)
        if os.path.exists(path):
            try:
                os.remove(path)
                return True
            except OSError:
                return False
        return False
