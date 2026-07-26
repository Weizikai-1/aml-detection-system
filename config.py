"""
项目配置文件
集中管理所有配置项
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ============ LLM 配置 ============
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# LLM 调用参数
LLM_CONFIG = {
    "temperature": 0.1,      # 低温度保证一致性
    "max_tokens": 2000,       # 单次最大输出
    "timeout": 60,            # 超时秒数
    "retry_times": 3,         # 重试次数
}

# ============ 路径配置 ============
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")

# ============ 反洗钱业务配置 ============
# 报告阈值(单笔超过此金额需报告,单位:元)
REPORT_THRESHOLD = 50000

# 规则引擎参数
RULES_CONFIG = {
    # 快进快出规则
    "fast_in_fast_out_minutes": 10,          # 资金停留最大分钟数
    "fast_in_fast_out_ratio": 0.95,          # 转出金额占收款比例
    # 分拆转账规则
    "smurfing_hour_window": 1,               # 时间窗口(小时)
    "smurfing_min_count": 5,                 # 最小交易次数
    "smurfing_amount_range": (40000, 50000), # 金额范围
    # 对敲交易规则
    "round_trip_max_days": 7,                # 反向转账最大间隔天数
    # 大额交易规则
    "large_amount_threshold": 100000,        # 大额交易阈值
}

# GNN 图分析配置(第2周用)
GNN_CONFIG = {
    "hidden_dim": 64,
    "num_layers": 2,
    "dropout": 0.2,
    "epochs": 50,
    "learning_rate": 0.01,
}

# STR 报告配置
REPORT_CONFIG = {
    "format": "markdown",                    # 输出格式
    "include_evidence": True,                # 包含证据链
    "include_risk_score": True,              # 包含风险评分
    "max_transactions_per_report": 10,       # 单份报告最大交易数
}

# ============ 统一配置入口 ============
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
        },
        "fast_in_fast_out": {
            "max_minutes": RULES_CONFIG["fast_in_fast_out_minutes"],
            "min_ratio": RULES_CONFIG["fast_in_fast_out_ratio"],
            "min_amount": 10000,  # 低于此金额不触发快进快出检测
        },
        "round_trip": {
            "max_days": RULES_CONFIG["round_trip_max_days"],
            "max_amount_diff_ratio": 0.2,  # 金额差异最大比例
            "min_amount": 10000,
        },
        "large_amount": {
            "threshold": RULES_CONFIG["large_amount_threshold"],
        },
    },
    "gnn": GNN_CONFIG,
    "report": REPORT_CONFIG,
    "workflow": {
        "min_rule_hits_for_graph": 1,  # 触发图分析的最小规则命中数
    },
    "paths": {
        "data_dir": DATA_DIR,
        "reports_dir": REPORTS_DIR,
        "logs_dir": LOGS_DIR,
    },
}


def check_config():
    """检查配置是否完整"""
    if not DEEPSEEK_API_KEY:
        print("错误: 未配置 DEEPSEEK_API_KEY")
        print("请复制 .env.example 为 .env,并填入你的 DeepSeek API Key")
        return False

    print("配置检查通过")
    print(f"  - LLM 模型: {DEEPSEEK_MODEL}")
    print(f"  - 数据目录: {DATA_DIR}")
    print(f"  - 报告目录: {REPORTS_DIR}")
    print(f"  - 报告阈值: {REPORT_THRESHOLD} 元")
    return True


if __name__ == "__main__":
    check_config()
