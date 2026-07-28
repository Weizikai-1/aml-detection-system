"""
认证路由

提供用户登录、token验证等功能。
符合业务戒律 M4: 登录记录可追溯。
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["认证"])

# 密码哈希上下文
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# JWT 配置
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRATION_HOURS", 24))

# 启动时校验 JWT 密钥
if not SECRET_KEY:
    import secrets
    SECRET_KEY = secrets.token_hex(32)
    logger.warning("[安全] JWT_SECRET_KEY 未设置，已随机生成（重启后旧token将失效，生产环境必须设置）")

# OAuth2 Schema
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码（截断到 72 字节）"""
    return pwd_context.verify(plain_password[:72], hashed_password)


def get_password_hash(password: str) -> str:
    """生成密码哈希（截断到 72 字节）"""
    return pwd_context.hash(password[:72])


def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    """
    创建 JWT access token

    Args:
        data: 包含用户信息的字典
        expires_delta: 过期时间

    Returns:
        JWT token 字符串
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now() + expires_delta
    else:
        expire = datetime.now() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    """
    获取当前登录用户

    Args:
        token: JWT token

    Returns:
        用户信息字典

    Raises:
        HTTPException: 认证失败
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        
        # 从数据库或缓存获取用户信息
        user = get_user_by_username(username)
        if user is None:
            raise credentials_exception
        
        return user
    except JWTError:
        raise credentials_exception


def require_role(*roles: str):
    """
    角色权限检查依赖工厂

    用法:
        @router.get("/admin-only", dependencies=[Depends(require_role("admin"))])
        async def admin_only(): ...

    Args:
        roles: 允许的角色名称

    Returns:
        依赖函数
    """
    async def role_checker(current_user: Dict[str, Any] = Depends(get_current_user)):
        if current_user["role"] not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足，需要角色: {', '.join(roles)}",
            )
        return current_user
    return role_checker


def get_user_by_username(username: str) -> Dict[str, Any]:
    """
    根据用户名获取用户信息

    优先级:
    1. PostgreSQL（如果可用）
    2. 环境变量配置的开发用户（仅开发模式）

    Args:
        username: 用户名

    Returns:
        用户信息字典，不存在返回 None
    """
    from api.database import get_db_mode
    
    if get_db_mode() == "postgres":
        return get_user_from_postgres(username)
    
    # 开发模式用户（通过环境变量配置密码，不硬编码）
    app_env = os.getenv("APP_ENV", "development")
    if app_env == "production":
        logger.warning(f"[认证] 生产环境未配置数据库，拒绝用户登录: {username}")
        return None
    
    # 开发环境：从环境变量读取密码
    dev_users = {
        "admin": {
            "user_id": "admin-001",
            "username": "admin",
            "hashed_password": get_password_hash(os.getenv("DEV_ADMIN_PASSWORD", "")),
            "email": "admin@aml-agent.local",
            "role": "admin",
            "is_active": True,
        },
        "analyst": {
            "user_id": "analyst-001",
            "username": "analyst",
            "hashed_password": get_password_hash(os.getenv("DEV_ANALYST_PASSWORD", "")),
            "email": "analyst@aml-agent.local",
            "role": "analyst",
            "is_active": True,
        },
    }
    
    user = dev_users.get(username)
    if user and not user["hashed_password"]:
        logger.warning(f"[认证] 开发用户 {username} 未设置密码（DEV_ADMIN_PASSWORD/DEV_ANALYST_PASSWORD），拒绝登录")
        return None
    
    return user


def get_user_from_postgres(username: str) -> Dict[str, Any]:
    """从 PostgreSQL 获取用户信息"""
    from api.database import session_scope
    from api.models import User
    
    with session_scope() as session:
        if session is None:
            return None
        
        try:
            user = session.query(User).filter_by(username=username).first()
            if user and user.is_active:
                return {
                    "user_id": user.user_id,
                    "username": user.username,
                    "hashed_password": user.hashed_password,
                    "email": user.email,
                    "role": user.role,
                    "is_active": user.is_active,
                }
        except Exception as e:
            logger.error(f"[认证] 从 PostgreSQL 获取用户失败: {e}")
    
    return None


# 限流装饰器（slowapi未安装时为空操作）
def _rate_limit(limit_str: str):
    """登录限流装饰器"""
    def decorator(func):
        try:
            from api.main import _limiter
            if _limiter is not None:
                return _limiter.limit(limit_str)(func)
        except Exception:
            pass
        return func
    return decorator


@router.post("/login", response_model=Dict[str, Any])
@_rate_limit("5/minute")
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """
    用户登录

    Args:
        request: 请求对象
        form_data: OAuth2 登录表单（username, password）

    Returns:
        {"access_token": "...", "token_type": "bearer", "user": {...}}
    """
    from api.audit_log import audit_logger, OperationType
    
    client_ip = request.client.host if request.client else "unknown"
    
    user = get_user_by_username(form_data.username)
    if not user or not verify_password(form_data.password, user["hashed_password"]):
        logger.warning(f"[认证] 登录失败: username={form_data.username}")
        
        # 记录审计日志
        audit_logger.log_failed(
            operation_type=OperationType.AUTH,
            action="用户登录失败",
            error_message="用户名或密码错误",
            username=form_data.username,
            ip_address=client_ip,
            details={"username": form_data.username},
        )
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # 更新最后登录时间（PostgreSQL模式）
    update_last_login(user["user_id"])
    
    # 创建 access token
    access_token_expires = timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    access_token = create_access_token(
        data={"sub": user["username"], "role": user["role"], "user_id": user["user_id"]},
        expires_delta=access_token_expires,
    )
    
    logger.info(f"[认证] 登录成功: username={user['username']}, role={user['role']}")
    
    # 记录审计日志
    audit_logger.log_success(
        operation_type=OperationType.AUTH,
        action="用户登录成功",
        user_id=user["user_id"],
        username=user["username"],
        ip_address=client_ip,
        details={"role": user["role"], "email": user.get("email", "")},
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": access_token_expires.total_seconds(),
        "user": {
            "user_id": user["user_id"],
            "username": user["username"],
            "role": user["role"],
            "email": user.get("email", ""),
        },
    }


def update_last_login(user_id: str):
    """更新用户最后登录时间"""
    from api.database import get_db_mode
    
    if get_db_mode() != "postgres":
        return
    
    from api.database import session_scope
    from api.models import User
    
    with session_scope() as session:
        if session is None:
            return
        
        try:
            user = session.query(User).filter_by(user_id=user_id).first()
            if user:
                user.last_login = datetime.now()
                session.commit()
        except Exception as e:
            logger.error(f"[认证] 更新最后登录时间失败: {e}")


@router.get("/me", response_model=Dict[str, Any])
async def get_current_user_info(current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    获取当前用户信息

    需要认证: Bearer token

    Returns:
        当前用户信息
    """
    return {
        "user_id": current_user["user_id"],
        "username": current_user["username"],
        "role": current_user["role"],
        "email": current_user.get("email", ""),
        "is_active": current_user["is_active"],
    }