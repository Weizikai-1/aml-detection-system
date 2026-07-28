"""
KYC客户身份画像模块（B0-3）

职责: 管理客户身份信息(KYC)，检测账户类型与实际行为的不匹配

严格遵守戒律:
- M1: KYC数据基于真实客户信息，不编造
- M2: 行为不匹配时标注具体偏离理由
- M4: 证据链完整，记录预期行为和实际行为对比
- P1: 高风险偏离不遗漏
- P2: 合理偏离不误报（需明显偏离才标记）

客户类型与预期行为模式:
- 个人账户: 小额、低频、日常消费为主
- 个体工商户: 中等金额、中等频率、经营性收支
- 企业账户: 大额、高频、对公交易为主
- 金融机构: 超大额、高频、同业拆借

使用方式:
    from tools.kyc_profile import kyc_manager
    kyc_manager.set_kyc_info("ACC001", customer_type="personal", industry="retail")
    mismatch = kyc_manager.check_behavior_mismatch("ACC001", transactions)
"""
import os
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from collections import defaultdict


# ============================================================
# 客户类型定义与预期行为模式
# ============================================================

# 客户类型预期行为基线（基于反洗钱监管实践）
CUSTOMER_TYPE_PROFILES = {
    "personal": {
        "name": "个人账户",
        "expected_max_amount": 50000,       # 单笔预期最大金额
        "expected_daily_count": 5,           # 日均预期交易笔数
        "expected_monthly_amount": 200000,   # 月预期总金额
        "cross_border_expected": False,      # 预期是否有跨境交易
        "night_ratio_expected": 0.1,         # 预期夜间交易占比
        "description": "个人日常消费账户，小额低频",
    },
    "sole_proprietor": {
        "name": "个体工商户",
        "expected_max_amount": 200000,
        "expected_daily_count": 20,
        "expected_monthly_amount": 1000000,
        "cross_border_expected": False,
        "night_ratio_expected": 0.15,
        "description": "个体经营账户，中等金额频率",
    },
    "enterprise": {
        "name": "企业账户",
        "expected_max_amount": 1000000,
        "expected_daily_count": 50,
        "expected_monthly_amount": 5000000,
        "cross_border_expected": True,       # 企业可能有跨境贸易
        "night_ratio_expected": 0.1,
        "description": "企业对公账户，大额高频",
    },
    "financial_institution": {
        "name": "金融机构",
        "expected_max_amount": 10000000,
        "expected_daily_count": 100,
        "expected_monthly_amount": 50000000,
        "cross_border_expected": True,
        "night_ratio_expected": 0.2,
        "description": "金融机构同业账户，超大额超高频",
    },
}

# 行业风险等级
INDUSTRY_RISK_LEVELS = {
    "retail": {"level": "low", "name": "零售业", "risk_multiplier": 0.9},
    "manufacturing": {"level": "low", "name": "制造业", "risk_multiplier": 0.9},
    "service": {"level": "low", "name": "服务业", "risk_multiplier": 1.0},
    "technology": {"level": "low", "name": "科技业", "risk_multiplier": 1.0},
    "agriculture": {"level": "low", "name": "农业", "risk_multiplier": 0.9},
    "education": {"level": "low", "name": "教育", "risk_multiplier": 0.8},
    "healthcare": {"level": "low", "name": "医疗", "risk_multiplier": 0.9},
    "real_estate": {"level": "medium", "name": "房地产", "risk_multiplier": 1.1},
    "construction": {"level": "medium", "name": "建筑", "risk_multiplier": 1.1},
    "logistics": {"level": "medium", "name": "物流", "risk_multiplier": 1.0},
    "trade": {"level": "medium", "name": "贸易", "risk_multiplier": 1.2},
    "entertainment": {"level": "medium", "name": "娱乐", "risk_multiplier": 1.2},
    "precious_metals": {"level": "high", "name": "贵金属", "risk_multiplier": 1.3},
    "jewelry": {"level": "high", "name": "珠宝", "risk_multiplier": 1.3},
    "currency_exchange": {"level": "high", "name": "货币兑换", "risk_multiplier": 1.4},
    "crypto": {"level": "high", "name": "虚拟货币", "risk_multiplier": 1.5},
    "gambling": {"level": "high", "name": "博彩", "risk_multiplier": 1.5},
    "unknown": {"level": "medium", "name": "未知行业", "risk_multiplier": 1.0},
}


class KYCProfile:
    """客户KYC身份信息"""

    def __init__(self, account_id: str):
        self.account_id = account_id
        self.customer_type: str = "personal"           # personal/sole_proprietor/enterprise/financial_institution
        self.industry: str = "unknown"                  # 行业代码
        self.business_scope: str = ""                   # 经营范围描述
        self.registered_capital: float = 0              # 注册资本
        self.established_date: str = ""                 # 成立日期
        self.expected_regions: List[str] = []           # 预期交易地区
        self.high_risk_flags: List[str] = []            # 高风险标记（如PEP政治暴露人）
        self.updated_time: str = ""

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "customer_type": self.customer_type,
            "industry": self.industry,
            "business_scope": self.business_scope,
            "registered_capital": self.registered_capital,
            "established_date": self.established_date,
            "expected_regions": self.expected_regions,
            "high_risk_flags": self.high_risk_flags,
            "updated_time": self.updated_time,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KYCProfile":
        account_id = data.get("account_id", "")
        if not account_id:
            return None
        profile = cls(account_id)
        profile.customer_type = data.get("customer_type", "personal")
        profile.industry = data.get("industry", "unknown")
        profile.business_scope = data.get("business_scope", "")
        profile.registered_capital = data.get("registered_capital", 0)
        profile.established_date = data.get("established_date", "")
        profile.expected_regions = data.get("expected_regions", [])
        profile.high_risk_flags = data.get("high_risk_flags", [])
        profile.updated_time = data.get("updated_time", "")
        return profile

    def get_expected_profile(self) -> dict:
        """获取该客户类型的预期行为模式"""
        return CUSTOMER_TYPE_PROFILES.get(
            self.customer_type,
            CUSTOMER_TYPE_PROFILES["personal"],
        )

    def get_industry_risk_multiplier(self) -> float:
        """获取行业风险系数"""
        industry_info = INDUSTRY_RISK_LEVELS.get(
            self.industry,
            INDUSTRY_RISK_LEVELS["unknown"],
        )
        return industry_info["risk_multiplier"]


class KYCManager:
    """
    KYC客户身份管理器

    负责管理客户身份信息，检测行为偏离
    """

    def __init__(self, storage_path: str = ""):
        self.storage_path = storage_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "profiles", "kyc_profiles.json"
        )
        self._profiles: Dict[str, KYCProfile] = {}
        self._load()

    def _load(self):
        """从文件加载KYC画像"""
        if not os.path.exists(self.storage_path):
            return
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for acc_id, profile_data in data.items():
                    if not isinstance(profile_data, dict):
                        continue
                    if not profile_data.get("account_id"):
                        profile_data = {**profile_data, "account_id": acc_id}
                    profile = KYCProfile.from_dict(profile_data)
                    if profile is not None:
                        self._profiles[acc_id] = profile
        except (json.JSONDecodeError, IOError) as e:
            print(f"[KYC] 加载画像失败: {e}，使用空画像")
            self._profiles = {}

    def save(self):
        """保存KYC画像"""
        dir_part = os.path.dirname(self.storage_path)
        if dir_part:
            os.makedirs(dir_part, exist_ok=True)
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(
                    {acc_id: p.to_dict() for acc_id, p in self._profiles.items()},
                    f, ensure_ascii=False, indent=2
                )
        except (OSError, TypeError) as e:
            print(f"[KYC] 保存画像失败: {e}")

    def set_kyc_info(self, account_id: str, customer_type: str = "",
                     industry: str = "", business_scope: str = "",
                     registered_capital: float = 0, established_date: str = "",
                     expected_regions: List[str] = None,
                     high_risk_flags: List[str] = None):
        """设置客户KYC信息"""
        if not account_id:
            return
        profile = self._profiles.get(account_id, KYCProfile(account_id))
        if customer_type:
            profile.customer_type = customer_type
        if industry:
            profile.industry = industry
        if business_scope:
            profile.business_scope = business_scope
        if registered_capital:
            profile.registered_capital = registered_capital
        if established_date:
            profile.established_date = established_date
        if expected_regions is not None:
            profile.expected_regions = expected_regions
        if high_risk_flags is not None:
            profile.high_risk_flags = high_risk_flags
        profile.updated_time = datetime.now().isoformat()
        self._profiles[account_id] = profile
        self.save()

    def get_profile(self, account_id: str) -> Optional[KYCProfile]:
        """获取客户KYC画像"""
        return self._profiles.get(account_id)

    def check_behavior_mismatch(self, account_id: str,
                                transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        检测账户行为与KYC类型的不匹配

        检测维度:
        1. 单笔金额超出类型预期
        2. 交易频率超出类型预期
        3. 跨境交易与类型不符
        4. 夜间交易占比异常
        5. PEP账户高风险交易

        Returns:
            偏离列表，每项包含:
            {
                "dimension": 偏离维度,
                "expected": 预期值,
                "actual": 实际值,
                "risk_score": 风险评分,
                "evidence": 证据描述,
            }
        """
        profile = self._profiles.get(account_id)
        if profile is None:
            return []

        expected = profile.get_expected_profile()
        mismatches = []

        if not transactions:
            return mismatches

        # 计算实际行为指标
        max_amount = max(t.get("amount", 0) for t in transactions)
        total_amount = sum(t.get("amount", 0) for t in transactions)
        txn_count = len(transactions)

        # 夜间交易占比
        night_count = 0
        cross_border_count = 0
        for t in transactions:
            ts_str = t.get("timestamp", "")
            if ts_str:
                try:
                    from datetime import datetime
                    ts = datetime.fromisoformat(ts_str)
                    if 22 <= ts.hour or ts.hour < 6:
                        night_count += 1
                except (ValueError, TypeError):
                    pass
            # 跨境交易
            currency = str(t.get("currency", "CNY")).upper()
            country = str(t.get("counterparty_country", "")).upper()
            if currency not in ("CNY", "RMB", "") or (country and country != "CN"):
                cross_border_count += 1

        night_ratio = night_count / txn_count if txn_count > 0 else 0

        # 检测1: 单笔金额超出预期
        expected_max = expected["expected_max_amount"]
        # 戒律 P2: 需明显超出才标记（2倍以上）
        if max_amount > expected_max * 2:
            risk_score = min(70, int(40 + (max_amount / expected_max) * 5))
            mismatches.append({
                "dimension": "单笔金额异常",
                "expected": f"≤{expected_max:,.0f}元",
                "actual": f"{max_amount:,.0f}元",
                "risk_score": risk_score,
                "evidence": (
                    f"KYC行为偏离: 账户[{account_id}]类型为"
                    f"{expected['name']}，单笔最大金额{max_amount:,.0f}元"
                    f"超过预期{expected_max:,.0f}元的2倍"
                ),
            })

        # 检测2: 交易频率超出预期
        expected_daily = expected["expected_daily_count"]
        # 按天统计交易笔数
        daily_counts = defaultdict(int)
        for t in transactions:
            ts_str = t.get("timestamp", "")
            if ts_str:
                try:
                    day = ts_str[:10]  # YYYY-MM-DD
                    daily_counts[day] += 1
                except Exception:
                    pass
        if daily_counts:
            max_daily = max(daily_counts.values())
            # 戒律 P2: 需明显超出才标记（3倍以上）
            if max_daily > expected_daily * 3:
                risk_score = min(65, int(35 + (max_daily / expected_daily) * 3))
                mismatches.append({
                    "dimension": "交易频率异常",
                    "expected": f"日均≤{expected_daily}笔",
                    "actual": f"最高日{max_daily}笔",
                    "risk_score": risk_score,
                    "evidence": (
                        f"KYC行为偏离: 账户[{account_id}]类型为"
                        f"{expected['name']}，最高日交易{max_daily}笔"
                        f"超过预期{expected_daily}笔的3倍"
                    ),
                })

        # 检测3: 跨境交易与类型不符
        if cross_border_count > 0 and not expected["cross_border_expected"]:
            # 戒律 P2: 个人账户少量跨境可接受，超过3笔或占比>20%才标记
            if cross_border_count >= 3 or cross_border_count / txn_count > 0.2:
                risk_score = 60
                mismatches.append({
                    "dimension": "跨境交易异常",
                    "expected": f"{expected['name']}不应有跨境交易",
                    "actual": f"{cross_border_count}笔跨境交易",
                    "risk_score": risk_score,
                    "evidence": (
                        f"KYC行为偏离: 账户[{account_id}]类型为"
                        f"{expected['name']}，但有{cross_border_count}笔"
                        f"跨境交易(占比{cross_border_count/txn_count*100:.1f}%)"
                    ),
                })

        # 检测4: 夜间交易占比异常
        expected_night = expected["night_ratio_expected"]
        # 戒律 P2: 超过预期2倍才标记
        if night_ratio > expected_night * 2 and night_count >= 3:
            risk_score = min(60, int(30 + night_ratio * 50))
            mismatches.append({
                "dimension": "夜间交易异常",
                "expected": f"夜间占比≤{expected_night*100:.0f}%",
                "actual": f"夜间占比{night_ratio*100:.1f}%",
                "risk_score": risk_score,
                "evidence": (
                    f"KYC行为偏离: 账户[{account_id}]类型为"
                    f"{expected['name']}，夜间交易占比{night_ratio*100:.1f}%"
                    f"超过预期{expected_night*100:.0f}%的2倍"
                ),
            })

        # 检测5: PEP账户高风险交易
        if "PEP" in profile.high_risk_flags:
            if max_amount > expected_max:
                risk_score = 75
                mismatches.append({
                    "dimension": "PEP账户高风险交易",
                    "expected": f"PEP账户应限制大额交易",
                    "actual": f"单笔{max_amount:,.0f}元",
                    "risk_score": risk_score,
                    "evidence": (
                        f"KYC行为偏离: 账户[{account_id}]为PEP(政治暴露人)账户，"
                        f"单笔交易{max_amount:,.0f}元超过类型预期{expected_max:,.0f}元"
                    ),
                })

        # 行业风险系数调整
        industry_multiplier = profile.get_industry_risk_multiplier()
        if industry_multiplier > 1.2:
            for m in mismatches:
                m["risk_score"] = min(100, int(m["risk_score"] * industry_multiplier))

        return mismatches

    def get_all_profiles(self) -> Dict[str, KYCProfile]:
        """获取所有KYC画像"""
        return self._profiles

    def get_stats(self) -> Dict[str, int]:
        """获取统计信息"""
        type_counts = defaultdict(int)
        for p in self._profiles.values():
            type_counts[p.customer_type] += 1
        return {
            "total": len(self._profiles),
            "personal": type_counts["personal"],
            "sole_proprietor": type_counts["sole_proprietor"],
            "enterprise": type_counts["enterprise"],
            "financial_institution": type_counts["financial_institution"],
        }


# 全局单例
kyc_manager = KYCManager()
