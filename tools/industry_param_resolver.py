"""
行业差异化参数解析器 (Industry Parameter Resolver)

职责:
- 管理行业差异化规则参数配置（行业画像）
- 根据交易/账户的行业属性解析有效参数
- 不修改核心规则引擎，作为参数解析层叠加在 RuleTuner 之上

戒律遵循:
- M1: 行业配置必须显式定义，不臆造行业参数
- M2: 每个行业配置必须标注适用理由（reason 必填）
- M4: 行业配置版本可追溯，原子写入
- P4: 不破坏现有配置，行业参数仅作为覆盖层（overlay）

设计要点:
- 行业参数结构 = RuleTuner 可调参数结构 {group: {param: value}}
- 覆盖层合并方式: 组级深合并（base[group][param] <- override[group][param]）
- 缺失行业字段时回退到 DEFAULT_INDUSTRY
- 不直接修改 AML_CONFIG，仅产出有效参数字典供调用方使用
"""
import os
import json
import copy
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from config import INDUSTRY_PARAMS_DIR


# 默认行业标识（未配置行业时使用）
DEFAULT_INDUSTRY = "default"

# 交易中行业字段探测顺序（靠前优先）
INDUSTRY_FIELD_CANDIDATES = (
    "industry",
    "from_account_industry",
    "to_account_industry",
    "business_type",
    "sector",
)


def _now_str() -> str:
    return datetime.now().isoformat()


def _safe_industry_name(name: str) -> str:
    """
    生成安全的行业标识（仅允许字母数字、下划线、连字符、中文）

    戒律 M4: 防止文件名注入
    """
    if not name or not isinstance(name, str):
        return ""
    return "".join(
        c for c in name.strip()
        if c.isalnum() or c in "-_" or "\u4e00" <= c <= "\u9fff"
    )


def deep_merge_params(
    base: Dict[str, Any],
    override: Dict[str, Any],
) -> Dict[str, Any]:
    """
    组级深合并参数（不修改输入）

    戒律:
    - P4: 不破坏 base，返回新字典
    - M1: 合并基于真实 override 字段，缺失字段保持 base

    Args:
        base: 基线参数 {group: {param: value}}
        override: 覆盖参数 {group: {param: value}}

    Returns:
        合并后的新参数字典
    """
    merged = copy.deepcopy(base) if base else {}
    if not override:
        return merged
    for group, group_params in override.items():
        if not isinstance(group_params, dict):
            # 非字典值直接覆盖（向后兼容）
            merged[group] = copy.deepcopy(group_params)
            continue
        if group not in merged or not isinstance(merged[group], dict):
            merged[group] = {}
        for key, value in group_params.items():
            merged[group][key] = copy.deepcopy(value)
    return merged


# ============================================================
# 行业画像
# ============================================================
class IndustryProfile:
    """
    单个行业的参数画像

    Attributes:
        industry: 行业标识（如 "real_estate", "jewelry", "default"）
        description: 行业描述
        reason: 适用理由（戒律 M2: 必填）
        param_overrides: 参数覆盖 {group: {param: value}}
        created_at: 创建时间
        updated_at: 更新时间
        version: 版本号
    """

    def __init__(
        self,
        industry: str,
        param_overrides: Dict[str, Any],
        description: str = "",
        reason: str = "",
        created_at: str = "",
        updated_at: str = "",
        version: int = 1,
    ):
        # 戒律 M2: 适用理由必填
        if not reason or not reason.strip():
            raise ValueError(
                f"行业[{industry}]适用理由(reason)不能为空（戒律 M2: 必须标注理由）"
            )
        if not param_overrides or not isinstance(param_overrides, dict):
            raise ValueError(f"行业[{industry}]参数覆盖不能为空且必须是字典")
        safe = _safe_industry_name(industry)
        if not safe:
            raise ValueError(f"行业标识[{industry}]无效")

        self.industry: str = safe
        self.description: str = description or ""
        self.reason: str = reason.strip()
        self.param_overrides: Dict[str, Any] = copy.deepcopy(param_overrides)
        self.created_at: str = created_at or _now_str()
        self.updated_at: str = updated_at or self.created_at
        self.version: int = int(version)

    def to_dict(self) -> dict:
        return {
            "industry": self.industry,
            "description": self.description,
            "reason": self.reason,
            "param_overrides": self.param_overrides,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IndustryProfile":
        return cls(
            industry=data.get("industry", ""),
            param_overrides=data.get("param_overrides", {}),
            description=data.get("description", ""),
            reason=data.get("reason", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            version=data.get("version", 1),
        )


# ============================================================
# 行业参数注册中心
# ============================================================
class IndustryParamRegistry:
    """
    行业差异化参数注册中心

    用法:
        registry = IndustryParamRegistry()

        # 1. 注册行业画像
        registry.register_profile(
            industry="real_estate",
            param_overrides={
                "large_amount": {"threshold": 500000},  # 房地产行业大额标准更高
            },
            description="房地产行业差异化参数",
            reason="房地产行业单笔交易金额普遍较大，需提高大额阈值避免误报",
        )

        # 2. 解析有效参数
        effective = registry.get_effective_params("real_estate", base_params)

        # 3. 按交易分组解析
        grouped = registry.resolve_params_for_transactions(transactions, base_params)
    """

    def __init__(self, storage_dir: str = ""):
        self.storage_dir = storage_dir or INDUSTRY_PARAMS_DIR
        os.makedirs(self.storage_dir, exist_ok=True)

    # ============================================================
    # 注册 / 更新
    # ============================================================
    def register_profile(
        self,
        industry: str,
        param_overrides: Dict[str, Any],
        description: str = "",
        reason: str = "",
    ) -> IndustryProfile:
        """
        注册或更新行业画像

        戒律:
        - M1: 参数由调用方显式提供，不编造
        - M2: reason 必填
        - M4: 已存在则版本号递增，保留 created_at
        - P4: 不修改全局 AML_CONFIG

        Args:
            industry: 行业标识
            param_overrides: 参数覆盖 {group: {param: value}}
            description: 行业描述
            reason: 适用理由（必填）

        Returns:
            IndustryProfile
        """
        existing = self.get_profile(industry)
        if existing is not None:
            # 戒律 M4: 更新保留 created_at，版本递增
            profile = IndustryProfile(
                industry=industry,
                param_overrides=param_overrides,
                description=description,
                reason=reason,
                created_at=existing.created_at,
                updated_at=_now_str(),
                version=existing.version + 1,
            )
        else:
            profile = IndustryProfile(
                industry=industry,
                param_overrides=param_overrides,
                description=description,
                reason=reason,
            )
        self._save_profile(profile)
        return profile

    def _save_profile(self, profile: IndustryProfile) -> None:
        """保存行业画像（戒律 M4: 原子写入）"""
        path = self._profile_path(profile.industry)
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except OSError as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise RuntimeError(f"行业画像保存失败: {e}") from e

    def _profile_path(self, industry: str) -> str:
        safe = _safe_industry_name(industry)
        return os.path.join(self.storage_dir, f"{safe}.json")

    # ============================================================
    # 查询
    # ============================================================
    def get_profile(self, industry: str) -> Optional[IndustryProfile]:
        """获取单个行业画像，不存在返回 None"""
        path = self._profile_path(industry)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        try:
            return IndustryProfile.from_dict(data)
        except ValueError:
            return None

    def list_profiles(self) -> List[Dict[str, Any]]:
        """列出所有行业画像摘要（按行业名排序）"""
        results: List[Dict[str, Any]] = []
        if not os.path.exists(self.storage_dir):
            return results
        for fname in os.listdir(self.storage_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.storage_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results.append({
                    "industry": data.get("industry", ""),
                    "description": data.get("description", ""),
                    "reason": data.get("reason", ""),
                    "version": data.get("version", 1),
                    "updated_at": data.get("updated_at", ""),
                    "override_group_count": len(data.get("param_overrides", {})),
                })
            except (json.JSONDecodeError, OSError):
                continue
        results.sort(key=lambda r: r.get("industry", ""))
        return results

    def delete_profile(self, industry: str) -> bool:
        """删除行业画像"""
        path = self._profile_path(industry)
        if os.path.exists(path):
            try:
                os.remove(path)
                return True
            except OSError:
                return False
        return False

    # ============================================================
    # 参数解析
    # ============================================================
    def get_effective_params(
        self,
        industry: str,
        base_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        解析行业有效参数 = base_params + 行业覆盖

        戒律:
        - M1: 基于真实 base_params 和显式覆盖
        - P1/P2: 行业差异化参数旨在减少该行业误报和漏报
        - P4: 不修改 base_params

        Args:
            industry: 行业标识（未知行业回退到 base_params）
            base_params: 基线参数，None 时返回空行业覆盖的深拷贝

        Returns:
            有效参数字典（新对象，修改不影响 base）
        """
        if base_params is None:
            base_params = {}
        profile = self.get_profile(industry)
        if profile is None:
            # 未知行业：回退到基线（戒律 P4: 不破坏现有配置）
            return copy.deepcopy(base_params)
        return deep_merge_params(base_params, profile.param_overrides)

    def resolve_params_for_transactions(
        self,
        transactions: List[Dict[str, Any]],
        base_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        按交易行业分组解析有效参数

        戒律:
        - M1: 行业字段从交易真实数据提取
        - P4: 不修改交易列表

        Args:
            transactions: 交易列表
            base_params: 基线参数

        Returns:
            {industry: effective_params} 字典
        """
        if base_params is None:
            base_params = {}
        # 收集交易中出现的所有行业
        industries = {DEFAULT_INDUSTRY}
        for txn in transactions:
            ind = extract_industry(txn)
            if ind:
                industries.add(ind)
        # 为每个行业解析有效参数
        return {
            ind: self.get_effective_params(ind, base_params)
            for ind in industries
        }

    def group_transactions_by_industry(
        self,
        transactions: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        按行业分组交易（用于行业差异化批量分析）

        Returns:
            {industry: [transactions]}
        """
        grouped: Dict[str, List[Dict[str, Any]]] = {DEFAULT_INDUSTRY: []}
        for txn in transactions:
            ind = extract_industry(txn) or DEFAULT_INDUSTRY
            grouped.setdefault(ind, []).append(txn)
        return grouped

    # ============================================================
    # 校验（可选：调用 RuleTuner 校验覆盖参数合法性）
    # ============================================================
    def validate_overrides(
        self,
        param_overrides: Dict[str, Any],
    ) -> Tuple[bool, List[str]]:
        """
        校验覆盖参数是否符合 RuleTuner schema

        Returns:
            (is_valid, errors)
        """
        try:
            from tools.rule_tuner import RuleTuner
            tuner = RuleTuner()
            is_valid, errors, _ = tuner.validate_params(param_overrides)
            return is_valid, errors
        except Exception as e:
            # 校验失败不阻断（允许扩展参数组），但返回警告
            return True, [f"校验异常(已忽略): {e}"]


# ============================================================
# 行业字段提取
# ============================================================
def extract_industry(transaction: Dict[str, Any]) -> Optional[str]:
    """
    从交易中提取行业标识

    探测顺序（靠前优先）:
    1. industry
    2. from_account_industry
    3. to_account_industry
    4. business_type
    5. sector

    戒律:
    - M1: 仅基于交易真实字段，不臆造
    - P4: 不修改交易

    Args:
        transaction: 交易字典

    Returns:
        行业标识字符串，缺失返回 None
    """
    if not transaction or not isinstance(transaction, dict):
        return None
    for field in INDUSTRY_FIELD_CANDIDATES:
        value = transaction.get(field)
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            safe = _safe_industry_name(value)
            if safe:
                return safe
    return None


# ============================================================
# 便利函数: 带行业参数运行规则
# ============================================================
def run_rules_with_industry(
    registry: IndustryParamRegistry,
    tuner,
    transactions: List[Dict[str, Any]],
    industry: str,
    base_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, List]:
    """
    在指定行业的有效参数下运行规则引擎

    戒律:
    - P4: 通过 RuleTuner._temporary_config 临时替换配置，不永久修改
    - M4: 退出上下文后恢复原配置

    Args:
        registry: 行业参数注册中心
        tuner: RuleTuner 实例
        transactions: 交易列表
        industry: 行业标识
        base_params: 基线参数（None 时使用 tuner 当前参数）

    Returns:
        各规则命中结果 {rule_name: [hits]}
    """
    if base_params is None:
        base_params = tuner.get_tunable_params()
    effective_params = registry.get_effective_params(industry, base_params)
    # 复用 RuleTuner 的临时配置 + 运行机制
    return tuner._run_rules(transactions, effective_params)
