"""
风险评分配置
"""
RISK_CONFIG = {
    "score_scale": 100,
    "levels": {
        "critical": 85,
        "high": 70,
        "medium": 50,
        "low": 0,
    },
    "llm_weight": 0.4,
    "suspicious_threshold": 60,
}
