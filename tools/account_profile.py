"""
账户风险画像

持久化存储每个账户的历史风险记录，用于：
- 累犯账户风险加成（戒律 P1：不遗漏高风险交易）
- 历史清白账户适度放宽（戒律 P2：不误报）

严格遵守戒律:
- M1: 所有画像数据基于真实历史分析结果，不臆测
- P2: 画像只是加权因子，不单独作为判定依据
"""
import os
import json
import time
from typing import Dict, List, Optional
from datetime import datetime


class AccountRiskProfile:
    """
    账户风险画像

    数据结构:
    {
        "account_id": "A001",
        "first_seen": "2026-01-01T00:00:00",
        "last_seen": "2026-07-27T00:00:00",
        "total_transactions": 100,
        "total_suspicious_hits": 3,       # 历史可疑命中次数
        "suspicious_patterns": {"分拆转账": 2, "快进快出": 1},
        "highest_risk_score": 75,
        "avg_risk_score": 55,
        "risk_trend": "rising",           # rising / stable / falling
        "last_analysis_time": "2026-07-27T00:00:00",
        "notes": [],
        "false_positive_count": 1,        # 分析师标记的误报次数
        "false_negative_count": 0,        # 分析师标记的漏报次数
    }
    """

    def __init__(self, account_id: str):
        self.account_id = account_id
        self.first_seen = ""
        self.last_seen = ""
        self.total_transactions = 0
        self.total_suspicious_hits = 0
        self.suspicious_patterns: Dict[str, int] = {}
        self.highest_risk_score = 0
        self.avg_risk_score = 0
        self.risk_trend = "stable"
        self.last_analysis_time = ""
        self.notes: List[str] = []
        # 误反馈统计（戒律 M1: 来自分析师真实判断）
        self.false_positive_count = 0
        self.false_negative_count = 0

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "total_transactions": self.total_transactions,
            "total_suspicious_hits": self.total_suspicious_hits,
            "suspicious_patterns": self.suspicious_patterns,
            "highest_risk_score": self.highest_risk_score,
            "avg_risk_score": self.avg_risk_score,
            "risk_trend": self.risk_trend,
            "last_analysis_time": self.last_analysis_time,
            "notes": self.notes,
            "false_positive_count": self.false_positive_count,
            "false_negative_count": self.false_negative_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AccountRiskProfile":
        # 戒律 P4: 校验 account_id 非空，避免无主画像污染存储
        account_id = data.get("account_id", "")
        if not account_id:
            return None
        profile = cls(account_id)
        profile.first_seen = data.get("first_seen", "")
        profile.last_seen = data.get("last_seen", "")
        profile.total_transactions = data.get("total_transactions", 0)
        profile.total_suspicious_hits = data.get("total_suspicious_hits", 0)
        profile.suspicious_patterns = data.get("suspicious_patterns", {})
        profile.highest_risk_score = data.get("highest_risk_score", 0)
        profile.avg_risk_score = data.get("avg_risk_score", 0)
        profile.risk_trend = data.get("risk_trend", "stable")
        profile.last_analysis_time = data.get("last_analysis_time", "")
        profile.notes = data.get("notes", [])
        # 兼容旧数据：缺失时默认 0
        profile.false_positive_count = data.get("false_positive_count", 0)
        profile.false_negative_count = data.get("false_negative_count", 0)
        return profile

    def get_risk_multiplier(self) -> float:
        """
        根据画像计算风险加成系数

        基础系数（基于历史可疑命中）:
        - 历史清白（0次可疑）且交易多: 0.9（适度降分，戒律 P2：不误报）
        - 1-2 次可疑: 1.0（正常）
        - 3-5 次可疑: 1.15（累犯加成）
        - 5 次以上: 1.3（高度可疑）

        误反馈调整（戒律 P1/P2: 误报降权、漏报加权）:
        - 误报次数: 每次降 0.05，最多降 0.25（系统倾向误报此账户）
        - 漏报次数: 每次加 0.10，最多加 0.30（系统倾向漏报此账户）
        - 最终系数钳制在 [0.7, 1.5] 范围内

        注意: 只是加权因子，不单独作为判定依据
        """
        hits = self.total_suspicious_hits
        if hits == 0 and self.total_transactions >= 10:
            base = 0.9  # 交易笔数多且从未可疑 → 适度放宽
        elif hits <= 2:
            base = 1.0
        elif hits <= 5:
            base = 1.15
        else:
            base = 1.3

        # 误反馈调整（无反馈时保持原值，兼容已有测试）
        if self.false_positive_count == 0 and self.false_negative_count == 0:
            return base

        fp_adjust = -0.05 * min(self.false_positive_count, 5)   # 最多降 0.25
        fn_adjust = +0.10 * min(self.false_negative_count, 3)   # 最多加 0.30
        final = base + fp_adjust + fn_adjust
        # 钳制到 [0.7, 1.5]（戒律 P1: 不遗漏下限；P2: 不误报上限）
        return max(0.7, min(1.5, final))

    def is_high_risk_recidivist(self) -> bool:
        """是否为高风险累犯账户"""
        return self.total_suspicious_hits >= 3 and self.highest_risk_score >= 70


class AccountProfileManager:
    """
    账户画像管理器

    负责画像的加载、保存、更新
    """

    def __init__(self, storage_path: str = ""):
        self.storage_path = storage_path
        self._profiles: Dict[str, AccountRiskProfile] = {}
        if storage_path and os.path.exists(storage_path):
            self._load()

    def _load(self):
        """从文件加载画像"""
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for acc_id, profile_data in data.items():
                if not isinstance(profile_data, dict):
                    continue
                # 戒律 P4: 兼容旧数据格式 — 若 profile_data 未内嵌 account_id，
                # 使用外层 dict key 补全，再交给 from_dict 校验
                if not profile_data.get("account_id"):
                    profile_data = {**profile_data, "account_id": acc_id}
                profile = AccountRiskProfile.from_dict(profile_data)
                # 戒律 P4: 跳过 from_dict 返回 None 的无效条目
                if profile is not None:
                    self._profiles[acc_id] = profile
        except (json.JSONDecodeError, IOError) as e:
            # 戒律 M4: 异常时打印日志并保留空画像，不静默清空
            print(f"[画像] 加载失败: {e}，使用空画像")
            self._profiles = {}

    def save(self):
        """保存画打到文件"""
        if not self.storage_path:
            return
        # 戒律 P4: dirname 返回空字符串时 makedirs 会崩溃
        dir_part = os.path.dirname(self.storage_path)
        if dir_part:
            os.makedirs(dir_part, exist_ok=True)
        data = {
            acc_id: profile.to_dict()
            for acc_id, profile in self._profiles.items()
        }
        # 戒律 M4: 捕获 IO/JSON 异常，避免崩溃且可追溯
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except (OSError, json.JSONDecodeError, TypeError) as e:
            print(f"[画像] 保存失败: {e}")

    def get_profile(self, account_id: str) -> AccountRiskProfile:
        """获取账户画像，不存在则创建新的"""
        if account_id not in self._profiles:
            self._profiles[account_id] = AccountRiskProfile(account_id)
        return self._profiles[account_id]

    def update_from_suspicious(self, suspicious_list: list, total_transactions: int = 0):
        """
        根据可疑交易列表更新画像

        Args:
            suspicious_list: 可疑交易列表
            total_transactions: 本次分析的总交易数（兼容旧接口，已不再使用）
        """
        now = datetime.now().isoformat()

        # 统计每个账户的可疑情况
        account_hits: Dict[str, Dict] = {}
        for s in suspicious_list:
            txn = s.get("transaction", {})
            from_acc = txn.get("from_account", "")
            to_acc = txn.get("to_account", "")
            score = s.get("risk_score", 0)
            rules = s.get("rule_hits", [])

            for acc in [from_acc, to_acc]:
                if not acc:
                    continue
                if acc not in account_hits:
                    account_hits[acc] = {"count": 0, "max_score": 0, "patterns": set()}
                account_hits[acc]["count"] += 1
                account_hits[acc]["max_score"] = max(account_hits[acc]["max_score"], score)
                for r in rules:
                    account_hits[acc]["patterns"].add(r)

        # 更新每个账户的画像
        for acc_id, hits in account_hits.items():
            profile = self.get_profile(acc_id)
            profile.total_suspicious_hits += hits["count"]
            profile.highest_risk_score = max(profile.highest_risk_score, hits["max_score"])
            for pattern in hits["patterns"]:
                profile.suspicious_patterns[pattern] = profile.suspicious_patterns.get(pattern, 0) + 1
            profile.last_analysis_time = now
            if not profile.first_seen:
                profile.first_seen = now
            profile.last_seen = now

    def update_from_transactions(self, transactions: list):
        """
        累加每个账户的真实交易笔数（用于戒律 P2: 清白账户 ×0.9 降分判定）

        戒律 M1: 基于真实交易数据累加
        戒律 P2: total_transactions 达到一定阈值才认为画像可靠
        """
        from collections import defaultdict
        counts: Dict[str, int] = defaultdict(int)
        for txn in transactions:
            for acc in [txn.get("from_account", ""), txn.get("to_account", "")]:
                if acc:
                    counts[acc] += 1
        for acc_id, n in counts.items():
            profile = self.get_profile(acc_id)
            profile.total_transactions += n
            profile.last_seen = datetime.now().isoformat()
            if not profile.first_seen:
                profile.first_seen = datetime.now().isoformat()

    def get_all_profiles(self) -> Dict[str, AccountRiskProfile]:
        """获取所有画像"""
        return self._profiles

    def get_high_risk_accounts(self, min_hits: int = 3, min_score: int = 70) -> List[AccountRiskProfile]:
        """获取高风险账户列表"""
        return [
            p for p in self._profiles.values()
            if p.total_suspicious_hits >= min_hits and p.highest_risk_score >= min_score
        ]
