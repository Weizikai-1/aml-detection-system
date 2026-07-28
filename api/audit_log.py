"""
审计日志模块

记录所有关键操作，符合业务戒律 M4: 审计可追溯。

审计日志包含:
- 操作时间
- 操作类型
- 操作人
- 操作内容
- 操作结果
- IP地址
- 请求ID（用于追踪）
- 哈希链（prev_hash / current_hash，防篡改完整性保护）

支持的操作类型:
- AUTH: 认证相关（登录、登出、token验证）
- ANALYSIS: 分析相关（提交、执行、完成）
- REPORT: 报告相关（生成、导出、删除）
- CONFIG: 配置相关（修改、回滚）
- ADMIN: 管理相关（用户管理、权限变更）
- FEEDBACK: 反馈相关（误报标记、漏报标记）

哈希链完整性保护（M11修复）:
- 每条日志记录包含 prev_hash 和 current_hash 字段
- current_hash = SHA256(prev_hash + canonical_json(entry_without_hash))
- 第一条日志的 prev_hash 为 "GENESIS"
- verify_integrity() 方法可检测任何篡改行为
"""
import os
import json
import hashlib
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)

AUDIT_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "audit")
os.makedirs(AUDIT_LOG_DIR, exist_ok=True)


class OperationType(str, Enum):
    AUTH = "AUTH"
    ANALYSIS = "ANALYSIS"
    REPORT = "REPORT"
    CONFIG = "CONFIG"
    ADMIN = "ADMIN"
    FEEDBACK = "FEEDBACK"
    SYSTEM = "SYSTEM"


class OperationResult(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PENDING = "PENDING"


# 哈希链创世值（第一条日志的 prev_hash）
_GENESIS_HASH = "GENESIS"

# 哈希链计算时需要排除的字段（这些字段本身不参与哈希计算）
_HASH_EXCLUDED_FIELDS = {"prev_hash", "current_hash"}


def _compute_entry_hash(prev_hash: str, entry_dict: Dict[str, Any]) -> str:
    """
    计算日志条目的哈希值

    哈希算法: SHA256(prev_hash + canonical_json(entry_without_hash_fields))

    Args:
        prev_hash: 上一条日志的 current_hash
        entry_dict: 日志条目字典（包含或不含 prev_hash/current_hash 均可）

    Returns:
        64位十六进制哈希字符串
    """
    # 掠除哈希字段，只对业务数据计算哈希
    content = {k: v for k, v in entry_dict.items() if k not in _HASH_EXCLUDED_FIELDS}
    # 使用 sorted keys 确保序列化稳定
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False)
    raw = prev_hash + canonical
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AuditEntry:
    """审计日志条目"""

    def __init__(
        self,
        operation_type: OperationType,
        action: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        ip_address: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        result: OperationResult = OperationResult.PENDING,
        error_message: Optional[str] = None,
        prev_hash: str = _GENESIS_HASH,
        current_hash: str = "",
    ):
        self.entry_id = str(uuid.uuid4())
        self.timestamp = datetime.now().isoformat()
        self.operation_type = operation_type.value
        self.action = action
        self.user_id = user_id
        self.username = username
        self.ip_address = ip_address
        self.request_id = request_id
        self.details = details or {}
        self.result = result.value
        self.error_message = error_message
        self.prev_hash = prev_hash
        self.current_hash = current_hash

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp,
            "operation_type": self.operation_type,
            "action": self.action,
            "user_id": self.user_id,
            "username": self.username,
            "ip_address": self.ip_address,
            "request_id": self.request_id,
            "details": self.details,
            "result": self.result,
            "error_message": self.error_message,
            "prev_hash": self.prev_hash,
            "current_hash": self.current_hash,
        }


class AuditLogger:
    """审计日志管理器"""

    def __init__(self, log_dir: str = AUDIT_LOG_DIR):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

    def _get_log_file_path(self) -> str:
        """获取当日日志文件路径"""
        today = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"audit_{today}.jsonl")

    def _get_last_hash(self, log_path: str) -> str:
        """
        获取指定日志文件中最后一条记录的 current_hash

        Args:
            log_path: 日志文件路径

        Returns:
            最后一条记录的 current_hash，无记录时返回 _GENESIS_HASH
        """
        try:
            if not os.path.exists(log_path):
                return _GENESIS_HASH

            last_hash = _GENESIS_HASH
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("current_hash"):
                            last_hash = entry["current_hash"]
                    except json.JSONDecodeError:
                        continue
            return last_hash
        except Exception:
            return _GENESIS_HASH

    def log(self, entry: AuditEntry) -> str:
        """
        记录审计日志

        哈希链逻辑:
        1. 获取上一条日志的 current_hash 作为当前 prev_hash
        2. 计算当前条目的 current_hash = SHA256(prev_hash + entry_content)
        3. 将含哈希字段的完整条目写入文件

        Args:
            entry: 审计日志条目

        Returns:
            entry_id
        """
        try:
            log_path = self._get_log_file_path()
            log_entry = entry.to_dict()

            # 对 details 字段进行脱敏处理（符合 L3 修复要求）
            from api.log_desensitize import desensitize_dict
            if log_entry.get("details"):
                log_entry["details"] = desensitize_dict(log_entry["details"])

            # 哈希链计算（M11 修复：完整性保护）
            prev_hash = self._get_last_hash(log_path)
            log_entry["prev_hash"] = prev_hash
            log_entry["current_hash"] = _compute_entry_hash(prev_hash, log_entry)

            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

            logger.debug(f"[审计] 日志记录成功: {entry.entry_id}")
            return entry.entry_id

        except Exception as e:
            logger.error(f"[审计] 日志记录失败: {e}")
            return entry.entry_id

    def log_success(
        self,
        operation_type: OperationType,
        action: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        ip_address: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        记录成功操作

        Args:
            operation_type: 操作类型
            action: 操作描述
            user_id: 用户ID
            username: 用户名
            ip_address: IP地址
            request_id: 请求ID
            details: 详细信息

        Returns:
            entry_id
        """
        entry = AuditEntry(
            operation_type=operation_type,
            action=action,
            user_id=user_id,
            username=username,
            ip_address=ip_address,
            request_id=request_id,
            details=details,
            result=OperationResult.SUCCESS,
        )
        return self.log(entry)

    def log_failed(
        self,
        operation_type: OperationType,
        action: str,
        error_message: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        ip_address: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        记录失败操作

        Args:
            operation_type: 操作类型
            action: 操作描述
            error_message: 错误信息
            user_id: 用户ID
            username: 用户名
            ip_address: IP地址
            request_id: 请求ID
            details: 详细信息

        Returns:
            entry_id
        """
        entry = AuditEntry(
            operation_type=operation_type,
            action=action,
            user_id=user_id,
            username=username,
            ip_address=ip_address,
            request_id=request_id,
            details=details,
            result=OperationResult.FAILED,
            error_message=error_message,
        )
        return self.log(entry)

    def query(
        self,
        operation_type: Optional[OperationType] = None,
        action: Optional[str] = None,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        result: Optional[OperationResult] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        查询审计日志

        Args:
            operation_type: 操作类型筛选
            action: 操作描述筛选
            user_id: 用户ID筛选
            username: 用户名筛选
            start_time: 开始时间（ISO格式）
            end_time: 结束时间（ISO格式）
            result: 操作结果筛选
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            {"total": 总数, "entries": 日志列表}
        """
        entries = []

        try:
            log_path = self._get_log_file_path()
            if not os.path.exists(log_path):
                return {"total": 0, "entries": []}

            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # 过滤
                    if operation_type and entry.get("operation_type") != operation_type.value:
                        continue
                    if action and action not in entry.get("action", ""):
                        continue
                    if user_id and entry.get("user_id") != user_id:
                        continue
                    if username and entry.get("username") != username:
                        continue
                    if result and entry.get("result") != result.value:
                        continue

                    # 时间范围过滤
                    entry_time = entry.get("timestamp", "")
                    if start_time and entry_time < start_time:
                        continue
                    if end_time and entry_time > end_time:
                        continue

                    entries.append(entry)

            entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            total = len(entries)
            entries = entries[offset : offset + limit]

        except Exception as e:
            logger.error(f"[审计] 查询失败: {e}")
            return {"total": 0, "entries": []}

        return {"total": total, "entries": entries}

    def get_entry(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单条审计日志

        Args:
            entry_id: 日志条目ID

        Returns:
            日志条目或None
        """
        import glob

        log_pattern = os.path.join(self.log_dir, "audit_*.jsonl")
        for log_path in glob.glob(log_pattern):
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            entry = json.loads(line)
                            if entry.get("entry_id") == entry_id:
                                return entry
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.error(f"[审计] 读取日志文件失败 {log_path}: {e}")

        return None

    def verify_integrity(self, log_path: str = None) -> Dict[str, Any]:
        """
        验证审计日志的哈希链完整性（M11 修复）

        从第一条日志开始，逐条重新计算哈希并比对 current_hash。
        如果任何一条日志的哈希不匹配，说明该日志被篡改。

        Args:
            log_path: 指定日志文件路径，None时验证当日日志

        Returns:
            {
                "valid": bool,           # 整体是否完整
                "total": int,            # 总条目数
                "verified": int,         # 验证通过数
                "tampered": int,         # 被篡改数
                "tampered_entries": list # 被篡改的条目信息
            }
        """
        if log_path is None:
            log_path = self._get_log_file_path()

        result = {
            "valid": True,
            "total": 0,
            "verified": 0,
            "tampered": 0,
            "tampered_entries": [],
        }

        try:
            if not os.path.exists(log_path):
                return result

            prev_hash = _GENESIS_HASH
            line_num = 0

            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    line_num += 1
                    result["total"] += 1

                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        result["valid"] = False
                        result["tampered"] += 1
                        result["tampered_entries"].append({
                            "line": line_num,
                            "reason": "JSON解析失败",
                        })
                        continue

                    # 检查 prev_hash 是否与上一条 current_hash 一致
                    stored_prev_hash = entry.get("prev_hash", "")
                    if stored_prev_hash != prev_hash:
                        result["valid"] = False
                        result["tampered"] += 1
                        result["tampered_entries"].append({
                            "line": line_num,
                            "entry_id": entry.get("entry_id", ""),
                            "reason": f"prev_hash 不匹配（期望 {prev_hash[:16]}...，实际 {stored_prev_hash[:16]}...）",
                        })
                        # 用存储的 prev_hash 继续验证
                        prev_hash = stored_prev_hash

                    # 重新计算 current_hash 并比对
                    stored_current_hash = entry.get("current_hash", "")
                    expected_hash = _compute_entry_hash(prev_hash, entry)

                    if stored_current_hash != expected_hash:
                        result["valid"] = False
                        result["tampered"] += 1
                        result["tampered_entries"].append({
                            "line": line_num,
                            "entry_id": entry.get("entry_id", ""),
                            "reason": "current_hash 不匹配（内容被篡改）",
                        })
                    else:
                        result["verified"] += 1

                    # 更新 prev_hash 为当前条目的 current_hash
                    prev_hash = stored_current_hash

        except Exception as e:
            logger.error(f"[审计] 完整性验证失败: {e}")
            result["valid"] = False
            result["tampered_entries"].append({"reason": f"验证过程异常: {e}"})

        return result

    def verify_all_files(self) -> List[Dict[str, Any]]:
        """
        验证所有审计日志文件的完整性

        Returns:
            每个文件的验证结果列表
        """
        import glob

        log_pattern = os.path.join(self.log_dir, "audit_*.jsonl")
        results = []

        for log_path in sorted(glob.glob(log_pattern)):
            verify_result = self.verify_integrity(log_path)
            results.append({
                "file": os.path.basename(log_path),
                **verify_result,
            })

        return results


# 全局审计日志实例
audit_logger = AuditLogger()