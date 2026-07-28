"""
统一日志工具

提供结构化日志输出，便于后续监控和调试。
- 所有 Agent 统一日志格式
- 支持分级（INFO/WARN/ERROR）
- 支持耗时统计
- 保持控制台友好输出
- 戒律 M4: 同时写入文件持久化，便于追溯
"""
import time
import json
import logging
import os
from typing import Any, Dict, Optional


_LOG_BUFFER: list = []
_enabled = True

# 戒律 M4: 文件日志持久化（模块级单例，避免重复创建 Handler）
_file_logger: Optional[logging.Logger] = None
_file_logger_initialized = False


def _get_file_logger() -> Optional[logging.Logger]:
    """获取（惰性初始化）文件日志器"""
    global _file_logger, _file_logger_initialized
    if _file_logger_initialized:
        return _file_logger
    _file_logger_initialized = True
    try:
        from config import LOGS_DIR
        os.makedirs(LOGS_DIR, exist_ok=True)
        log_path = os.path.join(LOGS_DIR, "aml_agent.log")
        logger = logging.getLogger("aml_agent")
        logger.setLevel(logging.DEBUG)
        # 避免重复添加 Handler（多次调用时）
        if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        logger.propagate = False
        _file_logger = logger
    except Exception:
        # 配置失败时降级为仅控制台输出（不抛异常影响主流程）
        _file_logger = None
    return _file_logger


def enable_logging(enabled: bool = True):
    """启用/禁用日志输出"""
    global _enabled
    _enabled = enabled


def get_log_buffer() -> list:
    """获取日志缓冲区（用于测试断言）"""
    return _LOG_BUFFER


def clear_log_buffer():
    """清空日志缓冲区"""
    _LOG_BUFFER.clear()


def _log(level: str, agent: str, message: str, data: Optional[Dict[str, Any]] = None):
    """输出结构化日志"""
    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "agent": agent,
        "message": message,
        "data": data or {},
    }
    _LOG_BUFFER.append(entry)

    # 戒律 M4: 同步写入文件持久化
    fl = _get_file_logger()
    if fl is not None:
        try:
            log_line = f"[{agent}] {message}"
            if data:
                log_line += " | " + json.dumps(data, ensure_ascii=False, default=str)
            level_map = {
                "INFO": logging.INFO,
                "WARN": logging.WARNING,
                "ERROR": logging.ERROR,
            }
            fl.log(level_map.get(level, logging.INFO), log_line)
        except Exception:
            pass

    if not _enabled:
        return
    prefix = f"[{agent}]"
    if level == "WARN":
        prefix = f"[{agent}] ⚠️"
    elif level == "ERROR":
        prefix = f"[{agent}] ❌"
    print(f"{prefix} {message}")
    if data:
        for k, v in data.items():
            if isinstance(v, float):
                print(f"    - {k}: {v:,.2f}" if v > 100 else f"    - {k}: {v:.4f}")
            else:
                print(f"    - {k}: {v}")


def info(agent: str, message: str, **kwargs):
    """INFO 级别日志"""
    _log("INFO", agent, message, kwargs)


def warn(agent: str, message: str, **kwargs):
    """WARN 级别日志"""
    _log("WARN", agent, message, kwargs)


def error(agent: str, message: str, **kwargs):
    """ERROR 级别日志"""
    _log("ERROR", agent, message, kwargs)


def section(agent: str, title: str):
    """输出分节标题"""
    if not _enabled:
        return
    print()
    print("=" * 60)
    print(f"[{agent}] {title}")
    print("=" * 60)


class Timer:
    """
    计时器上下文管理器，用于统计耗时

    用法:
        with Timer("rule_engine") as t:
            do_work()
        print(f"耗时: {t.elapsed:.2f}秒")
    """

    def __init__(self, agent: str, label: str = ""):
        self.agent = agent
        self.label = label
        self.start_time = 0.0
        self.elapsed = 0.0

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, *args):
        self.elapsed = time.time() - self.start_time
        if self.label:
            info(self.agent, f"{self.label} 耗时: {self.elapsed:.2f} 秒")
        return False
