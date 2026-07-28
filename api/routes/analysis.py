"""
分析路由

提供反洗钱分析任务的提交、查询等功能。
符合业务戒律 P1: 不遗漏高风险交易（异步任务队列）。
符合业务戒律 M4: 所有操作需认证，审计可追溯。
"""
import logging
from datetime import datetime
from typing import Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException, status
from api.routes.auth import get_current_user, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis", tags=["分析"])

# 任务状态
TASK_STATUS = {
    "pending": "等待处理",
    "running": "分析中",
    "completed": "已完成",
    "failed": "失败",
}

# 内存任务存储（生产环境使用 Redis）
_tasks = {}


@router.post("/", response_model=Dict[str, Any], status_code=status.HTTP_202_ACCEPTED)
async def submit_analysis(
    transactions: List[Dict[str, Any]],
    auto_evaluate: bool = False,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    提交分析任务（异步）

    Args:
        transactions: 交易数据列表
        auto_evaluate: 是否自动评估

    Returns:
        {"task_id": "...", "status": "pending", "message": "..."}
    """
    if not transactions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="交易数据不能为空",
        )
    
    from uuid import uuid4
    from api.tasks import submit_analysis_task
    
    execution_id = str(uuid4())[:8]
    
    # 提交异步任务
    task = submit_analysis_task.delay(
        transactions=transactions,
        execution_id=execution_id,
        auto_evaluate=auto_evaluate,
    )
    
    _tasks[execution_id] = {
        "task_id": task.id,
        "execution_id": execution_id,
        "status": "pending",
        "progress": 0,
        "message": "任务已提交，等待处理",
        "submitted_at": datetime.now().isoformat(),
    }
    
    logger.info(f"[分析] 任务提交: execution_id={execution_id}, transactions={len(transactions)}")
    
    return {
        "task_id": execution_id,
        "status": "pending",
        "message": "分析任务已提交",
        "transactions_count": len(transactions),
        "submitted_at": _tasks[execution_id]["submitted_at"],
    }


@router.get("/tasks", response_model=List[Dict[str, Any]])
async def list_tasks(
    limit: int = 20,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    获取任务列表

    Args:
        limit: 返回数量限制

    Returns:
        任务列表
    """
    from api.dual_write import get_history_adapter
    
    adapter = get_history_adapter()
    runs = adapter.list_runs(limit=limit)
    
    results = []
    for run in runs:
        execution_id = run.get("execution_id", "")
        task_info = _tasks.get(execution_id, {})
        
        results.append({
            "task_id": execution_id,
            "status": task_info.get("status", "completed"),
            "transactions_count": run.get("transactions_count", 0),
            "rule_hit_count": run.get("rule_hit_count", 0),
            "report_count": run.get("report_count", 0),
            "duration_seconds": run.get("duration_seconds", 0),
            "timestamp": run.get("timestamp", ""),
            "risk_distribution": run.get("risk_distribution", {}),
        })
    
    return results


@router.get("/tasks/{task_id}", response_model=Dict[str, Any])
async def get_task(
    task_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    获取任务详情

    Args:
        task_id: 任务ID（execution_id）

    Returns:
        任务详情
    """
    from api.dual_write import get_history_adapter
    
    adapter = get_history_adapter()
    run = adapter.get_run(task_id)
    
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"任务不存在: {task_id}",
        )
    
    task_info = _tasks.get(task_id, {})
    
    return {
        "task_id": task_id,
        "status": task_info.get("status", "completed"),
        "progress": task_info.get("progress", 100),
        "message": task_info.get("message", "任务已完成"),
        "transactions_count": run.get("transactions_count", 0),
        "transactions_hash": run.get("transactions_hash", ""),
        "rule_hit_count": run.get("rule_hit_count", 0),
        "report_count": run.get("report_count", 0),
        "duration_seconds": run.get("duration_seconds", 0),
        "timestamp": run.get("timestamp", ""),
        "risk_distribution": run.get("risk_distribution", {}),
        "value_metrics": run.get("value_metrics", {}),
        "primary_accounts": run.get("primary_accounts", []),
        "rule_details": run.get("rule_details", {}),
        "interrupted": run.get("interrupted", False),
        "error": run.get("error", ""),
    }


@router.post("/run", response_model=Dict[str, Any])
async def run_analysis_sync(
    transactions: List[Dict[str, Any]],
    auto_evaluate: bool = False,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    同步执行分析（阻塞调用）

    Args:
        transactions: 交易数据列表
        auto_evaluate: 是否自动评估

    Returns:
        分析结果
    """
    if not transactions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="交易数据不能为空",
        )
    
    logger.info(f"[分析] 同步分析开始: transactions={len(transactions)}")
    
    try:
        from graph.workflow import AMLAgentsGraph
        
        workflow = AMLAgentsGraph(auto_evaluate=auto_evaluate)
        result = workflow.run(
            transactions=transactions,
        )
        
        execution_id = result.get("execution_id", "")
        
        _tasks[execution_id] = {
            "task_id": execution_id,
            "execution_id": execution_id,
            "status": "completed",
            "progress": 100,
            "message": "分析完成",
        }
        
        logger.info(f"[分析] 同步分析完成: execution_id={execution_id}, reports={result.get('report_count', 0)}")
        
        return {
            "task_id": execution_id,
            "status": "completed",
            "message": "分析完成",
            "transactions_count": result.get("transactions_count", 0),
            "rule_hit_count": result.get("rule_hit_count", 0),
            "report_count": result.get("report_count", 0),
            "risk_distribution": result.get("risk_distribution", {}),
            "value_metrics": result.get("value_metrics", {}),
            "compliance_score": result.get("compliance_score", 0),
            "total_processing_time": result.get("total_processing_time", 0),
        }
    
    except Exception as e:
        logger.error(f"[分析] 同步分析失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"分析失败: {str(e)}",
        )