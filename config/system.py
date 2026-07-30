"""
系统配置 - 合并 gnn/report/cache/risk/paths（消除5个微型文件）
"""
import os

# ============================================================
# 路径
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
FEEDBACK_DIR = os.path.join(DATA_DIR, "feedback")
EXPORTS_DIR = os.path.join(PROJECT_ROOT, "exports")

# ============================================================
# GNN
# ============================================================
GNN_CONFIG = {
    "hidden_dim": 64,
    "num_layers": 2,
    "dropout": 0.5,
    "epochs": 200,
    "learning_rate": 0.01,
}

# ============================================================
# 报告
# ============================================================
REPORT_CONFIG = {
    "format": "markdown",
    "include_evidence": True,
    "include_risk_score": True,
    "max_transactions_per_report": 10,
}

# ============================================================
# 缓存
# ============================================================
CACHE_CONFIG = {
    "enabled": os.getenv("CACHE_ENABLED", "false").lower() == "true",
    "expire_days": 7,
    "max_size_mb": 100,
    "llm_cache_enabled": os.getenv("LLM_CACHE_ENABLED", "false").lower() == "true",
    "llm_cache_expire_hours": 24,
}

# ============================================================
# 风险评分
# ============================================================
RISK_CONFIG = {
    "score_scale": 100,
    "levels": {"critical": 85, "high": 70, "medium": 50, "low": 0},
    "llm_weight": 0.4,
    "suspicious_threshold": 60,
}
