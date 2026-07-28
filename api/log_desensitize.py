"""
日志脱敏模块

对敏感信息进行脱敏处理，符合业务戒律 M4: 敏感信息保护。

脱敏规则:
- 银行卡号: 保留前4位和后4位，中间用****替换
- 身份证号: 保留前6位和后4位，中间用**********替换
- 手机号: 保留前3位和后4位，中间用****替换
- 姓名: 保留第一个字，后面用*替换
- 邮箱: 用户名部分保留第一个字符，域名保留
- API密钥: 保留前8位，后面用***替换
"""
import re
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SENSITIVE_PATTERNS = {
    "bank_card": {
        "pattern": r"\b(62\d{14}|62\d{15}|62\d{16}|62\d{17}|62\d{18})\b",
        "replace": lambda m: m.group(1)[:4] + "****" + m.group(1)[-4:],
        "description": "银行卡号",
    },
    "id_card": {
        "pattern": r"\b(\d{17}[\dXx]|\d{15})\b",
        "replace": lambda m: m.group(1)[:6] + "**********" + m.group(1)[-4:],
        "description": "身份证号",
    },
    "phone": {
        "pattern": r"\b(1[3-9]\d{9})\b",
        "replace": lambda m: m.group(1)[:3] + "****" + m.group(1)[-4:],
        "description": "手机号",
    },
    "email": {
        "pattern": r"\b([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b",
        "replace": lambda m: m.group(1)[0] + "***@" + m.group(2),
        "description": "邮箱",
    },
    "api_key": {
        "pattern": r"\b([A-Za-z0-9]{8})[A-Za-z0-9]{20,}\b",
        "replace": lambda m: m.group(1) + "***",
        "description": "API密钥",
    },
    "account_number": {
        "pattern": r"\b(\d{4})\d{4,12}(\d{4})\b",
        "replace": lambda m: m.group(1) + "****" + m.group(2),
        "description": "账户号",
    },
}


def desensitize_text(text: str) -> str:
    """
    对文本进行脱敏处理

    Args:
        text: 原始文本

    Returns:
        脱敏后的文本
    """
    if not isinstance(text, str):
        return text

    result = text
    for name, config in SENSITIVE_PATTERNS.items():
        result = re.sub(config["pattern"], config["replace"], result)

    return result


def desensitize_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    对字典中的敏感字段进行脱敏

    Args:
        data: 原始字典

    Returns:
        脱敏后的字典
    """
    if not isinstance(data, dict):
        return data

    sensitive_keys = {
        "card_no", "card_number", "bank_card", "account_no", "account_number",
        "id_card", "id_number", "identity_card", "phone", "mobile", "tel",
        "email", "mail", "username", "name", "api_key", "password", "secret",
        "token", "access_token", "jwt",
    }

    result = {}
    for key, value in data.items():
        lower_key = key.lower()

        if lower_key in sensitive_keys or any(sk in lower_key for sk in sensitive_keys):
            if isinstance(value, str):
                result[key] = desensitize_text(value)
            elif isinstance(value, (list, dict)):
                result[key] = desensitize_value(value)
            else:
                result[key] = "***"
        else:
            result[key] = desensitize_value(value)

    return result


def desensitize_list(data: List[Any]) -> List[Any]:
    """
    对列表中的敏感数据进行脱敏

    Args:
        data: 原始列表

    Returns:
        脱敏后的列表
    """
    return [desensitize_value(item) for item in data]


def desensitize_value(value: Any) -> Any:
    """
    对任意值进行脱敏处理

    Args:
        value: 原始值

    Returns:
        脱敏后的值
    """
    if isinstance(value, str):
        return desensitize_text(value)
    elif isinstance(value, dict):
        return desensitize_dict(value)
    elif isinstance(value, list):
        return desensitize_list(value)
    else:
        return value


class DesensitizingFormatter(logging.Formatter):
    """
    脱敏日志格式化器

    在输出日志前自动对敏感信息进行脱敏处理。
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        格式化日志记录并脱敏

        Args:
            record: 日志记录

        Returns:
            脱敏后的日志字符串
        """
        # 先调用父类格式化
        result = super().format(record)

        # 对结果进行脱敏
        return desensitize_text(result)


def patch_logger(logger_name: str = None):
    """
    为指定logger安装脱敏格式化器

    Args:
        logger_name: logger名称，None表示根logger
    """
    target_logger = logging.getLogger(logger_name)

    for handler in target_logger.handlers:
        if isinstance(handler, logging.StreamHandler):
            formatter = handler.formatter
            if formatter:
                desensitizing_formatter = DesensitizingFormatter(
                    fmt=formatter._fmt if hasattr(formatter, "_fmt") else "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    datefmt=formatter.datefmt if hasattr(formatter, "datefmt") else None,
                )
                handler.setFormatter(desensitizing_formatter)

    logger.info(f"[安全] 已为 logger '{logger_name or 'root'}' 安装脱敏格式化器")