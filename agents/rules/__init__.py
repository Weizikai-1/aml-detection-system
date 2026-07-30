"""反洗钱检测规则模块 — 10条独立规则"""
from agents.rules.smurfing import detect as detect_smurfing
from agents.rules.fast_in_fast_out import detect as detect_fast_in_fast_out
from agents.rules.round_trip import detect as detect_round_trip
from agents.rules.large_amount import detect as detect_large_amount
from agents.rules.baseline_deviation import detect as detect_baseline_deviation
from agents.rules.remark_keywords import detect as detect_remark_keywords
from agents.rules.shell_company import detect as detect_shell_companies
from agents.rules.sanction_list import detect as detect_sanction_list
from agents.rules.cross_border import detect as detect_cross_border
from agents.rules.crypto_pattern import detect as detect_crypto_pattern

__all__ = [
    "detect_smurfing", "detect_fast_in_fast_out", "detect_round_trip",
    "detect_large_amount", "detect_baseline_deviation", "detect_remark_keywords",
    "detect_shell_companies", "detect_sanction_list", "detect_cross_border",
    "detect_crypto_pattern",
]
