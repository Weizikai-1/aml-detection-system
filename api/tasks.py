"""
Celery 异步任务

提供分析任务的异步执行能力。
符合业务戒律 P1: 不遗漏高风险交易（异步队列保证）。
"""
import os
import logging
from typing import Dict, Any, List

from celery import Celery

logger = logging.getLogger(__name__)

# 创建 Celery 应用
BROKER_URL = os.getenv("CELERY_BROKER_URL", "memory://")
BACKEND_URL = os.getenv("CELERY_BACKEND_URL", "memory://")

celery_app = Celery(
    "aml_agent",
    broker=BROKER_URL,
    backend=BACKEND_URL,
)

# 配置
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 任务重试配置
    task_retry_backoff=3,
    task_retry_backoff_max=60,
    task_max_retries=3,
    # 结果过期时间（24小时）
    result_expires=86400,
)


@celery_app.task(bind=True, name="submit_analysis_task")
def submit_analysis_task(self, transactions: List[Dict[str, Any]], execution_id: str, auto_evaluate: bool = False) -> Dict[str, Any]:
    """
    异步执行反洗钱分析任务

    Args:
        transactions: 交易数据列表
        execution_id: 执行ID
        auto_evaluate: 是否自动评估

    Returns:
        分析结果字典
    """
    logger.info(f"[任务] 开始分析: execution_id={execution_id}, transactions={len(transactions)}")
    
    try:
        from graph.workflow import AMLAgentsGraph
        
        workflow = AMLAgentsGraph(auto_evaluate=auto_evaluate)
        result = workflow.run(
            transactions=transactions,
            thread_id=execution_id,
        )
        
        logger.info(f"[任务] 分析完成: execution_id={execution_id}, reports={result.get('report_count', 0)}")
        
        return result
    
    except Exception as e:
        logger.error(f"[任务] 分析失败: execution_id={execution_id}, error={e}", exc_info=True)
        raise self.retry(exc=e, countdown=30)


@celery_app.task(name="export_report_task")
def export_report_task(report_id: str, export_format: str = "excel") -> str:
    """
    异步导出报告

    Args:
        report_id: 报告ID
        export_format: 导出格式（excel/pdf）

    Returns:
        文件路径
    """
    logger.info(f"[任务] 开始导出报告: report_id={report_id}, format={export_format}")
    
    try:
        if export_format == "pdf":
            from tools.pdf_exporter import PDFExporter
            exporter = PDFExporter()
            file_path = exporter.export_single(report_id)
        else:
            from tools.excel_exporter import ExcelExporter
            exporter = ExcelExporter()
            file_path = exporter.export_single(report_id)
        
        logger.info(f"[任务] 导出完成: report_id={report_id}, file={file_path}")
        
        return file_path
    
    except Exception as e:
        logger.error(f"[任务] 导出失败: report_id={report_id}, error={e}", exc_info=True)
        raise