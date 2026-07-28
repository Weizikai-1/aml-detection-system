"""
路径配置
"""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
FEEDBACK_DIR = os.path.join(DATA_DIR, "feedback")
RULE_TUNING_DIR = os.path.join(DATA_DIR, "rule_configs")
RULE_SUGGESTIONS_DIR = os.path.join(DATA_DIR, "rule_suggestions")
EXPORTS_DIR = os.path.join(PROJECT_ROOT, "exports")
GROUND_TRUTH_DIR = os.path.join(DATA_DIR, "ground_truth")
EVALUATIONS_DIR = os.path.join(DATA_DIR, "evaluations")
LINEAGE_DIR = os.path.join(DATA_DIR, "lineage")
# 阶段三: 参数优化相关目录
INDUSTRY_PARAMS_DIR = os.path.join(DATA_DIR, "industry_params")
AB_TESTS_DIR = os.path.join(DATA_DIR, "ab_tests")
CROSS_IMPACT_DIR = os.path.join(DATA_DIR, "cross_impact")
OPTIMIZATION_LOOP_DIR = os.path.join(DATA_DIR, "optimization_loop")

MEMORY_DIR = os.path.join(DATA_DIR, "memory")
MEMORY_CASES_DIR = os.path.join(MEMORY_DIR, "cases")
MEMORY_FALSE_POSITIVES_DIR = os.path.join(MEMORY_DIR, "false_positives")
MEMORY_FALSE_NEGATIVES_DIR = os.path.join(MEMORY_DIR, "false_negatives")
MEMORY_RULE_STATS_DIR = os.path.join(MEMORY_DIR, "rule_stats")

for _dir in [MEMORY_DIR, MEMORY_CASES_DIR, MEMORY_FALSE_POSITIVES_DIR,
             MEMORY_FALSE_NEGATIVES_DIR, MEMORY_RULE_STATS_DIR]:
    os.makedirs(_dir, exist_ok=True)
