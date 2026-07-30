"""
共享工具模块 — 反洗钱系统公共基础设施

消除 agents/ 和 tools/ 中重复代码:
- 时间戳解析 (3处重复 → 1处)
- 风险评分计算 (散布代码 → 统一函数)
- 结构化日志 (替换156个 print())
- 配置常量 (消除11+处硬编码)
"""
import logging
import sys
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

# ============================================================
# 结构化日志 — 统一替换所有 print()
# ============================================================
_logger: Optional[logging.Logger] = None


def get_logger(name: str = "aml") -> logging.Logger:
    """获取结构化日志实例，有层级控制、时间戳、行号"""
    global _logger
    if _logger is not None:
        return _logger.getChild(name) if name != "aml" else _logger

    _logger = logging.getLogger("aml")
    _logger.setLevel(logging.INFO)

    if not _logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-5s] %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        ))
        _logger.addHandler(handler)

    return _logger.getChild(name) if name != "aml" else _logger


# ============================================================
# 时间戳解析 — 消除3处重复
# ============================================================
def parse_timestamp(value: Any) -> Optional[datetime]:
    """
    统一的时间戳解析函数。
    支持: ISO格式字符串、datetime对象、None
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def timestamp_to_iso(ts: Optional[datetime]) -> str:
    """datetime → ISO格式字符串，None → "" """
    return ts.isoformat() if ts else ""


# ============================================================
# 风险评分 — 消除11+处硬编码50
# ============================================================
DEFAULT_RISK_SCORE = 50
RISK_SCORE_MAX = 100
RISK_SCORE_MIN = 0


def clamp_score(score: float) -> int:
    """钳制风险评分到 [0, 100]"""
    return max(RISK_SCORE_MIN, min(RISK_SCORE_MAX, round(score)))


def merge_scores(scores: List[float], weights: Optional[List[float]] = None) -> float:
    """加权合并多个风险评分"""
    if not scores:
        return float(DEFAULT_RISK_SCORE)
    if weights is None:
        weights = [1.0] * len(scores)
    total = sum(s * w for s, w in zip(scores, weights))
    total_w = sum(weights)
    return total / total_w if total_w > 0 else float(DEFAULT_RISK_SCORE)


# ============================================================
# 评估指标 — 消除 evaluate.py 和 trainer 中重复
# ============================================================
def calc_binary_metrics(pred: 'np.ndarray', true: 'np.ndarray') -> Dict[str, float]:
    """计算二分类指标: Precision/Recall/F1/混淆矩阵"""
    import numpy as np
    tp = int(((pred == 1) & (true == 1)).sum())
    fp = int(((pred == 1) & (true == 0)).sum())
    tn = int(((pred == 0) & (true == 0)).sum())
    fn = int(((pred == 0) & (true == 1)).sum())
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return {"precision": p, "recall": r, "f1": f, "tp": tp, "fp": fp, "tn": tn, "fn": fn}


# ============================================================
# 交易/账户辅助
# ============================================================
def is_self_transfer(txn: dict) -> bool:
    """判断是否自转账 (from == to)"""
    fa = txn.get("from_account")
    ta = txn.get("to_account")
    return bool(fa and ta and fa == ta)


def safe_amount(txn: dict) -> float:
    """安全获取交易金额，缺失/None -> 0"""
    amount = txn.get("amount")
    if not isinstance(amount, (int, float)):
        return 0.0
    return float(amount)


def safe_get(obj: dict, key: str, default: Any = None) -> Any:
    """安全获取字典值，不抛KeyError"""
    return obj.get(key, default)
