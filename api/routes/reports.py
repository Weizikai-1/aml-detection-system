"""
报告路由

提供可疑交易报告的查询、导出等功能。
符合业务戒律 M4: 报告完整可追溯，所有操作需认证。
"""
import os
import re
import logging
from typing import Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from api.routes.auth import get_current_user, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["报告"])

# report_id 格式校验（防止路径遍历：只允许字母、数字、下划线、短横线）
_REPORT_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')


def _validate_report_id(report_id: str) -> str:
    """校验 report_id 格式，防止路径遍历攻击"""
    if not report_id or not _REPORT_ID_PATTERN.match(report_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的报告ID格式",
        )
    return report_id


def _get_reports_from_history(limit: int = 20) -> List[Dict[str, Any]]:
    """从历史记录中提取所有报告"""
    from tools.history_manager import HistoryManager
    manager = HistoryManager()
    runs = manager.list_runs(limit=limit)

    reports = []
    for run in runs:
        execution_id = run.get("execution_id", "")
        report_count = run.get("report_count", 0)
        if report_count > 0:
            reports.append({
                "report_id": execution_id,
                "execution_id": execution_id,
                "timestamp": run.get("timestamp", ""),
                "report_count": report_count,
                "risk_distribution": run.get("risk_distribution", {}),
                "primary_accounts": run.get("primary_accounts", []),
                "transactions_count": run.get("transactions_count", 0),
                "rule_hit_count": run.get("rule_hit_count", 0),
            })

    return reports


def _get_report_detail(report_id: str) -> Dict[str, Any]:
    """从历史记录中获取报告详情"""
    from tools.history_manager import HistoryManager
    manager = HistoryManager()
    run = manager.get_run(report_id)

    if not run:
        return None

    str_reports = run.get("str_reports", [])
    return {
        "report_id": report_id,
        "execution_id": report_id,
        "timestamp": run.get("timestamp", ""),
        "transactions_count": run.get("transactions_count", 0),
        "rule_hit_count": run.get("rule_hit_count", 0),
        "report_count": run.get("report_count", 0),
        "risk_distribution": run.get("risk_distribution", {}),
        "primary_accounts": run.get("primary_accounts", []),
        "str_reports": str_reports,
        "duration_seconds": run.get("duration_seconds", 0),
        "interrupted": run.get("interrupted", False),
    }


@router.get("/", response_model=List[Dict[str, Any]])
async def list_reports(
    limit: int = 20,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    获取报告列表

    Args:
        limit: 返回数量限制
        current_user: 当前登录用户

    Returns:
        报告列表
    """
    return _get_reports_from_history(limit=limit)


@router.get("/stats", response_model=Dict[str, Any])
async def get_reports_stats(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    获取报告统计信息

    Args:
        current_user: 当前登录用户

    Returns:
        统计信息
    """
    from tools.history_manager import HistoryManager

    manager = HistoryManager()
    stats = manager.stats()

    return {
        "total_runs": stats.get("total_runs", 0),
        "total_reports": stats.get("total_reports", 0),
        "total_transactions": stats.get("total_transactions", 0),
        "avg_duration": stats.get("avg_duration", 0),
        "first_run": stats.get("first_run", ""),
        "last_run": stats.get("last_run", ""),
    }


@router.get("/{report_id}", response_model=Dict[str, Any])
async def get_report(
    report_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    获取报告详情

    Args:
        report_id: 报告ID（execution_id）
        current_user: 当前登录用户

    Returns:
        报告详情
    """
    _validate_report_id(report_id)
    report = _get_report_detail(report_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"报告不存在: {report_id}",
        )
    return report


@router.get("/{report_id}/export/excel", response_class=FileResponse)
async def export_report_excel(
    report_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    导出报告为 Excel

    Args:
        report_id: 报告ID（execution_id）
        current_user: 当前登录用户

    Returns:
        Excel 文件下载
    """
    _validate_report_id(report_id)
    from tools.excel_exporter import ExcelExporter
    from config import EXPORTS_DIR

    report_detail = _get_report_detail(report_id)
    if not report_detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"报告不存在: {report_id}",
        )

    str_reports = report_detail.get("str_reports", [])
    if not str_reports:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"报告数据为空: {report_id}",
        )

    os.makedirs(EXPORTS_DIR, exist_ok=True)
    output_path = os.path.join(EXPORTS_DIR, f"report_{report_id}.xlsx")

    exporter = ExcelExporter()
    file_path = exporter.export_reports(str_reports, output_path)

    if not file_path or not os.path.exists(file_path):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"报告导出失败: {report_id}",
        )

    return FileResponse(
        file_path,
        filename=f"report_{report_id}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/{report_id}/export/pdf", response_class=FileResponse)
async def export_report_pdf(
    report_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    导出报告为 PDF

    Args:
        report_id: 报告ID（execution_id）
        current_user: 当前登录用户

    Returns:
        PDF 文件下载
    """
    _validate_report_id(report_id)
    from tools.pdf_exporter import PDFExporter
    from config import EXPORTS_DIR

    report_detail = _get_report_detail(report_id)
    if not report_detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"报告不存在: {report_id}",
        )

    str_reports = report_detail.get("str_reports", [])
    if not str_reports:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"报告数据为空: {report_id}",
        )

    os.makedirs(EXPORTS_DIR, exist_ok=True)
    output_path = os.path.join(EXPORTS_DIR, f"report_{report_id}.pdf")

    exporter = PDFExporter()
    file_path = exporter.export_reports(str_reports, output_path)

    if not file_path or not os.path.exists(file_path):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"报告导出失败: {report_id}",
        )

    return FileResponse(
        file_path,
        filename=f"report_{report_id}.pdf",
        media_type="application/pdf",
    )


@router.get("/export/batch", response_class=FileResponse)
async def export_batch_reports(
    report_ids: str = None,
    current_user: Dict[str, Any] = Depends(require_role("admin")),
):
    """
    批量导出报告（仅管理员）

    Args:
        report_ids: 报告ID列表（逗号分隔），为空则导出所有报告
        current_user: 当前登录用户（必须为admin角色）

    Returns:
        ZIP 文件下载
    """
    from tools.batch_exporter import BatchExporter
    from tools.history_manager import HistoryManager
    from config import EXPORTS_DIR

    manager = HistoryManager()

    # 收集所有报告
    all_reports = []
    if report_ids:
        ids = [id.strip() for id in report_ids.split(",") if id.strip()]
        for rid in ids:
            detail = _get_report_detail(rid)
            if detail:
                all_reports.extend(detail.get("str_reports", []))
    else:
        runs = manager.list_runs(limit=100)
        for run in runs:
            detail = _get_report_detail(run.get("execution_id", ""))
            if detail:
                all_reports.extend(detail.get("str_reports", []))

    if not all_reports:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="没有可导出的报告",
        )

    os.makedirs(EXPORTS_DIR, exist_ok=True)

    exporter = BatchExporter()
    result = exporter.export(all_reports, create_zip=True, create_summary=True)

    zip_path = result.get("zip_path", "")
    if not zip_path or not os.path.exists(zip_path):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="批量导出失败",
        )

    return FileResponse(
        zip_path,
        filename="reports_batch.zip",
        media_type="application/zip",
    )
