"""
统一配置入口
整合所有配置为 AML_CONFIG 字典
"""
from config.llm import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, LLM_CONFIG
from config.rules import RULES_CONFIG
from config.risk import RISK_CONFIG
from config.gnn import GNN_CONFIG
from config.report import REPORT_CONFIG
from config.aml_rules import AML_RULES
from config.cache import CACHE_CONFIG
from config.paths import DATA_DIR, REPORTS_DIR, LOGS_DIR, CACHE_DIR, HISTORY_DIR, FEEDBACK_DIR, EXPORTS_DIR

AML_CONFIG = {
    "llm": {
        "api_key": DEEPSEEK_API_KEY,
        "base_url": DEEPSEEK_BASE_URL,
        "model": DEEPSEEK_MODEL,
        **LLM_CONFIG,
    },
    "rules": {
        "smurfing": {
            "hour_window": RULES_CONFIG["smurfing_hour_window"],
            "min_count": RULES_CONFIG["smurfing_min_count"],
            "amount_low": RULES_CONFIG["smurfing_amount_range"][0],
            "amount_high": RULES_CONFIG["smurfing_amount_range"][1],
            "risk_score": 70,
        },
        "fast_in_fast_out": {
            "max_minutes": RULES_CONFIG["fast_in_fast_out_minutes"],
            "min_ratio": RULES_CONFIG["fast_in_fast_out_ratio"],
            "min_amount": 10000,
            "risk_score_primary": 60,
            "risk_score_secondary": 50,
        },
        "round_trip": {
            "max_days": RULES_CONFIG["round_trip_max_days"],
            "max_amount_diff_ratio": 0.2,
            "min_amount": 10000,
            "risk_score": 65,
        },
        "large_amount": {
            "threshold": RULES_CONFIG["large_amount_threshold"],
            "risk_score": 40,
        },
        "baseline_deviation": RULES_CONFIG["baseline_deviation"],
        "remark_keywords": RULES_CONFIG["remark_keywords"],
        "shell_company": RULES_CONFIG["shell_company"],
        "sanction_list": RULES_CONFIG["sanction_list"],
        "cross_border": RULES_CONFIG["cross_border"],
        "crypto_pattern": RULES_CONFIG["crypto_pattern"],
    },
    "risk": RISK_CONFIG,
    "gnn": GNN_CONFIG,
    "report": REPORT_CONFIG,
    "aml_rules": AML_RULES,
    "workflow": {
        "min_rule_hits_for_graph": 1,
    },
    "cache": CACHE_CONFIG,
    "paths": {
        "data_dir": DATA_DIR,
        "reports_dir": REPORTS_DIR,
        "logs_dir": LOGS_DIR,
        "cache_dir": CACHE_DIR,
        "history_dir": HISTORY_DIR,
        "feedback_dir": FEEDBACK_DIR,
        "exports_dir": EXPORTS_DIR,
    },
}


# ============================================================
# P0-5: YAML 规则覆盖硬编码 config
# ============================================================
# 设计原则（戒律 M1: 不编造数据）:
#   1. AML_CONFIG["rules"] 硬编码值作为安全兜底默认值
#   2. RuleYAMLManager 以 AML_CONFIG["rules"] 为 defaults，生成 YAML 文件时写入真实值
#   3. 加载 YAML 后深度合并覆盖 AML_CONFIG["rules"]，YAML 成为权威配置层
#   4. 失败安全降级: YAML 加载失败时保持 AML_CONFIG 硬编码值不变（戒律 P1: 不阻塞主流程）
#
# 行为:
#   - 首次运行（无 YAML）: 用 AML_CONFIG 真实值生成 YAML，合并后无变化
#   - 用户编辑 YAML: 下次启动 YAML 值覆盖 AML_CONFIG，实现动态配置
#   - YAML 损坏: 回退到 AML_CONFIG 硬编码值，系统仍可运行
def _deep_merge_rules(base: dict, overlay: dict) -> dict:
    """深度合并: overlay 递归覆盖 base，仅在 base 已有键时合并嵌套 dict（避免污染）"""
    for k, v in overlay.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge_rules(base[k], v)
        else:
            base[k] = v
    return base


def _apply_yaml_rules_override():
    """加载 YAML 规则并深度合并覆盖 AML_CONFIG['rules']（失败安全降级）"""
    try:
        from config.rules_yaml import RuleYAMLManager
        # 以 AML_CONFIG["rules"] 为真实默认值（戒律 M1: 不编造阈值）
        manager = RuleYAMLManager(defaults=AML_CONFIG["rules"])
        yaml_rules = manager.load()
        # 深度合并: YAML 覆盖 AML_CONFIG 硬编码值
        _deep_merge_rules(AML_CONFIG["rules"], yaml_rules)
        # 暴露 manager 供运行时热更新（reload_if_needed）
        AML_CONFIG["_rule_yaml_manager"] = manager
    except Exception as e:
        # 安全降级: YAML 加载失败时保持 AML_CONFIG 硬编码值（戒律 P1: 不阻塞）
        print(f"[配置] YAML 规则加载跳过，使用硬编码默认值: {e}")


_apply_yaml_rules_override()


def check_config():
    from config.llm import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
    from config.paths import DATA_DIR, REPORTS_DIR
    if not DEEPSEEK_API_KEY:
        print("错误: 未配置 DEEPSEEK_API_KEY")
        print("请复制 .env.example 为 .env,并填入你的 DeepSeek API Key")
        return False
    print("配置检查通过")
    print(f"  - LLM 模型: {DEEPSEEK_MODEL}")
    print(f"  - 数据目录: {DATA_DIR}")
    print(f"  - 报告目录: {REPORTS_DIR}")
    return True
