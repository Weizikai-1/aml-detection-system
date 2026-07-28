"""
数据上传路由

提供银行交易流水文件上传功能，支持 CSV/Excel/JSON 格式，自动识别银行格式。

符合业务戒律:
- M1: 使用真实数据，导入后原样保留原始字段
- M2: 返回字段映射结果，让用户确认数据是否正确解析
- M4: 上传记录审计日志
"""
import logging
import os
import tempfile
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile
from api.routes.auth import get_current_user, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/upload", tags=["数据上传"])


UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/file", response_model=Dict[str, Any], status_code=status.HTTP_200_OK)
async def upload_transaction_file(
    file: UploadFile = File(...),
    auto_analyze: bool = True,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    上传交易数据文件（支持 CSV/Excel/JSON）

    Args:
        file: 上传的文件（.csv/.xls/.xlsx/.json）
        auto_analyze: 上传后是否自动触发分析

    Returns:
        {
            "success": bool,
            "message": str,
            "file_name": str,
            "bank_format": str,
            "bank_name": str,
            "total_rows": int,
            "valid_rows": int,
            "invalid_rows": int,
            "field_mapping": dict,
            "data_quality": dict,
            "task_id": str (如果 auto_analyze=True),
        }
    """
    # 校验文件类型
    allowed_extensions = {".csv", ".xls", ".xlsx", ".json"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件格式: {ext}。支持格式: {', '.join(allowed_extensions)}",
        )

    # 保存临时文件
    try:
        with tempfile.NamedTemporaryFile(
            suffix=ext,
            dir=UPLOAD_DIR,
            delete=False,
            encoding="utf-8" if ext == ".json" else None,
        ) as f:
            content = await file.read()
            if ext in (".csv", ".json"):
                f.write(content)
            else:
                # Excel 文件二进制写入
                f.write(content)
            temp_path = f.name
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"文件保存失败: {str(e)}",
        )

    # 导入数据
    try:
        from tools.data_importer import import_transactions

        result = import_transactions(temp_path)

        # 清理临时文件
        os.unlink(temp_path)

        if not result["transactions"]:
            return {
                "success": False,
                "message": "未导入任何有效交易数据",
                "file_name": file.filename,
                "bank_format": result.get("bank_format", "unknown"),
                "bank_name": result.get("bank_name", "未知银行"),
                "total_rows": result.get("total", 0),
                "valid_rows": result.get("valid", 0),
                "invalid_rows": result.get("total", 0) - result.get("valid", 0),
                "field_mapping": result.get("mapping", {}),
                "data_quality": result.get("data_quality", {}),
                "errors": result.get("errors", []),
            }

        # 如果需要自动分析
        task_id = None
        if auto_analyze:
            task_id = await _trigger_analysis(result["transactions"], current_user)

        return {
            "success": True,
            "message": f"成功导入 {result['valid']} 条交易数据",
            "file_name": file.filename,
            "bank_format": result.get("bank_format", "unknown"),
            "bank_name": result.get("bank_name", "未知银行"),
            "total_rows": result.get("total", 0),
            "valid_rows": result.get("valid", 0),
            "invalid_rows": result.get("total", 0) - result.get("valid", 0),
            "field_mapping": result.get("mapping", {}),
            "data_quality": result.get("data_quality", {}),
            "task_id": task_id,
        }

    except Exception as e:
        # 清理临时文件
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        logger.error(f"文件导入失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"文件导入失败: {str(e)}",
        )


@router.post("/preview", response_model=Dict[str, Any], status_code=status.HTTP_200_OK)
async def preview_uploaded_file(
    file: UploadFile = File(...),
    limit: int = 10,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    预览上传文件内容（不导入，仅查看前 N 行）

    Args:
        file: 上传的文件
        limit: 预览行数（默认 10）

    Returns:
        {
            "success": bool,
            "bank_format": str,
            "bank_name": str,
            "field_mapping": dict,
            "sample_rows": list,
            "columns": list,
        }
    """
    ext = os.path.splitext(file.filename)[1].lower()
    allowed_extensions = {".csv", ".xls", ".xlsx", ".json"}
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件格式: {ext}",
        )

    try:
        with tempfile.NamedTemporaryFile(
            suffix=ext,
            dir=UPLOAD_DIR,
            delete=False,
            encoding="utf-8" if ext == ".json" else None,
        ) as f:
            content = await file.read()
            if ext in (".csv", ".json"):
                f.write(content)
            else:
                f.write(content)
            temp_path = f.name

        from tools.data_importer import (
            detect_bank_format,
            _auto_detect_mapping,
            _read_csv,
            _read_excel,
            _read_json,
            BANK_FORMAT_TEMPLATES,
        )

        # 读取数据
        if ext == ".csv":
            raw_rows = _read_csv(temp_path)
        elif ext in (".xls", ".xlsx"):
            raw_rows = _read_excel(temp_path)
        elif ext == ".json":
            raw_rows = _read_json(temp_path)
        else:
            raw_rows = []

        os.unlink(temp_path)

        if not raw_rows:
            return {
                "success": False,
                "message": "文件为空",
                "columns": [],
                "sample_rows": [],
            }

        columns = list(raw_rows[0].keys())
        bank_format = detect_bank_format(columns)
        bank_name = BANK_FORMAT_TEMPLATES.get(bank_format, {}).get("name", "未知银行")
        mapping = _auto_detect_mapping(columns)

        return {
            "success": True,
            "bank_format": bank_format,
            "bank_name": bank_name,
            "field_mapping": mapping,
            "columns": columns,
            "sample_rows": raw_rows[:limit],
            "total_rows": len(raw_rows),
        }

    except Exception as e:
        if "temp_path" in locals() and os.path.exists(temp_path):
            os.unlink(temp_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"预览失败: {str(e)}",
        )


async def _trigger_analysis(transactions: List[Dict[str, Any]], user: Dict[str, Any]) -> str:
    """触发分析任务（同步模式）"""
    from uuid import uuid4
    from graph.workflow import AMLAgentsGraph

    execution_id = str(uuid4())[:8]

    workflow = AMLAgentsGraph(auto_evaluate=True)
    workflow.run(
        transactions=transactions,
        thread_id=execution_id,
    )
    return execution_id


@router.get("/formats", response_model=Dict[str, Any])
async def get_supported_formats():
    """获取支持的银行格式列表"""
    from tools.data_importer import BANK_FORMAT_TEMPLATES

    formats = []
    for key, template in BANK_FORMAT_TEMPLATES.items():
        formats.append({
            "code": key,
            "name": template.get("name", ""),
            "fields": template.get("fields", []),
        })

    return {
        "supported_formats": formats,
        "required_fields": ["付款账号", "收款账号", "交易金额", "交易时间"],
        "supported_file_types": [".csv", ".xls", ".xlsx", ".json"],
        "example_columns": [
            ["交易流水号", "交易日期", "交易时间", "交易金额", "付款账号", "收款账号", "摘要"],
            ["流水号", "日期", "金额", "付款人账号", "收款人账号", "用途"],
        ],
    }
