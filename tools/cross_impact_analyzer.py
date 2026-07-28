"""
交叉影响分析器 (Cross-Impact Analyzer)

职责:
- 分析改变某条规则参数对**其他规则**命中数的交叉影响
- 构建影响矩阵 [参数变更 × 规则] -> 命中数变化
- 识别强交叉影响（参数变更显著影响其他规则）

戒律遵循:
- M1: 基于真实交易数据计算，不编造
- M2: 强交叉影响附带解释
- M4: 分析结果持久化，可追溯
- P4: 不修改全局配置，通过 RuleTuner 临时配置运行

设计要点:
- 每次只变更一个参数，隔离测量其交叉影响
- 基线参数 + 单参数变更 -> 运行规则 -> 对比各规则命中数
- 强交叉影响: |delta| >= STRONG_IMPACT_THRESHOLD 或 |relative| >= 强相对阈值
"""
import os
import json
import uuid
import copy
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from config import CROSS_IMPACT_DIR


# 强交叉影响判定阈值
STRONG_IMPACT_ABSOLUTE_THRESHOLD = 1     # 命中数绝对变化 >= 1 视为有影响
STRONG_IMPACT_RELATIVE_THRESHOLD = 0.20  # 相对变化 >= 20% 视为强影响


def _now_str() -> str:
    return datetime.now().isoformat()


# ============================================================
# 数据结构
# ============================================================
class ParamChange:
    """单参数变更"""

    def __init__(
        self,
        group: str,
        param: str,
        old_value: Any,
        new_value: Any,
    ):
        self.group = group
        self.param = param
        self.old_value = old_value
        self.new_value = new_value

    @property
    def key(self) -> str:
        """唯一标识"""
        return f"{self.group}.{self.param}"

    @property
    def description(self) -> str:
        return f"{self.group}.{self.param}: {self.old_value} -> {self.new_value}"

    def to_dict(self) -> dict:
        return {
            "group": self.group,
            "param": self.param,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "key": self.key,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ParamChange":
        return cls(
            group=data.get("group", ""),
            param=data.get("param", ""),
            old_value=data.get("old_value"),
            new_value=data.get("new_value"),
        )


class RuleImpact:
    """单条规则受某参数变更的影响"""

    def __init__(
        self,
        rule_name: str,
        baseline_hits: int,
        modified_hits: int,
    ):
        self.rule_name = rule_name
        self.baseline_hits = baseline_hits
        self.modified_hits = modified_hits
        self.delta = modified_hits - baseline_hits
        if baseline_hits > 0:
            self.relative_change = self.delta / baseline_hits
        else:
            # 基线为 0 时：有新增命中记为 +1.0，无变化记为 0.0
            self.relative_change = 1.0 if self.delta > 0 else 0.0

    @property
    def is_cross_impact(self) -> bool:
        """是否构成交叉影响（变更参数所属规则以外的影响）"""
        # 注意: 此属性需结合 ParamChange.group 判断，这里仅判断是否有变化
        return self.delta != 0

    @property
    def is_strong(self) -> bool:
        """是否为强影响"""
        if abs(self.delta) >= STRONG_IMPACT_ABSOLUTE_THRESHOLD:
            return True
        if abs(self.relative_change) >= STRONG_IMPACT_RELATIVE_THRESHOLD:
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "rule_name": self.rule_name,
            "baseline_hits": self.baseline_hits,
            "modified_hits": self.modified_hits,
            "delta": self.delta,
            "relative_change": round(self.relative_change, 4),
            "is_strong": self.is_strong,
        }


class CrossImpactResult:
    """交叉影响分析完整结果"""

    def __init__(
        self,
        analysis_id: str,
        timestamp: str,
        baseline_params: Dict[str, Any],
        param_changes: List[ParamChange],
        # {param_change_key: {rule_name: RuleImpact}}
        impacts: Dict[str, Dict[str, RuleImpact]],
        strong_impacts: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.analysis_id = analysis_id
        self.timestamp = timestamp
        self.baseline_params = baseline_params
        self.param_changes = param_changes
        self.impacts = impacts
        self.strong_impacts = strong_impacts
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "analysis_id": self.analysis_id,
            "timestamp": self.timestamp,
            "baseline_params": self.baseline_params,
            "param_changes": [pc.to_dict() for pc in self.param_changes],
            "impacts": {
                pc_key: {
                    rule_name: ri.to_dict()
                    for rule_name, ri in rule_impacts.items()
                }
                for pc_key, rule_impacts in self.impacts.items()
            },
            "strong_impacts": self.strong_impacts,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CrossImpactResult":
        param_changes = [
            ParamChange.from_dict(d) for d in data.get("param_changes", [])
        ]
        impacts: Dict[str, Dict[str, RuleImpact]] = {}
        for pc_key, rule_impacts in data.get("impacts", {}).items():
            impacts[pc_key] = {}
            for rule_name, ri_data in rule_impacts.items():
                impacts[pc_key][rule_name] = RuleImpact(
                    rule_name=ri_data.get("rule_name", rule_name),
                    baseline_hits=ri_data.get("baseline_hits", 0),
                    modified_hits=ri_data.get("modified_hits", 0),
                )
        return cls(
            analysis_id=data.get("analysis_id", ""),
            timestamp=data.get("timestamp", ""),
            baseline_params=data.get("baseline_params", {}),
            param_changes=param_changes,
            impacts=impacts,
            strong_impacts=data.get("strong_impacts", []),
            metadata=data.get("metadata", {}),
        )


# ============================================================
# 交叉影响分析器
# ============================================================
class CrossImpactAnalyzer:
    """
    交叉影响分析器

    用法:
        analyzer = CrossImpactAnalyzer()
        result = analyzer.analyze(
            transactions=transactions,
            baseline_params=baseline_params,
            param_changes=[
                ParamChange("large_amount", "threshold", 100000, 50000),
                ParamChange("smurfing", "min_count", 5, 3),
            ],
        )
        for si in result.strong_impacts:
            print(si["description"])
    """

    def __init__(self, storage_dir: str = ""):
        self.storage_dir = storage_dir or CROSS_IMPACT_DIR
        os.makedirs(self.storage_dir, exist_ok=True)
        # 复用 RuleTuner 运行规则（戒律 M1: 口径一致）
        from tools.rule_tuner import RuleTuner
        self._tuner = RuleTuner()

    # ============================================================
    # 规则命中计数
    # ============================================================
    def _run_and_count(
        self,
        transactions: List[Any],
        params: Optional[Dict[str, Any]],
    ) -> Dict[str, int]:
        """
        运行规则并返回各规则命中数

        戒律:
        - M1: 基于真实交易数据
        - P4: 通过 RuleTuner._run_rules 临时配置，不修改全局
        """
        hits = self._tuner._run_rules(transactions, params)
        return {rule_name: len(rule_hits) for rule_name, rule_hits in hits.items()}

    # ============================================================
    # 单参数变更影响
    # ============================================================
    def _measure_single_change_impact(
        self,
        transactions: List[Any],
        baseline_params: Dict[str, Any],
        baseline_counts: Dict[str, int],
        change: ParamChange,
    ) -> Dict[str, RuleImpact]:
        """
        测量单个参数变更对各规则的影响

        戒律:
        - M1: 基线和变更使用同一交易数据
        - P4: 仅变更目标参数，其他参数保持基线
        """
        # 构造变更后参数 = 基线 + 单参数变更
        modified_params = copy.deepcopy(baseline_params)
        if change.group not in modified_params:
            modified_params[change.group] = {}
        if not isinstance(modified_params[change.group], dict):
            modified_params[change.group] = {}
        modified_params[change.group][change.param] = change.new_value

        modified_counts = self._run_and_count(transactions, modified_params)

        # 对齐所有规则名（基线 + 变更）
        all_rules = set(baseline_counts.keys()) | set(modified_counts.keys())
        impacts: Dict[str, RuleImpact] = {}
        for rule_name in all_rules:
            baseline_hit = baseline_counts.get(rule_name, 0)
            modified_hit = modified_counts.get(rule_name, 0)
            impacts[rule_name] = RuleImpact(
                rule_name=rule_name,
                baseline_hits=baseline_hit,
                modified_hits=modified_hit,
            )
        return impacts

    # ============================================================
    # 强交叉影响识别
    # ============================================================
    def _identify_strong_impacts(
        self,
        param_changes: List[ParamChange],
        impacts: Dict[str, Dict[str, RuleImpact]],
    ) -> List[Dict[str, Any]]:
        """
        识别强交叉影响

        戒律:
        - M2: 每个强影响附带描述
        - P1: 关注导致命中减少的交叉影响（可能遗漏）
        - P2: 关注导致命中激增的交叉影响（可能误报）

        交叉影响定义: 参数变更所属规则以外的规则命中变化
        """
        strong: List[Dict[str, Any]] = []
        # 建立 group -> 规则名映射（粗略：参数组名对应的规则）
        # 注意: 参数组名与规则名不完全一致，这里用组名作为"直接受影响规则"的近似
        for change in param_changes:
            pc_key = change.key
            rule_impacts = impacts.get(pc_key, {})
            for rule_name, ri in rule_impacts.items():
                if not ri.is_strong:
                    continue
                # 判断是否为交叉影响（规则名与变更组关联则视为直接影响）
                # 由于规则名是中文（如"分拆转账"），组名是英文（如"smurfing"），
                # 这里通过 delta != 0 且 modified != baseline 来识别所有影响，
                # 交叉影响由调用方根据规则名与组的关系判断
                is_cross = self._is_cross_impact(change.group, rule_name)
                if not is_cross and ri.delta == 0:
                    continue
                direction = "增加" if ri.delta > 0 else "减少" if ri.delta < 0 else "不变"
                guardrail = ""
                if ri.delta < 0:
                    guardrail = "（戒律 P1: 命中减少可能遗漏）"
                elif ri.delta > 0:
                    guardrail = "（戒律 P2: 命中增加可能误报）"
                strong.append({
                    "param_change": pc_key,
                    "change_description": change.description,
                    "rule_name": rule_name,
                    "baseline_hits": ri.baseline_hits,
                    "modified_hits": ri.modified_hits,
                    "delta": ri.delta,
                    "relative_change": round(ri.relative_change, 4),
                    "direction": direction,
                    "is_cross_impact": is_cross,
                    "guardrail_note": guardrail,
                })
        return strong

    def _is_cross_impact(self, group: str, rule_name: str) -> bool:
        """
        判断规则名是否属于该参数组的"直接规则"

        参数组与规则名映射（粗略）:
        - smurfing -> 分拆转账
        - fast_in_fast_out -> 快进快出
        - round_trip -> 对敲交易
        - large_amount -> 大额交易
        - baseline_deviation -> 基线偏离
        - remark_keywords -> 备注关键词
        - shell_company -> 空壳公司

        非对应规则视为交叉影响
        """
        mapping = {
            "smurfing": "分拆转账",
            "fast_in_fast_out": "快进快出",
            "round_trip": "对敲交易",
            "large_amount": "大额交易",
            "baseline_deviation": "基线偏离",
            "remark_keywords": "备注关键词",
            "shell_company": "空壳公司",
        }
        direct_rule = mapping.get(group, "")
        return rule_name != direct_rule

    # ============================================================
    # 主入口
    # ============================================================
    def analyze(
        self,
        transactions: List[Any],
        baseline_params: Dict[str, Any],
        param_changes: List[ParamChange],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CrossImpactResult:
        """
        运行交叉影响分析

        戒律:
        - M1: 基于真实交易数据
        - M2: 强影响附带描述
        - M4: 结果持久化
        - P4: 不修改全局配置

        Args:
            transactions: 交易列表
            baseline_params: 基线参数 {group: {param: value}}
            param_changes: 参数变更列表
            metadata: 附加元数据

        Returns:
            CrossImpactResult
        """
        if not param_changes:
            raise ValueError("参数变更列表不能为空")

        # 1. 运行基线规则
        baseline_counts = self._run_and_count(transactions, baseline_params)

        # 2. 逐个测量参数变更影响
        impacts: Dict[str, Dict[str, RuleImpact]] = {}
        for change in param_changes:
            rule_impacts = self._measure_single_change_impact(
                transactions, baseline_params, baseline_counts, change
            )
            impacts[change.key] = rule_impacts

        # 3. 识别强交叉影响
        strong_impacts = self._identify_strong_impacts(param_changes, impacts)

        analysis_id = f"CIA-{uuid.uuid4().hex[:8].upper()}"
        result = CrossImpactResult(
            analysis_id=analysis_id,
            timestamp=_now_str(),
            baseline_params=copy.deepcopy(baseline_params),
            param_changes=param_changes,
            impacts=impacts,
            strong_impacts=strong_impacts,
            metadata=metadata or {},
        )

        # 4. 持久化
        self._save_result(result)
        return result

    def _save_result(self, result: CrossImpactResult) -> None:
        """保存分析结果（戒律 M4: 原子写入）"""
        path = os.path.join(self.storage_dir, f"{result.analysis_id}.json")
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except OSError as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise RuntimeError(f"交叉影响分析结果保存失败: {e}") from e

    # ============================================================
    # 查询
    # ============================================================
    def list_analyses(self) -> List[Dict[str, Any]]:
        """列出所有分析结果摘要（按时间倒序）"""
        results: List[Dict[str, Any]] = []
        if not os.path.exists(self.storage_dir):
            return results
        for fname in os.listdir(self.storage_dir):
            if not fname.startswith("CIA-") or not fname.endswith(".json"):
                continue
            path = os.path.join(self.storage_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results.append({
                    "analysis_id": data.get("analysis_id", ""),
                    "timestamp": data.get("timestamp", ""),
                    "param_change_count": len(data.get("param_changes", [])),
                    "strong_impact_count": len(data.get("strong_impacts", [])),
                })
            except (json.JSONDecodeError, OSError):
                continue
        results.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return results

    def get_analysis(self, analysis_id: str) -> Optional[CrossImpactResult]:
        """获取指定分析结果"""
        path = os.path.join(self.storage_dir, f"{analysis_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        try:
            return CrossImpactResult.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return None

    def delete_analysis(self, analysis_id: str) -> bool:
        """删除分析结果"""
        path = os.path.join(self.storage_dir, f"{analysis_id}.json")
        if os.path.exists(path):
            try:
                os.remove(path)
                return True
            except OSError:
                return False
        return False

    # ============================================================
    # 影响矩阵
    # ============================================================
    def build_impact_matrix(
        self,
        result: CrossImpactResult,
    ) -> Dict[str, Dict[str, int]]:
        """
        从分析结果构建影响矩阵

        Returns:
            {param_change_key: {rule_name: delta}}
        """
        matrix: Dict[str, Dict[str, int]] = {}
        for pc_key, rule_impacts in result.impacts.items():
            matrix[pc_key] = {
                rule_name: ri.delta
                for rule_name, ri in rule_impacts.items()
            }
        return matrix
