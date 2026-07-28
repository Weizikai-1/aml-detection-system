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
