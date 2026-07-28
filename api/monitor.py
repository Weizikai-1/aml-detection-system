"""
API 监控指标

基于 Prometheus 客户端，提供系统监控指标。
"""
import logging
from prometheus_client import Counter, Histogram, Gauge

logger = logging.getLogger(__name__)

# 请求计数
REQUEST_COUNTER = Counter(
    "api_requests_total",
    "Total API requests",
    ["endpoint", "method", "status"],
)

# 请求耗时
REQUEST_LATENCY = Histogram(
    "api_request_duration_seconds",
    "API request duration",
    ["endpoint", "method"],
)

# 分析任务计数
ANALYSIS_COUNTER = Counter(
    "analysis_tasks_total",
    "Total analysis tasks",
    ["status"],
)

# 活跃任务数
ACTIVE_TASKS = Gauge(
    "active_tasks",
    "Number of active tasks",
)

# 规则命中数
RULE_HITS = Counter(
    "rule_hits_total",
    "Total rule hits",
    ["rule_name"],
)

# 报告生成数
REPORTS_GENERATED = Counter(
    "reports_generated_total",
    "Total reports generated",
    ["risk_level"],
)


def init_monitor():
    """初始化监控指标"""
    logger.info("[监控] 监控指标初始化完成")


def record_request(endpoint: str, method: str, status: int, duration: float):
    """
    记录 API 请求
    
    Args:
        endpoint: 端点路径
        method: HTTP方法
        status: 状态码
        duration: 耗时（秒）
    """
    REQUEST_COUNTER.labels(endpoint=endpoint, method=method, status=status).inc()
    REQUEST_LATENCY.labels(endpoint=endpoint, method=method).observe(duration)


def record_analysis_task(status: str):
    """
    记录分析任务状态
    
    Args:
        status: 任务状态（pending/running/completed/failed）
    """
    ANALYSIS_COUNTER.labels(status=status).inc()


def set_active_tasks(count: int):
    """
    设置活跃任务数
    
    Args:
        count: 任务数量
    """
    ACTIVE_TASKS.set(count)


def record_rule_hit(rule_name: str):
    """
    记录规则命中
    
    Args:
        rule_name: 规则名称
    """
    RULE_HITS.labels(rule_name=rule_name).inc()


def record_report_generated(risk_level: str):
    """
    记录报告生成
    
    Args:
        risk_level: 风险等级
    """
    REPORTS_GENERATED.labels(risk_level=risk_level).inc()