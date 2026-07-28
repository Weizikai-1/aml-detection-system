"""
数据库连接管理

支持双后端模式:
1. JSON 文件模式（默认，本地开发/测试）
2. PostgreSQL 模式（生产环境，通过 DATABASE_URL 环境变量启用）

设计原则:
- M1: 不编造数据，所有数据库操作基于真实数据
- M4: 连接状态可追溯，异常可记录
- 向后兼容: 无 PostgreSQL 时自动降级为 JSON 模式
"""
import os
import time
import logging
from typing import Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


# 全局引擎和会话工厂
_engine = None
_SessionFactory = None
_db_mode = "json"  # 默认JSON模式


def get_db_mode() -> str:
    """获取当前数据库模式"""
    return _db_mode


def is_postgres_available() -> bool:
    """检查 PostgreSQL 是否可用"""
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        return False
    if "postgresql" not in database_url:
        return False
    try:
        import sqlalchemy  # noqa: F401
        import psycopg2  # noqa: F401
        return True
    except ImportError:
        return False


def init_db(database_url: str = None, echo: bool = False):
    """
    初始化数据库连接

    Args:
        database_url: PostgreSQL 连接字符串，None时使用环境变量
        echo: 是否输出SQL语句（调试用）

    Returns:
        True=PostgreSQL模式, False=JSON模式
    """
    global _engine, _SessionFactory, _db_mode

    if database_url is None:
        database_url = os.getenv("DATABASE_URL", "")

    # 无 PostgreSQL 连接字符串 → 保持 JSON 模式
    if not database_url or "postgresql" not in database_url:
        _db_mode = "json"
        logger.info("[数据库] 使用 JSON 文件模式")
        return False

    # 尝试连接 PostgreSQL
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        _engine = create_engine(
            database_url,
            echo=echo,
            # 连接池配置
            pool_size=5,          # 常驻连接数
            max_overflow=10,      # 最大溢出连接数
            pool_timeout=30,      # 获取连接超时（秒）
            pool_recycle=3600,    # 连接回收时间（秒）
            pool_pre_ping=True,   # 连接前检查可用性
            # 事务隔离级别
            isolation_level="READ_COMMITTED",
        )

        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)

        # 测试连接
        from sqlalchemy import text
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        _db_mode = "postgres"
        logger.info("[数据库] PostgreSQL 连接成功")
        return True

    except Exception as e:
        _db_mode = "json"
        _engine = None
        _SessionFactory = None
        logger.warning(f"[数据库] PostgreSQL 连接失败，降级为 JSON 模式: {e}")
        return False


def get_engine():
    """获取 SQLAlchemy 引擎"""
    return _engine


def get_session():
    """获取数据库会话"""
    if _SessionFactory is None:
        return None
    return _SessionFactory()


@contextmanager
def session_scope():
    """
    数据库会话上下文管理器

    用法:
        with session_scope() as session:
            session.add(obj)
            # 自动 commit / rollback
    """
    session = get_session()
    if session is None:
        yield None
        return
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"[数据库] 事务回滚: {e}")
        raise
    finally:
        session.close()


def create_tables():
    """创建所有表（仅 PostgreSQL 模式）"""
    if _db_mode != "postgres" or _engine is None:
        return False

    try:
        from api.models import Base
        Base.metadata.create_all(_engine)
        logger.info("[数据库] 表结构创建/验证完成")
        return True
    except Exception as e:
        logger.error(f"[数据库] 表创建失败: {e}")
        return False


def check_connection() -> dict:
    """
    检查数据库连接状态

    Returns:
        {"mode": "postgres"/"json", "connected": bool, "info": str}
    """
    if _db_mode != "postgres" or _engine is None:
        return {
            "mode": "json",
            "connected": True,
            "info": "JSON 文件模式，无需连接",
        }

    try:
        from sqlalchemy import text
        with _engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.fetchone()[0]
        return {
            "mode": "postgres",
            "connected": True,
            "info": f"PostgreSQL {version}",
        }
    except Exception as e:
        return {
            "mode": "postgres",
            "connected": False,
            "info": f"连接失败: {e}",
        }