"""
规则调参管理器

功能:
- 加载/保存规则引擎参数配置
- 参数校验与戒律守护
- 调参前后效果对比（基于真实交易数据）
- 配置版本管理与回滚

严格遵守戒律:
- M1: 调参效果对比基于真实交易数据，不编造结果
- P1: 调参后高风险命中数下降超过30%时警告（不遗漏）
- P2: 调参后总命中数激增超过200%时警告（不误报）
- M4: 每次调参保存完整快照，可追溯
"""
import os
import json
import copy
import threading
from datetime import datetime
from contextlib import contextmanager
from typing import Dict, Any, List, Tuple, Optional

from config import AML_CONFIG, RULE_TUNING_DIR


# 高风险交易判定线（与 RISK_CONFIG.levels.high 对齐）
HIGH_RISK_SCORE_THRESHOLD = 70

# 戒律守护阈值
RISK_DROP_WARNING_RATIO = 0.30   # 高风险命中数下降超过30%警告（戒律 P1）
HITS_SURGE_WARNING_RATIO = 2.0   # 总命中数激增超过200%警告（戒律 P2）

# 戒律 M4: 全局锁，保证 _temporary_config 修改 AML_CONFIG['rules'] 期间不被并发规则运行污染
_CONFIG_SWAP_LOCK = threading.Lock()


class RuleTuner:
    """
    规则调参管理器

    用法:
        tuner = RuleTuner()

        # 1. 获取当前可调参数
        params = tuner.get_tunable_params()

        # 2. 用户修改参数
        new_params = {...}

        # 3. 校验参数
        is_valid, errors, warnings = tuner.validate_params(new_params)

        # 4. 对比效果（基于真实交易数据）
        comparison = tuner.compare_effect(transactions, new_params)

        # 5. 保存配置
        tuner.save_config("v2_strict", new_params, description="收紧分拆转账阈值")

        # 6. 应用配置（影响后续分析）
        tuner.apply_config(new_params)
    """

    # 可调参数定义：分组、字段名、类型、范围、默认值、说明
    TUNABLE_SCHEMA: Dict[str, Any] = {
        "smurfing": {
            "label": "分拆转账",
            "params": {
                "hour_window": {
                    "type": "int", "min": 1, "max": 24, "default": 1,
                    "desc": "时间窗口（小时）",
                },
                "min_count": {
                    "type": "int", "min": 2, "max": 20, "default": 5,
                    "desc": "最小交易笔数",
                },
                "amount_low": {
                    "type": "float", "min": 1000, "max": 100000, "default": 40000,
                    "desc": "金额下限（元）",
                },
                "amount_high": {
                    "type": "float", "min": 1000, "max": 200000, "default": 50000,
                    "desc": "金额上限（元）",
                },
                "risk_score": {
                    "type": "int", "min": 0, "max": 100, "default": 70,
                    "desc": "命中风险分",
                },
            },
        },
        "fast_in_fast_out": {
            "label": "快进快出",
            "params": {
                "max_minutes": {
                    "type": "int", "min": 1, "max": 1440, "default": 10,
                    "desc": "最大停留时间（分钟）",
                },
                "min_ratio": {
                    "type": "float", "min": 0.5, "max": 1.0, "default": 0.95,
                    "desc": "转出占比阈值",
                },
                "min_amount": {
                    "type": "float", "min": 0, "max": 1000000, "default": 10000,
                    "desc": "最小入账金额（元）",
                },
                "risk_score_primary": {
                    "type": "int", "min": 0, "max": 100, "default": 60,
                    "desc": "主交易风险分",
                },
                "risk_score_secondary": {
                    "type": "int", "min": 0, "max": 100, "default": 50,
                    "desc": "关联交易风险分",
                },
            },
        },
        "round_trip": {
            "label": "对敲交易",
            "params": {
                "max_days": {
                    "type": "int", "min": 1, "max": 90, "default": 7,
                    "desc": "最大间隔天数",
                },
                "max_amount_diff_ratio": {
                    "type": "float", "min": 0.0, "max": 1.0, "default": 0.2,
                    "desc": "金额差异比例上限",
                },
                "min_amount": {
                    "type": "float", "min": 0, "max": 1000000, "default": 10000,
                    "desc": "最小交易金额（元）",
                },
                "risk_score": {
                    "type": "int", "min": 0, "max": 100, "default": 65,
                    "desc": "命中风险分",
                },
            },
        },
        "large_amount": {
            "label": "大额交易",
            "params": {
                "threshold": {
                    "type": "float", "min": 10000, "max": 10000000, "default": 100000,
                    "desc": "大额交易阈值（元）",
                },
                "risk_score": {
                    "type": "int", "min": 0, "max": 100, "default": 40,
                    "desc": "命中风险分",
                },
            },
        },
        "baseline_deviation": {
            "label": "基线偏离",
            "params": {
                "min_txns_for_baseline": {
                    "type": "int", "min": 3, "max": 50, "default": 5,
                    "desc": "计算基线所需最少交易笔数",
                },
                "amount_zscore_threshold": {
                    "type": "float", "min": 1.0, "max": 10.0, "default": 3.0,
                    "desc": "Z-score 触发阈值",
                },
                "max_risk_score": {
                    "type": "int", "min": 0, "max": 100, "default": 60,
                    "desc": "最高风险分上限",
                },
            },
        },
    }

    def __init__(self, storage_dir: str = ""):
        """
        Args:
            storage_dir: 配置存储目录，默认使用 RULE_TUNING_DIR
        """
        self.storage_dir = storage_dir or RULE_TUNING_DIR
        os.makedirs(self.storage_dir, exist_ok=True)

    # ============================================================
    # 参数读取
    # ============================================================
    def get_tunable_params(self) -> Dict[str, Any]:
        """获取当前生效的可调参数"""
        params: Dict[str, Any] = {}
        rules_cfg = AML_CONFIG["rules"]
        for group, schema in self.TUNABLE_SCHEMA.items():
            params[group] = {}
            group_cfg = rules_cfg.get(group, {})
            for key in schema["params"]:
                params[group][key] = copy.deepcopy(group_cfg.get(key))
        return params

    def get_defaults(self) -> Dict[str, Any]:
        """获取默认参数（来自 schema）"""
        params: Dict[str, Any] = {}
        for group, schema in self.TUNABLE_SCHEMA.items():
            params[group] = {}
            for key, spec in schema["params"].items():
                params[group][key] = spec["default"]
        return params

    def get_param_metadata(self) -> Dict[str, Any]:
        """获取参数元数据（用于UI渲染）"""
        metadata = {}
        for group, schema in self.TUNABLE_SCHEMA.items():
            metadata[group] = {
                "label": schema["label"],
                "params": schema["params"],
            }
        return metadata

    # ============================================================
    # 参数校验
    # ============================================================
    def validate_params(
        self,
        params: Dict[str, Any],
    ) -> Tuple[bool, List[str], List[str]]:
        """
        校验参数

        Returns:
            (is_valid, errors, warnings)
            - is_valid: True 表示参数可应用
            - errors: 阻断性错误列表
            - warnings: 警告列表（不阻断）
        """
        errors: List[str] = []
        warnings: List[str] = []

        for group, group_params in params.items():
            if group not in self.TUNABLE_SCHEMA:
                errors.append(f"未知参数组: {group}")
                continue

            if not isinstance(group_params, dict):
                errors.append(f"参数组 {group} 必须是字典")
                continue

            schema = self.TUNABLE_SCHEMA[group]
            for key, value in group_params.items():
                if key not in schema["params"]:
                    errors.append(f"未知参数: {group}.{key}")
                    continue

                spec = schema["params"][key]

                # 类型检查
                if spec["type"] == "int":
                    # 戒律 P4: int 类型必须额外拒绝 float，避免 3.14 被误判为合法整数
                    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, int):
                        errors.append(f"{group}.{key} 必须是整数（当前: {value!r}）")
                        continue
                elif spec["type"] == "float":
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        errors.append(f"{group}.{key} 必须是数字（当前: {value!r}）")
                        continue

                # 范围检查
                if value < spec["min"]:
                    errors.append(f"{group}.{key}={value} 低于最小值 {spec['min']}")
                if value > spec["max"]:
                    errors.append(f"{group}.{key}={value} 超过最大值 {spec['max']}")

        # 依赖关系校验
        if "smurfing" in params and isinstance(params["smurfing"], dict):
            sm = params["smurfing"]
            if sm.get("amount_low", 0) > sm.get("amount_high", 0):
                errors.append("分拆转账: amount_low 不能大于 amount_high")

        # 戒律守护警告
        warnings.extend(self._guardian_warnings(params))

        return (len(errors) == 0, errors, warnings)

    def _guardian_warnings(self, params: Dict[str, Any]) -> List[str]:
        """戒律守护：对激进调参给出警告（类型错误的参数跳过，由 validate_params 主流程报错）"""
        warnings: List[str] = []
        defaults = self.get_defaults()

        def _is_number(v) -> bool:
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        # 大额交易阈值（戒律 P1：不遗漏）
        if "large_amount" in params:
            la = params.get("large_amount", {})
            default_threshold = defaults["large_amount"]["threshold"]
            new_threshold = la.get("threshold", default_threshold)
            if _is_number(new_threshold) and new_threshold > default_threshold * 2:
                warnings.append(
                    f"大额交易阈值提高到 {new_threshold:,.0f} 元（默认 {default_threshold:,.0f}），"
                    f"可能遗漏大额可疑交易（戒律 P1: 不遗漏）"
                )

        # 快进快出转出占比（戒律 P2：不误报）
        if "fast_in_fast_out" in params:
            fi = params.get("fast_in_fast_out", {})
            default_ratio = defaults["fast_in_fast_out"]["min_ratio"]
            new_ratio = fi.get("min_ratio", default_ratio)
            if _is_number(new_ratio) and new_ratio < 0.7:
                warnings.append(
                    f"快进快出转出占比阈值降至 {new_ratio:.2f}，"
                    f"可能产生大量误报（戒律 P2: 不误报）"
                )

        # 分拆转账最小笔数（达到 2 倍即警告）
        if "smurfing" in params:
            sm = params.get("smurfing", {})
            default_count = defaults["smurfing"]["min_count"]
            new_count = sm.get("min_count", default_count)
            if _is_number(new_count) and new_count >= default_count * 2:
                warnings.append(
                    f"分拆转账最小笔数提高到 {new_count}（默认 {default_count}），"
                    f"可能遗漏分拆可疑交易（戒律 P1: 不遗漏）"
                )

        # 风险分过低
        for group in ["smurfing", "fast_in_fast_out", "round_trip", "large_amount"]:
            if group in params:
                gp = params.get(group, {})
                for score_key in ["risk_score", "risk_score_primary"]:
                    if score_key in gp:
                        v = gp[score_key]
                        if _is_number(v) and v < 30:
                            warnings.append(
                                f"{group}.{score_key}={v} 过低，"
                                f"规则命中后风险评级偏低（戒律 M3: 风险评分应反映可疑程度）"
                            )

        return warnings

    # ============================================================
    # 配置临时替换（用于效果对比）
    # ============================================================
    @contextmanager
    def _temporary_config(self, new_rules: Dict[str, Any]):
        """
        临时替换 AML_CONFIG['rules']，退出时恢复

        戒律 M4: 确保调参对比不影响其他正在运行的分析
        戒律 P4: 通过全局锁阻止并发规则运行，避免线程安全问题
        """
        import config as cfg_module
        # 获取锁后才能修改全局配置，阻止并发规则运行
        _CONFIG_SWAP_LOCK.acquire()
        original = copy.deepcopy(cfg_module.AML_CONFIG["rules"])
        try:
            merged = copy.deepcopy(original)
            for group, group_params in new_rules.items():
                if group not in merged:
                    merged[group] = {}
                merged[group].update(group_params)
            cfg_module.AML_CONFIG["rules"] = merged
            yield
        finally:
            cfg_module.AML_CONFIG["rules"] = original
            _CONFIG_SWAP_LOCK.release()

    # ============================================================
    # 调参效果对比
    # ============================================================
    def compare_effect(
        self,
        transactions: List[Any],
        new_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        对比调参前后效果（基于真实交易数据）

        戒律 M1: 基于真实数据计算，不编造
        戒律 P1/P2: 高风险下降/总命中激增时给出警告

        Args:
            transactions: 真实交易列表
            new_params: 新参数

        Returns:
            {
                "before": {"rule_counts": {...}, "total_hits": N, "high_risk_hits": N},
                "after":  {"rule_counts": {...}, "total_hits": N, "high_risk_hits": N},
                "diff":   {"total_hits_delta": N, "high_risk_hits_delta": N},
                "warnings": [...],
            }
        """
        # 先校验参数
        is_valid, errors, _ = self.validate_params(new_params)
        if not is_valid:
            raise ValueError(f"参数校验失败: {'; '.join(errors)}")

        before_hits = self._run_rules(transactions, None)
        after_hits = self._run_rules(transactions, new_params)

        before_total = sum(len(v) for v in before_hits.values())
        after_total = sum(len(v) for v in after_hits.values())

        before_high_risk = sum(
            1 for rule_hits in before_hits.values()
            for s in rule_hits
            if s.get("risk_score", 0) >= HIGH_RISK_SCORE_THRESHOLD
        )
        after_high_risk = sum(
            1 for rule_hits in after_hits.values()
            for s in rule_hits
            if s.get("risk_score", 0) >= HIGH_RISK_SCORE_THRESHOLD
        )

        warnings: List[str] = []

        # 戒律 P1: 高风险命中数下降警告
        if before_high_risk > 0 and after_high_risk < before_high_risk:
            drop_ratio = (before_high_risk - after_high_risk) / before_high_risk
            if drop_ratio >= RISK_DROP_WARNING_RATIO:
                warnings.append(
                    f"高风险命中数从 {before_high_risk} 降至 {after_high_risk} "
                    f"（下降 {drop_ratio*100:.0f}%），可能遗漏高风险交易（戒律 P1）"
                )

        # 戒律 P2: 总命中数激增警告
        if before_total > 0 and after_total > before_total:
            surge_ratio = after_total / before_total
            if surge_ratio >= HITS_SURGE_WARNING_RATIO:
                warnings.append(
                    f"总命中数从 {before_total} 升至 {after_total} "
                    f"（激增 {(surge_ratio-1)*100:.0f}%），可能产生大量误报（戒律 P2）"
                )

        # 规则失效警告
        for rule_name, before_list in before_hits.items():
            before_n = len(before_list)
            after_n = len(after_hits.get(rule_name, []))
            if before_n > 0 and after_n == 0:
                warnings.append(
                    f"规则 [{rule_name}] 调参后不再命中任何交易（原命中 {before_n} 笔），"
                    f"请确认参数是否合理"
                )

        return {
            "before": {
                "rule_counts": {k: len(v) for k, v in before_hits.items()},
                "total_hits": before_total,
                "high_risk_hits": before_high_risk,
            },
            "after": {
                "rule_counts": {k: len(v) for k, v in after_hits.items()},
                "total_hits": after_total,
                "high_risk_hits": after_high_risk,
            },
            "diff": {
                "total_hits_delta": after_total - before_total,
                "high_risk_hits_delta": after_high_risk - before_high_risk,
            },
            "warnings": warnings,
        }

    def _run_rules(
        self,
        transactions: List[Any],
        new_params: Optional[Dict[str, Any]],
    ) -> Dict[str, List]:
        """在指定参数下运行规则引擎，返回各规则命中结果"""
        # 延迟导入避免循环依赖
        from agents.rule_engine import (
            _detect_smurfing,
            _detect_fast_in_fast_out,
            _detect_round_trip,
            _detect_large_amount,
            _detect_remark_keywords,
            _detect_shell_companies,
        )

        def _run_all() -> Dict[str, List]:
            return {
                "分拆转账": _detect_smurfing(transactions),
                "快进快出": _detect_fast_in_fast_out(transactions),
                "对敲交易": _detect_round_trip(transactions),
                "大额交易": _detect_large_amount(transactions),
                "备注关键词": _detect_remark_keywords(transactions),
                "空壳公司": _detect_shell_companies(transactions),
            }

        if new_params is None:
            return _run_all()

        # 临时替换配置
        with self._temporary_config(new_params):
            return _run_all()

    # ============================================================
    # 配置持久化
    # ============================================================
    def save_config(
        self,
        name: str,
        params: Dict[str, Any],
        description: str = "",
    ) -> str:
        """保存配置到文件"""
        is_valid, errors, _ = self.validate_params(params)
        if not is_valid:
            raise ValueError(f"参数校验失败: {'; '.join(errors)}")

        safe_name = self._safe_name(name)
        if not safe_name:
            raise ValueError("配置名称无效")

        # 戒律 M4: 多字段组合保证排序稳定
        # - created_at: ISO 展示用
        # - _created_at_ts: 纳秒级浮点秒（time.time_ns）
        # - _seq: 单调递增序号（同一纳秒内保存仍可区分）
        import time as _time
        now = datetime.now()
        seq = getattr(self, "_save_seq", 0) + 1
        self._save_seq = seq
        config_data = {
            "name": name,
            "description": description,
            "params": params,
            "created_at": now.isoformat(),
            "_created_at_ts": _time.time_ns() / 1e9,
            "_seq": seq,
            "schema_version": 1,
        }

        path = os.path.join(self.storage_dir, f"{safe_name}.json")
        # 戒律 M4: 捕获 IO/JSON 异常，避免崩溃且可追溯
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
        except (OSError, json.JSONDecodeError, TypeError) as e:
            raise RuntimeError(f"配置保存失败: {e}") from e
        return path

    def load_config(self, name: str) -> Dict[str, Any]:
        """加载配置"""
        safe_name = self._safe_name(name)
        path = os.path.join(self.storage_dir, f"{safe_name}.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"配置不存在: {name}")

        # 戒律 M4: 捕获 IO/JSON 异常，避免崩溃且可追溯
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"配置加载失败: {e}") from e

    def list_configs(self) -> List[Dict[str, Any]]:
        """列出所有已保存配置"""
        configs: List[Dict[str, Any]] = []
        if not os.path.exists(self.storage_dir):
            return configs

        for fname in os.listdir(self.storage_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.storage_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                configs.append({
                    "name": data.get("name", fname[:-5]),
                    "description": data.get("description", ""),
                    "created_at": data.get("created_at", ""),
                    "_created_at_ts": data.get("_created_at_ts", 0.0),
                    "_seq": data.get("_seq", 0),
                    "file": fname,
                })
            except (json.JSONDecodeError, IOError):
                continue

        # 戒律 M4: 多键组合排序
        # 1. 纳秒级 _created_at_ts 倒序
        # 2. 单调 _seq 倒序（同一纳秒内保证稳定）
        # 3. ISO created_at 字符串（缺失时的回退）
        configs.sort(
            key=lambda x: (
                x.get("_created_at_ts", 0.0),
                x.get("_seq", 0),
                x.get("created_at", ""),
            ),
            reverse=True,
        )
        return configs

    def delete_config(self, name: str) -> bool:
        """删除配置"""
        safe_name = self._safe_name(name)
        path = os.path.join(self.storage_dir, f"{safe_name}.json")
        if os.path.exists(path):
            os.unlink(path)
            return True
        return False

    def apply_config(self, params: Dict[str, Any]) -> None:
        """
        应用配置到运行时（影响后续分析）

        注意: 只影响当前进程，不会修改 config.py 文件
        """
        is_valid, errors, _ = self.validate_params(params)
        if not is_valid:
            raise ValueError(f"参数校验失败: {'; '.join(errors)}")

        import config as cfg_module
        for group, group_params in params.items():
            if group not in cfg_module.AML_CONFIG["rules"]:
                cfg_module.AML_CONFIG["rules"][group] = {}
            cfg_module.AML_CONFIG["rules"][group].update(group_params)

    def reset_to_defaults(self) -> Dict[str, Any]:
        """重置为默认参数并应用"""
        defaults = self.get_defaults()
        self.apply_config(defaults)
        return defaults

    @staticmethod
    def _safe_name(name: str) -> str:
        """生成安全的文件名（仅允许字母数字、下划线、连字符）"""
        return "".join(c for c in name if c.isalnum() or c in "-_").strip()
