"""
AML-Agent API 服务入口

基于 FastAPI 框架，提供生产级反洗钱分析 API。

设计原则:
- M1: 使用真实数据，不编造
- M4: API 调用记录完整可追溯（审计日志）
- P1: 分析任务不遗漏（异步任务队列）
- 错误隔离: 单个请求失败不影响其他请求

API 端点:
- GET /health                    — 健康检查
- POST /api/auth/login           — 用户登录
- POST /api/analysis/            — 提交分析任务（异步）
- POST /api/analysis/run         — 同步分析
- GET /api/analysis/tasks        — 查询任务列表
- GET /api/analysis/tasks/{id}   — 查询任务详情
- GET /api/reports               — 获取报告列表
- GET /api/reports/{id}          — 获取报告详情
- GET /api/reports/stats         — 获取报告统计
- POST /api/upload/file          — 上传交易数据文件
"""
import os
import logging
from datetime import datetime
from typing import Dict, Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

# API 限流
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    _limiter = Limiter(key_func=get_remote_address)
except ImportError:
    _limiter = None

# 配置日志（使用轮转避免磁盘耗尽）
from logging.handlers import RotatingFileHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            "logs/api.log",
            encoding="utf-8",
            maxBytes=100 * 1024 * 1024,  # 100MB
            backupCount=10,
        ),
    ],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 生命周期管理器（替代已废弃的 @app.on_event）
    
    启动时执行初始化，关闭时执行清理
    """
    logger.info("[API] AML-Agent API 服务启动中...")
    
    try:
        from api.log_desensitize import patch_logger
        patch_logger(None)
        logger.info("[API] 日志脱敏初始化完成（全模块覆盖）")
        
        from api.audit_log import audit_logger, OperationType
        audit_logger.log_success(
            operation_type=OperationType.SYSTEM,
            action="API服务启动",
            details={"version": "1.2.0"},
        )
        logger.info("[API] 审计日志初始化完成")
        
        from api.database import init_db, create_tables
        db_url = os.getenv("DATABASE_URL", "")
        if db_url:
            connected = init_db(db_url)
            if connected:
                create_tables()
                logger.info("[API] PostgreSQL 数据库连接成功")
            else:
                logger.info("[API] 使用 JSON 文件模式")
        else:
            logger.info("[API] 使用 JSON 文件模式")
        
        from api.dual_write import init_dual_write
        init_dual_write()
        logger.info("[API] 双写机制初始化完成")
        
        from api.monitor import init_monitor
        init_monitor()
        logger.info("[API] 监控指标初始化完成")
        
        logger.info("[API] AML-Agent API 服务启动完成")
    except Exception as e:
        logger.error(f"[API] 启动初始化失败: {e}", exc_info=True)
    
    yield
    
    logger.info("[API] AML-Agent API 服务关闭中...")


# 创建 FastAPI 应用
_app_env = os.getenv("APP_ENV", "development")
app = FastAPI(
    title="AML-Agent API",
    description="""
# 反洗钱多Agent分析系统 API

## 概述

AML-Agent 是一个基于多Agent架构的反洗钱分析系统，提供完整的交易监控、风险评估和告警管理功能。

## 主要功能

- **实时交易分析**: 实时监控和分析金融交易
- **多维度风险评估**: 基于规则引擎和机器学习模型的综合风险评估
- **智能告警管理**: 自动触发告警，支持分级处理
- **合规报告生成**: 自动生成符合监管要求的报告

## 业务戒律

- **M1**: 使用真实数据，不编造
- **M2**: 不遗漏高风险交易
- **M3**: 不误报正常交易
- **M4**: 标注可疑理由，可追溯
- **M5**: 审计日志完整记录

## 技术架构

- **API层**: FastAPI + Uvicorn
- **数据层**: PostgreSQL + JSON文件双写
- **任务队列**: Celery + Redis
- **监控**: Prometheus + Grafana

## 安全措施

- JWT Bearer 认证
- API 限流保护
- IP 白名单（/metrics）
- CORS 配置
- 日志脱敏处理
""",
    version="1.2.0",
    lifespan=lifespan,
    docs_url=None if _app_env == "production" else "/docs",
    redoc_url=None if _app_env == "production" else "/redoc",
    openapi_tags=[
        {"name": "系统", "description": "系统状态、健康检查等"},
        {"name": "认证", "description": "用户登录、令牌管理"},
        {"name": "分析", "description": "交易分析、风险评估"},
        {"name": "报告", "description": "分析报告管理"},
        {"name": "监控", "description": "系统监控指标"},
    ],
)

# CORS 配置
cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:8501,http://localhost:8000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

# 注册限流器
if _limiter is not None:
    app.state.limiter = _limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# /metrics IP 白名单（M8 修复：防止监控指标泄露）
_METRICS_ALLOWED_IPS = os.getenv("METRICS_ALLOWED_IPS", "127.0.0.1,::1,localhost")
_metrics_allowed_ips = {ip.strip() for ip in _METRICS_ALLOWED_IPS.split(",") if ip.strip()}

@app.middleware("http")
async def metrics_ip_whitelist(request: Request, call_next):
    """/metrics 端点 IP 白名单中间件"""
    if request.url.path == "/metrics":
        client_ip = request.client.host if request.client else ""
        if client_ip not in _metrics_allowed_ips:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Forbidden"},
            )
    return await call_next(request)

# 全局异常处理
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """处理请求参数校验错误"""
    logger.error(f"[API] 参数校验失败: {exc.errors()}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors(), "msg": "请求参数格式错误"},
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """处理全局异常"""
    logger.error(f"[API] 未处理异常: {exc}", exc_info=True)
    # 生产环境不泄露内部异常详情
    detail = str(exc) if _app_env != "production" else "内部错误"
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": detail, "msg": "服务器内部错误"},
    )

# 请求日志中间件
@app.middleware("http")
async def request_log_middleware(request: Request, call_next):
    """记录所有 API 请求日志和监控指标"""
    start_time = datetime.now()
    client_ip = request.client.host if request.client else "unknown"
    
    try:
        response = await call_next(request)
        duration = (datetime.now() - start_time).total_seconds()
        
        # 记录成功请求
        logger.info(
            f"[API] {request.method} {request.url.path} "
            f"status={response.status_code} duration={duration:.3f}s ip={client_ip}"
        )
        
        # 记录监控指标
        from api.monitor import record_request
        record_request(request.url.path, request.method, response.status_code, duration)
        
        return response
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        logger.error(
            f"[API] {request.method} {request.url.path} "
            f"error={str(e)} duration={duration:.3f}s ip={client_ip}"
        )
        
        # 记录失败请求监控指标
        from api.monitor import record_request
        record_request(request.url.path, request.method, 500, duration)
        
        raise

# ===== 健康检查端点 =====
@app.get("/health", tags=["系统"])
async def health_check() -> Dict[str, Any]:
    """
    健康检查端点
    
    返回系统状态、数据库连接状态等信息
    """
    from api.database import check_connection
    
    db_status = check_connection()
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
        "database": db_status,
    }

# ===== Prometheus 监控端点 =====
from prometheus_client import generate_latest

@app.get("/metrics", tags=["监控"])
async def metrics():
    """
    Prometheus 监控指标端点
    
    返回系统监控指标，供 Prometheus 抓取
    """
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(generate_latest(), media_type="text/plain")

# ===== API 路由注册 =====
from api.routes.auth import router as auth_router
from api.routes.analysis import router as analysis_router
from api.routes.reports import router as reports_router
from api.routes.upload import router as upload_router

app.include_router(auth_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")
app.include_router(reports_router, prefix="/api")
app.include_router(upload_router, prefix="/api")

# 静态文件服务
import os
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# 上传页面
@app.get("/upload", tags=["页面"])
async def upload_page():
    """数据上传页面"""
    upload_html = os.path.join(static_dir, "upload.html")
    if os.path.exists(upload_html):
        return FileResponse(upload_html)
    return {
        "message": "上传页面",
        "api": "/api/upload/file",
        "docs": "/api/upload/formats",
    }

# 根路径
@app.get("/", tags=["系统"])
async def root():
    """API 根路径"""
    return {
        "message": "AML-Agent API",
        "docs": "/docs",
        "health": "/health",
        "upload": "/upload",
    }