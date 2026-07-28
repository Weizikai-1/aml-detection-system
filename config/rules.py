"""
规则引擎配置
"""
RULES_CONFIG = {
    "fast_in_fast_out_minutes": 10,
    "fast_in_fast_out_ratio": 0.95,
    "smurfing_hour_window": 1,
    "smurfing_min_count": 5,
    "smurfing_amount_range": (40000, 50000),
    "round_trip_max_days": 7,
    "large_amount_threshold": 100000,
    "baseline_deviation": {
        "min_txns_for_baseline": 5,
        "amount_zscore_threshold": 3.0,
        "night_activity_boost": True,
        "new_counterparty_weight": 0.2,
        "max_risk_score": 60,
    },
    "remark_keywords": {
        "enabled": True,
        "risk_score": 55,
        "high_risk_keywords": [
            "过账", "走账", "代付", "代收", "刷单", "套现",
            "跑分", "洗钱", "地下钱庄", "对敲", "空转",
            "测试", "虚拟币", "BTC", "USDT", "币安",
        ],
        "low_risk_keywords": [
            "工资", "薪资", "奖金", "报销", "差旅",
            "货款", "采购款", "订单", "发票", "还款",
            "房租", "水电", "物业费", "学费", "医药费",
            "餐费", "餐饮", "购物", "消费", "退款",
            "理财", "基金", "存款", "利息", "分红",
        ],
        "low_risk_discount": 0.6,
    },
    "shell_company": {
        "enabled": True,
        "risk_score": 75,
        "min_total_txns": 8,
        "min_counterparties": 5,
        "max_retention_rate": 0.15,
        "night_ratio_threshold": 0.4,
        "fast_turnover_ratio_threshold": 0.5,
        "required_dimensions": 3,
    },
    "sanction_list": {
        "enabled": True,
        "ofac_risk_score": 95,
        "pboc_risk_score": 90,
        "custom_risk_score": 95,
        "country_risk_score": 90,
        "crypto_risk_score": 95,
    },
    "cross_border": {
        "enabled": True,
        "min_amount": 50000,
        "frequent_count": 5,
        "frequent_days": 7,
        "split_threshold": 200000,
        "split_count": 3,
        "risk_score": 65,
        "high_risk_score": 80,
        "high_risk_regions": [
            "AE", "SG", "HK", "MO",
            "PA", "KY", "VG", "BVI",
        ],
    },
    "crypto_pattern": {
        "enabled": True,
        "otc_hub_min_in": 3,
        "otc_hub_min_out": 3,
        "otc_window_hours": 24,
        "otc_risk_score": 80,
        "mixer_min_in_count": 5,
        "mixer_max_in_amount": 10000,
        "mixer_min_out_amount": 50000,
        "mixer_window_minutes": 30,
        "mixer_risk_score": 85,
        "fx_keywords": ["换u", "收u", "出u", "收usdt", "出usdt", "换usdt",
                        "买币", "卖币", "买btc", "卖btc", "收btc", "出btc",
                        "场外", "otc交易", "币商", "承兑商"],
        "fx_min_count": 3,
        "fx_window_hours": 24,
        "fx_min_amount": 5000,
        "fx_risk_score": 70,
        "known_platform_keywords": [
            "binance", "huobi", "okex", "okx", "coinbase", "kraken",
            "gate.io", "bybit", "kucoin", "bitfinex", "抹茶", "芝麻开门",
        ],
        "platform_risk_score": 75,
    },
}

RULE_AUTO_LEARN_CONFIG = {
    "enabled": True,
    "min_feedback_count": 10,
    "fp_rate_threshold": 0.3,
    "fn_rate_threshold": 0.2,
    "max_adjust_ratio": 0.2,
    "high_risk_drop_limit": 0.3,
    "suggestion_ttl_days": 30,
    "conflict_warning_only": True,
    "algorithm_version": "1.0.0",
}

from tools.geo_risk_scorer import GEO_RISK_CONFIG as _GEO_RISK_CONFIG
GEO_RISK_CONFIG = _GEO_RISK_CONFIG
