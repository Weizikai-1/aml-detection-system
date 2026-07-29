"""
YAML 规则配置系统 — 动态规则加载与自适应调优

核心能力:
1. YAML 文件规则加载: 规则阈值外置到 YAML 文件
2. 规则热更新: 运行时修改规则无需重启
3. 自适应参数调优: 基于历史数据自动优化规则参数
4. A/B 测试: 对比新旧规则的检测效果
5. 规则版本管理: 保留规则变更历史

设计准则:
- 配置即代码: YAML 文件可版本控制
- 向后兼容: 旧格式自动迁移
- 安全降级: YAML 加载失败回退到默认值
"""
import os
import yaml
import json
import copy
import time
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict


# ============================================================
# 1. YAML 规则管理器
# ============================================================

class RuleYAMLManager:
    """
    YAML 规则管理器

    管理规则配置的加载、保存、版本控制
    """

    def __init__(self, config_path: Optional[str] = None, defaults: Optional[Dict[str, Any]] = None):
        """
        Args:
            config_path: YAML 规则配置文件路径
            defaults: 外部传入的真实规则默认值（来自 AML_CONFIG["rules"]），
                      优先于内置编造默认值，避免运行时阈值与代码默认不一致（戒律 M1: 不编造数据）
        """
        if config_path is None:
            base_dir = os.path.dirname(os.path.dirname(__file__))
            config_dir = os.path.join(base_dir, "config", "rules")
            os.makedirs(config_dir, exist_ok=True)
            config_path = os.path.join(config_dir, "aml_rules.yaml")

        self.config_path = config_path
        self._defaults = defaults  # 真实默认值（来自 AML_CONFIG["rules"]），为 None 时回退到内置默认
        self._rules: Dict[str, Any] = {}
        self._versions: List[Dict] = []  # 历史版本
        self._last_reload: float = 0

    def load(self) -> Dict[str, Any]:
        """
        加载 YAML 规则配置

        Returns:
            规则配置字典
        """
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self._rules = yaml.safe_load(f)
                print(f"规则配置加载成功: {self.config_path}")
                self._last_reload = time.time()
                return self._rules
            except Exception as e:
                print(f"规则配置加载失败: {e}")
                print("使用默认规则配置")
                self._rules = self._get_default_rules()
                return self._rules
        else:
            print(f"规则配置文件不存在，创建默认配置: {self.config_path}")
            self._rules = self._get_default_rules()
            self.save()
            return self._rules

    def save(self, rules: Optional[Dict] = None, create_version: bool = True) -> str:
        """
        保存规则配置到 YAML 文件

        Args:
            rules: 要保存的规则 (None 则保存当前)
            create_version: 是否创建版本快照

        Returns:
            保存的文件路径
        """
        if rules is None:
            rules = self._rules

        # 创建版本快照
        if create_version:
            self._versions.append({
                "timestamp": datetime.now().isoformat(),
                "rules": copy.deepcopy(rules),
            })
            # 只保留最近 10 个版本
            if len(self._versions) > 10:
                self._versions = self._versions[-10:]

        # 确保目录存在
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)

        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(rules, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        print(f"规则配置已保存: {self.config_path}")
        return self.config_path

    def reload_if_needed(self, cooldown: float = 5.0) -> bool:
        """
        检查并热更新规则配置

        Args:
            cooldown: 冷却时间(秒)，避免频繁IO

        Returns:
            是否重新加载
        """
        now = time.time()
        if now - self._last_reload < cooldown:
            return False

        if os.path.exists(self.config_path):
            file_mtime = os.path.getmtime(self.config_path)
            if file_mtime > self._last_reload:
                self.load()
                return True
        return False

    def get(self, rule_name: str, default: Any = None) -> Any:
        """获取指定规则"""
        return self._rules.get(rule_name, default)

    def update_rule(self, rule_name: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        更新单个规则

        Args:
            rule_name: 规则名称
            updates: 要更新的字段

        Returns:
            更新后的规则
        """
        if rule_name not in self._rules:
            self._rules[rule_name] = {}

        self._rules[rule_name].update(updates)
        self.save(self._rules)  # 自动保存并创建版本
        return self._rules[rule_name]

    def get_version_history(self) -> List[Dict]:
        """获取版本历史"""
        return self._versions.copy()

    def rollback_to_version(self, version_index: int) -> Dict[str, Any]:
        """
        回滚到指定版本

        Args:
            version_index: 版本索引 (0 = 最早)

        Returns:
            回滚后的规则
        """
        if not self._versions:
            print("无历史版本可回滚")
            return self._rules

        if version_index < 0 or version_index >= len(self._versions):
            version_index = len(self._versions) - 1

        self._rules = copy.deepcopy(self._versions[version_index]["rules"])
        self.save(self._rules, create_version=True)  # 创建新版本
        print(f"已回滚到版本 {version_index}: {self._versions[version_index]['timestamp']}")
        return self._rules

    def _get_default_rules(self) -> Dict[str, Any]:
        """
        获取默认规则配置

        优先级: 外部传入的真实默认值(AML_CONFIG["rules"]) > 内置默认值
        戒律 M1: 优先使用真实 AML_CONFIG 默认值，避免编造阈值导致运行时行为漂移
        """
        # 优先返回外部传入的真实默认值（来自 AML_CONFIG["rules"]）
        if self._defaults is not None:
            return copy.deepcopy(self._defaults)

        return {
            # 分拆转账规则
            "smurfing": {
                "enabled": True,
                "hour_window": 1,
                "min_count": 5,
                "amount_range": [40000, 50000],
                "risk_score": 85,
                "description": "检测分拆转账: 同一收款账户1小时内收到≥5笔来自不同付款方的4-5万转账",
            },

            # 快进快出规则
            "fast_in_fast_out": {
                "enabled": True,
                "min_minutes": 10,
                "ratio_threshold": 0.95,
                "risk_score": 80,
                "description": "检测快进快出: 大额入账后短时间内接近全额转出",
            },

            # 对敲交易规则
            "round_trip": {
                "enabled": True,
                "max_days": 7,
                "amount_tolerance": 0.05,
                "risk_score": 75,
                "description": "检测对敲交易: A转给B，一段时间后B又转给A，金额相近",
            },

            # 大额交易规则
            "large_amount": {
                "enabled": True,
                "threshold": 100000,
                "report_only": True,
                "risk_score": 60,
                "description": "大额交易报告: 超过阈值的交易需要报告",
            },

            # 语义异常规则
            "semantic_anomaly": {
                "enabled": True,
                "amount_mismatch_multiplier": 3.0,
                "night_hours": [0, 6],
                "large_amount_threshold": 50000,
                "risk_score_amplification": 1.2,
                "description": "语义异常检测: 金额与备注不匹配、异常时间交易",
            },

            # GNN 模型参数
            "gnn_model": {
                "model_type": "edge_aware_gat",
                "hidden_channels": 64,
                "num_heads": 4,
                "dropout": 0.5,
                "learning_rate": 0.001,
                "description": "GNN 模型配置",
            },

            # 混合裁决权重
            "adjudication_weights": {
                "rule_engine": 0.40,
                "gnn_model": 0.35,
                "semantic_analysis": 0.25,
                "description": "各检测信号的权重分配",
            },
        }


# ============================================================
# 2. 自适应规则调优器
# ============================================================

class AdaptiveRuleTuner:
    """
    自适应规则调优器

    基于历史检测数据，自动优化规则参数:
    1. 统计每条规则的误报率和漏报率
    2. 基于反馈调整阈值
    3. A/B 测试新旧规则效果
    """

    def __init__(self, yaml_manager: RuleYAMLManager):
        """
        Args:
            yaml_manager: YAML 规则管理器
        """
        self.yaml_manager = yaml_manager
        self._feedback_buffer: List[Dict] = []  # 反馈缓冲
        self._stats: Dict[str, Dict] = defaultdict(lambda: {
            "total_hits": 0,
            "true_positives": 0,
            "false_positives": 0,
            "false_negatives": 0,
            "precision": 0.0,
            "recall": 0.0,
            "f1_score": 0.0,
        })

    def record_feedback(
        self,
        rule_name: str,
        transaction_id: str,
        is_correct: bool,
        was_flagged: bool,
        actual_fraud: bool,
        details: Optional[Dict] = None,
    ):
        """
        记录规则反馈

        Args:
            rule_name: 规则名称
            transaction_id: 交易ID
            is_correct: 规则判定是否正确
            was_flagged: 交易是否被规则标记
            actual_fraud: 交易是否实际欺诈
            details: 附加信息
        """
        feedback = {
            "rule_name": rule_name,
            "transaction_id": transaction_id,
            "is_correct": is_correct,
            "was_flagged": was_flagged,
            "actual_fraud": actual_fraud,
            "timestamp": datetime.now().isoformat(),
            "details": details or {},
        }
        self._feedback_buffer.append(feedback)

        # 更新统计
        stats = self._stats[rule_name]
        stats["total_hits"] += 1

        if was_flagged and actual_fraud:
            stats["true_positives"] += 1
        elif was_flagged and not actual_fraud:
            stats["false_positives"] += 1
        elif not was_flagged and actual_fraud:
            stats["false_negatives"] += 1

        # 计算指标
        tp = stats["true_positives"]
        fp = stats["false_positives"]
        fn = stats["false_negatives"]

        stats["precision"] = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        stats["recall"] = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        p, r = stats["precision"], stats["recall"]
        stats["f1_score"] = 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    def get_rule_stats(self, rule_name: str) -> Dict:
        """获取规则统计信息"""
        return dict(self._stats[rule_name])

    def get_all_stats(self) -> Dict[str, Dict]:
        """获取所有规则统计"""
        return {name: dict(stats) for name, stats in self._stats.items()}

    def suggest_optimizations(self, min_feedback: int = 50) -> List[Dict]:
        """
        基于统计数据建议参数优化

        Args:
            min_feedback: 最少反馈数量才建议优化

        Returns:
            优化建议列表
        """
        suggestions = []

        for rule_name, stats in self._stats.items():
            if stats["total_hits"] < min_feedback:
                continue

            # 场景 1: 误报率过高 (>30%)
            fp_rate = stats["false_positives"] / stats["total_hits"] if stats["total_hits"] > 0 else 0
            if fp_rate > 0.3:
                suggestions.append({
                    "rule_name": rule_name,
                    "issue": "high_false_positive_rate",
                    "severity": "high",
                    "current_fp_rate": round(fp_rate, 3),
                    "suggestion": "考虑提高规则阈值或缩小检测范围",
                    "current_params": self.yaml_manager.get(rule_name, {}),
                })

            # 场景 2: 精确率低但召回率高 -> 阈值太低
            if stats["precision"] < 0.5 and stats["recall"] > 0.7:
                suggestions.append({
                    "rule_name": rule_name,
                    "issue": "low_precision_high_recall",
                    "severity": "medium",
                    "precision": round(stats["precision"], 3),
                    "recall": round(stats["recall"], 3),
                    "suggestion": "适当提高风险评分阈值以提升精确率",
                })

            # 场景 3: 召回率低 -> 漏报多
            if stats["recall"] < 0.5:
                suggestions.append({
                    "rule_name": rule_name,
                    "issue": "low_recall",
                    "severity": "high",
                    "recall": round(stats["recall"], 3),
                    "suggestion": "考虑降低检测阈值或增加检测维度",
                })

        return suggestions

    def apply_optimization(self, rule_name: str, optimization: Dict) -> Dict:
        """
        应用优化建议到规则

        Args:
            rule_name: 规则名称
            optimization: 优化建议

        Returns:
            更新结果
        """
        current_rule = self.yaml_manager.get(rule_name, {})

        # 基于建议生成新参数
        updates = {}

        if optimization.get("issue") == "high_false_positive_rate":
            # 提高风险分要求
            current_score = current_rule.get("risk_score", 70)
            updates["risk_score"] = min(current_score + 5, 95)

        elif optimization.get("issue") == "low_precision_high_recall":
            current_score = current_rule.get("risk_score", 70)
            updates["risk_score"] = min(current_score + 10, 95)

        elif optimization.get("issue") == "low_recall":
            # 降低检测阈值
            if "threshold" in current_rule:
                updates["threshold"] = current_rule["threshold"] * 0.9
            elif "amount_range" in current_rule:
                amount_range = current_rule["amount_range"]
                updates["amount_range"] = [
                    amount_range[0] * 1.1,  # 扩大下限
                    amount_range[1] * 1.1,  # 扩大上限
                ]

        if updates:
            result = self.yaml_manager.update_rule(rule_name, updates)
            return {
                "success": True,
                "rule_name": rule_name,
                "updates": updates,
                "new_rule": result,
            }

        return {"success": False, "reason": "无适用的优化策略"}


# ============================================================
# 3. A/B 测试器
# ============================================================

class RuleABTest:
    """
    规则 A/B 测试器

    对比新旧规则的检测效果:
    1. 将流量分为两组
    2. A 组使用现有规则
    3. B 组使用新规则
    4. 对比两组的精确率、召回率、F1
    """

    def __init__(self, rule_yaml_manager: RuleYAMLManager):
        self.manager = rule_yaml_manager
        self._experiments: Dict[str, Dict] = {}

    def create_experiment(
        self,
        experiment_id: str,
        rule_name: str,
        new_params: Dict[str, Any],
        traffic_split: float = 0.2,
    ) -> Dict:
        """
        创建 A/B 实验

        Args:
            experiment_id: 实验ID
            rule_name: 要测试的规则
            new_params: 新规则参数
            traffic_split: B 组流量比例

        Returns:
            实验信息
        """
        self._experiments[experiment_id] = {
            "rule_name": rule_name,
            "new_params": new_params,
            "traffic_split": traffic_split,
            "control_hits": 0,
            "treatment_hits": 0,
            "control_tp": 0,
            "control_fp": 0,
            "treatment_tp": 0,
            "treatment_fp": 0,
            "started_at": datetime.now().isoformat(),
            "status": "running",
        }

        print(f"A/B 实验创建: {experiment_id}")
        print(f"  规则: {rule_name}")
        print(f"  新参数: {new_params}")
        print(f"  B组流量: {traffic_split * 100:.0f}%")

        return self._experiments[experiment_id]

    def assign_group(self, experiment_id: str, transaction_id: str) -> str:
        """
        将交易分配到对照组或实验组

        Args:
            experiment_id: 实验ID
            transaction_id: 交易ID

        Returns:
            'control' 或 'treatment'
        """
        if experiment_id not in self._experiments:
            return "control"

        exp = self._experiments[experiment_id]
        # 基于 transaction_id 哈希确定性分组
        hash_val = hash(transaction_id) % 100 / 100.0
        if hash_val < exp["traffic_split"]:
            return "treatment"
        return "control"

    def record_result(
        self,
        experiment_id: str,
        group: str,
        was_flagged: bool,
        is_fraud: bool,
    ):
        """记录实验结果"""
        if experiment_id not in self._experiments:
            return

        exp = self._experiments[experiment_id]

        if group == "control":
            exp["control_hits"] += 1
            if was_flagged and is_fraud:
                exp["control_tp"] += 1
            elif was_flagged and not is_fraud:
                exp["control_fp"] += 1
        else:
            exp["treatment_hits"] += 1
            if was_flagged and is_fraud:
                exp["treatment_tp"] += 1
            elif was_flagged and not is_fraud:
                exp["treatment_fp"] += 1

    def get_results(self, experiment_id: str) -> Dict:
        """获取实验结果"""
        if experiment_id not in self._experiments:
            return {"error": "实验不存在"}

        exp = self._experiments[experiment_id]

        # 计算指标
        def calc_metrics(tp, fp, total):
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0
            recall = tp / total if total > 0 else 0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
            return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}

        control_total = exp["control_tp"] + exp["control_fp"]
        treatment_total = exp["treatment_tp"] + exp["treatment_fp"]

        control_metrics = calc_metrics(exp["control_tp"], exp["control_fp"], control_total)
        treatment_metrics = calc_metrics(exp["treatment_tp"], exp["treatment_fp"], treatment_total)

        # 判断是否有显著差异
        improved = treatment_metrics["f1"] > control_metrics["f1"] + 0.02

        return {
            "experiment_id": experiment_id,
            "rule_name": exp["rule_name"],
            "status": exp["status"],
            "control": {
                "hits": exp["control_hits"],
                "tp": exp["control_tp"],
                "fp": exp["control_fp"],
                "metrics": control_metrics,
            },
            "treatment": {
                "hits": exp["treatment_hits"],
                "tp": exp["treatment_tp"],
                "fp": exp["treatment_fp"],
                "metrics": treatment_metrics,
            },
            "improved": improved,
            "recommendation": "采用新规则" if improved else "保持现有规则",
        }


# ============================================================
# 4. 便捷函数
# ============================================================

def create_rule_management_system(
    config_path: Optional[str] = None,
    defaults: Optional[Dict[str, Any]] = None,
):
    """
    创建完整的规则管理系统

    Args:
        config_path: YAML 规则配置文件路径
        defaults: 真实规则默认值（来自 AML_CONFIG["rules"]），避免编造阈值（戒律 M1）

    Returns:
        (yaml_manager, tuner, ab_tester)
    """
    yaml_manager = RuleYAMLManager(config_path, defaults=defaults)
    yaml_manager.load()

    tuner = AdaptiveRuleTuner(yaml_manager)
    ab_tester = RuleABTest(yaml_manager)

    return yaml_manager, tuner, ab_tester


if __name__ == "__main__":
    print("=" * 60)
    print("YAML 规则配置系统 - 测试")
    print("=" * 60)

    # 1. 创建系统
    yaml_mgr, tuner, ab_tester = create_rule_management_system()

    # 2. 查看规则
    rules = yaml_mgr.load()
    print("\n加载的规则:")
    for name, rule in rules.items():
        print(f"  {name}: {rule.get('description', 'N/A')}")

    # 3. 模拟反馈
    print("\n模拟反馈数据...")
    for i in range(100):
        tuner.record_feedback(
            rule_name="smurfing",
            transaction_id=f"TXN-{i:04d}",
            is_correct=random.random() > 0.3,
            was_flagged=random.random() > 0.4,
            actual_fraud=random.random() > 0.7,
        )

    # 4. 获取统计
    stats = tuner.get_rule_stats("smurfing")
    print(f"\nSmurfing 规则统计:")
    for key, val in stats.items():
        if isinstance(val, float):
            print(f"  {key}: {val:.4f}")
        else:
            print(f"  {key}: {val}")

    # 5. 获取优化建议
    suggestions = tuner.suggest_optimizations(min_feedback=30)
    print(f"\n优化建议: {len(suggestions)} 条")
    for s in suggestions:
        print(f"  - {s['rule_name']}: {s['suggestion']}")

    # 6. A/B 测试演示
    print("\nA/B 测试演示...")
    exp = ab_tester.create_experiment(
        "exp_001", "smurfing",
        new_params={"hour_window": 2, "min_count": 4},
        traffic_split=0.3,
    )

    for i in range(50):
        group = ab_tester.assign_group("exp_001", f"TXN-AB-{i:04d}")
        ab_tester.record_result(
            "exp_001", group,
            was_flagged=random.random() > 0.5,
            is_fraud=random.random() > 0.6,
        )

    results = ab_tester.get_results("exp_001")
    print(f"\n实验结果:")
    print(f"  对照组 F1: {results['control']['metrics']['f1']}")
    print(f"  实验组 F1: {results['treatment']['metrics']['f1']}")
    print(f"  建议: {results['recommendation']}")

    print("\n✅ 规则管理系统测试完成!")
