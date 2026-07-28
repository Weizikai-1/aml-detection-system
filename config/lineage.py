"""
数据血缘追踪配置
"""
LINEAGE_CONFIG = {
    "enabled": True,
    "retain_days": 90,
    "index_by_report": True,
    "index_by_transaction": True,
    "max_index_entries": 10000,
}
