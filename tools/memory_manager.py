"""
记忆管理器 — 反洗钱系统的长期记忆

职责:
- 案件记忆：历史可疑案件特征库，用于相似案例检索
- 误报记忆：记录误报模式，下次自动降权
- 漏报记忆：记录漏报模式，下次自动提权
- 规则统计记忆：每条规则的历史表现（精确率/召回率）

设计准则:
- M1: 只存真实数据，不编造
- M2: 每条记忆都有证据链
- M4: 原子写入，有索引可查
- P2: 记忆衰减机制，不误报正常交易

记忆类型:
- case: 案件记忆（可疑交易特征向量）
- false_positive: 误报记忆（被标记为误报的案件）
- false_negative: 漏报记忆（被漏掉的可疑案件）
- rule_stat: 规则统计记忆（每条规则的历史表现）
"""
import os
import json
import uuid
import hashlib
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict

from config import (
    MEMORY_CASES_DIR,
    MEMORY_FALSE_POSITIVES_DIR,
    MEMORY_FALSE_NEGATIVES_DIR,
    MEMORY_RULE_STATS_DIR,
)


# 记忆衰减配置（天）
DECAY_CONFIG = {
    "case": {"half_life_days": 180, "min_weight": 0.1},
    "false_positive": {"half_life_days": 90, "min_weight": 0.2},
    "false_negative": {"half_life_days": 365, "min_weight": 0.1},
    "rule_stat": {"half_life_days": 30, "min_weight": 0.3},
}


def _make_id(prefix: str, content: str = None) -> str:
    """生成记忆ID"""
    if content:
        h = hashlib.md5(content.encode("utf-8")).hexdigest()[:12]
        return f"{prefix}-{h}"
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _atomic_write(filepath: str, data: dict) -> None:
    """M4: 原子写入，tmp + os.replace"""
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, filepath)


def _load_json(filepath: str) -> Optional[dict]:
    """安全加载JSON"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _calc_decay_weight(created_at: str, half_life_days: int, min_weight: float) -> float:
    """计算衰减权重（指数衰减）"""
    try:
        created = datetime.fromisoformat(created_at)
    except (ValueError, TypeError):
        return min_weight
    days = (datetime.now() - created).total_seconds() / 86400
    if days <= 0:
        return 1.0
    weight = 0.5 ** (days / half_life_days)
    return max(weight, min_weight)


class MemoryManager:
    """
    反洗钱系统记忆管理器

    管理四类记忆：
    - 案件记忆（case）：历史可疑案件
    - 误报记忆（false_positive）：被标记误报的案件
    - 漏报记忆（false_negative）：漏掉的可疑案件
    - 规则统计（rule_stat）：每条规则的历史表现
    """

    def __init__(self, auto_decay: bool = True):
        self.auto_decay = auto_decay
        self._dirs = {
            "case": MEMORY_CASES_DIR,
            "false_positive": MEMORY_FALSE_POSITIVES_DIR,
            "false_negative": MEMORY_FALSE_NEGATIVES_DIR,
            "rule_stat": MEMORY_RULE_STATS_DIR,
        }
        # 内存索引：加快检索
        self._index = {
            "case": {},
            "false_positive": {},
            "false_negative": {},
            "rule_stat": {},
        }
        self._load_index()

    def _load_index(self) -> None:
        """加载内存索引（只加载元数据，不加载全文）"""
        for mem_type, directory in self._dirs.items():
            idx = {}
            if not os.path.isdir(directory):
                self._index[mem_type] = idx
                continue
            for fname in os.listdir(directory):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(directory, fname)
                data = _load_json(fpath)
                if not data:
                    continue
                mid = data.get("id", fname.replace(".json", ""))
                idx[mid] = {
                    "file": fpath,
                    "created_at": data.get("created_at", ""),
                    "tags": data.get("tags", []),
                    "weight": 1.0,
                }
            self._index[mem_type] = idx

    # ============================================================
    # 案件记忆
    # ============================================================
    def store_case(self, case_data: dict, evidence: list = None, tags: list = None) -> str:
        """
        存储一个案件记忆

        Args:
            case_data: 案件数据（交易信息、风险评分、命中规则等）
            evidence: 证据列表（M2: 证据完整）
            tags: 标签列表

        Returns:
            记忆ID
        """
        # M1: 不编造，case_data 必须有内容
        if not case_data or not isinstance(case_data, dict):
            raise ValueError("案件数据不能为空")

        content_key = json.dumps(case_data, sort_keys=True, ensure_ascii=False)
        mid = _make_id("case", content_key)
        now = datetime.now().isoformat()

        memory = {
            "id": mid,
            "type": "case",
            "created_at": now,
            "updated_at": now,
            "case_data": case_data,
            "evidence": evidence or [],
            "tags": tags or [],
            "access_count": 0,
            "last_accessed": None,
        }

        fpath = os.path.join(self._dirs["case"], f"{mid}.json")
        if not os.path.exists(fpath):
            _atomic_write(fpath, memory)
            self._index["case"][mid] = {
                "file": fpath,
                "created_at": now,
                "tags": tags or [],
                "weight": 1.0,
            }
        return mid

    def get_case(self, case_id: str) -> Optional[dict]:
        """获取单个案件记忆"""
        idx_entry = self._index["case"].get(case_id)
        if not idx_entry:
            return None
        data = _load_json(idx_entry["file"])
        if data:
            data["access_count"] = data.get("access_count", 0) + 1
            data["last_accessed"] = datetime.now().isoformat()
            _atomic_write(idx_entry["file"], data)
        return data

    def list_cases(self, limit: int = 100, tag: str = None) -> List[dict]:
        """列出案件记忆（带衰减权重）"""
        cases = []
        for mid, idx in self._index["case"].items():
            if tag and tag not in idx.get("tags", []):
                continue
            weight = 1.0
            if self.auto_decay:
                cfg = DECAY_CONFIG["case"]
                weight = _calc_decay_weight(idx["created_at"], cfg["half_life_days"], cfg["min_weight"])
            cases.append({"id": mid, "created_at": idx["created_at"], "weight": weight, "tags": idx["tags"]})
        cases.sort(key=lambda x: x["created_at"], reverse=True)
        return cases[:limit]

    # ============================================================
    # 误报/漏报记忆
    # ============================================================
    def store_false_positive(self, case_data: dict, reason: str, feedback_by: str = None) -> str:
        """
        存储误报记忆

        戒律 P2: 不误报 — 记录误报模式，下次遇到类似模式降权
        """
        if not case_data:
            raise ValueError("案件数据不能为空")
        if not isinstance(case_data, dict):
            raise TypeError("case_data 必须是字典")

        content_key = json.dumps(case_data, sort_keys=True, ensure_ascii=False)
        mid = _make_id("fp", content_key)
        now = datetime.now().isoformat()

        memory = {
            "id": mid,
            "type": "false_positive",
            "created_at": now,
            "case_data": case_data,
            "reason": reason,
            "feedback_by": feedback_by,
            "access_count": 0,
        }

        fpath = os.path.join(self._dirs["false_positive"], f"{mid}.json")
        if not os.path.exists(fpath):
            _atomic_write(fpath, memory)
            self._index["false_positive"][mid] = {
                "file": fpath,
                "created_at": now,
                "tags": [],
                "weight": 1.0,
            }
        return mid

    def store_false_negative(self, case_data: dict, missed_rule: str = None, feedback_by: str = None) -> str:
        """
        存储漏报记忆

        戒律 P1: 不遗漏 — 记录漏报模式，下次遇到类似模式提权
        """
        if not case_data:
            raise ValueError("案件数据不能为空")
        if not isinstance(case_data, dict):
            raise TypeError("case_data 必须是字典")

        content_key = json.dumps(case_data, sort_keys=True, ensure_ascii=False)
        mid = _make_id("fn", content_key)
        now = datetime.now().isoformat()

        memory = {
            "id": mid,
            "type": "false_negative",
            "created_at": now,
            "case_data": case_data,
            "missed_rule": missed_rule,
            "feedback_by": feedback_by,
            "access_count": 0,
        }

        fpath = os.path.join(self._dirs["false_negative"], f"{mid}.json")
        if not os.path.exists(fpath):
            _atomic_write(fpath, memory)
            self._index["false_negative"][mid] = {
                "file": fpath,
                "created_at": now,
                "tags": [],
                "weight": 1.0,
            }
        return mid

    def get_false_positives(self, limit: int = 50) -> List[dict]:
        """获取误报记忆列表"""
        result = []
        for mid, idx in self._index["false_positive"].items():
            weight = 1.0
            if self.auto_decay:
                cfg = DECAY_CONFIG["false_positive"]
                weight = _calc_decay_weight(idx["created_at"], cfg["half_life_days"], cfg["min_weight"])
            result.append({"id": mid, "created_at": idx["created_at"], "weight": weight})
        result.sort(key=lambda x: x["created_at"], reverse=True)
        return result[:limit]

    def get_false_negatives(self, limit: int = 50) -> List[dict]:
        """获取漏报记忆列表"""
        result = []
        for mid, idx in self._index["false_negative"].items():
            weight = 1.0
            if self.auto_decay:
                cfg = DECAY_CONFIG["false_negative"]
                weight = _calc_decay_weight(idx["created_at"], cfg["half_life_days"], cfg["min_weight"])
            result.append({"id": mid, "created_at": idx["created_at"], "weight": weight})
        result.sort(key=lambda x: x["created_at"], reverse=True)
        return result[:limit]

    # ============================================================
    # 规则统计记忆
    # ============================================================
    def update_rule_stat(self, rule_name: str, hit_count: int, false_positive_count: int = 0,
                         false_negative_count: int = 0) -> None:
        """
        更新规则统计记忆

        戒律 M1: 基于真实数据，不编造
        """
        fpath = os.path.join(self._dirs["rule_stat"], f"{rule_name}.json")
        now = datetime.now().isoformat()

        existing = _load_json(fpath)
        if existing:
            data = existing
            data["total_hits"] = data.get("total_hits", 0) + hit_count
            data["total_false_positives"] = data.get("total_false_positives", 0) + false_positive_count
            data["total_false_negatives"] = data.get("total_false_negatives", 0) + false_negative_count
            data["updated_at"] = now
        else:
            data = {
                "rule_name": rule_name,
                "created_at": now,
                "updated_at": now,
                "total_hits": hit_count,
                "total_false_positives": false_positive_count,
                "total_false_negatives": false_negative_count,
            }

        # 计算精确率和召回率（避免除零）
        total = data["total_hits"] + data["total_false_negatives"]
        data["precision"] = data["total_hits"] / max(1, data["total_hits"] + data["total_false_positives"])
        data["recall"] = data["total_hits"] / max(1, total)
        data["f1"] = 2 * data["precision"] * data["recall"] / max(0.001, data["precision"] + data["recall"])

        _atomic_write(fpath, data)

        self._index["rule_stat"][rule_name] = {
            "file": fpath,
            "created_at": data.get("created_at", now),
            "tags": [],
            "weight": 1.0,
        }

    def get_rule_stat(self, rule_name: str) -> Optional[dict]:
        """获取单条规则的统计"""
        idx_entry = self._index["rule_stat"].get(rule_name)
        if not idx_entry:
            return None
        return _load_json(idx_entry["file"])

    def get_all_rule_stats(self) -> Dict[str, dict]:
        """获取所有规则统计"""
        result = {}
        for rule_name in self._index["rule_stat"]:
            stat = self.get_rule_stat(rule_name)
            if stat:
                result[rule_name] = stat
        return result

    # ============================================================
    # 通用方法
    # ============================================================
    def get_memory_count(self, mem_type: str = None) -> int:
        """获取记忆数量"""
        if mem_type:
            return len(self._index.get(mem_type, {}))
        total = 0
        for idx in self._index.values():
            total += len(idx)
        return total

    def clear_all(self) -> None:
        """清空所有记忆（测试用）"""
        for mem_type, directory in self._dirs.items():
            if not os.path.isdir(directory):
                continue
            for fname in os.listdir(directory):
                if fname.endswith(".json"):
                    os.remove(os.path.join(directory, fname))
            self._index[mem_type] = {}

    def refresh_index(self) -> None:
        """刷新内存索引"""
        self._load_index()


_default_memory = None


def get_memory_manager() -> MemoryManager:
    """获取全局记忆管理器单例"""
    global _default_memory
    if _default_memory is None:
        _default_memory = MemoryManager()
    return _default_memory
