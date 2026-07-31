"""
反思记忆 — ChromaDB 向量存储
存储历史检测案例，支持相似案例检索
"""
import os
import json
import logging
from datetime import datetime

log = logging.getLogger("aml.memory")

MEMORY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "memory")


class MemoryStore:
    """简易文件记忆库 — 无需 ChromaDB 依赖即可运行"""

    def __init__(self):
        os.makedirs(MEMORY_DIR, exist_ok=True)
        self._file = os.path.join(MEMORY_DIR, "cases.jsonl")

    def save_case(self, case: dict):
        """保存检测案例"""
        case["saved_at"] = datetime.now().isoformat()
        try:
            with open(self._file, "a", encoding="utf-8") as f:
                f.write(json.dumps(case, ensure_ascii=False) + "\n")
            log.debug(f"案例已保存: {case.get('case_id', 'unknown')}")
        except Exception as e:
            log.warning(f"保存案例失败: {e}")

    def load_recent(self, n: int = 5) -> list:
        """加载最近 N 个案例"""
        if not os.path.exists(self._file):
            return []
        cases = []
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        cases.append(json.loads(line))
        except Exception as e:
            log.warning(f"加载记忆失败: {e}")
            return []
        return cases[-n:]

    def find_similar(self, rule_name: str, risk_score: int, n: int = 3) -> list:
        """按规则名 + 风险分查找相似历史案例"""
        recent = self.load_recent(50)
        matches = []
        for c in recent:
            if rule_name in str(c.get("rules", [])):
                matches.append(c)
        matches.sort(key=lambda x: abs(x.get("risk_score", 0) - risk_score))
        return matches[:n]


# 全局单例
memory = MemoryStore()
