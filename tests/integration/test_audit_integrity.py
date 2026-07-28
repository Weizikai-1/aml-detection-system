"""
审计日志哈希链完整性测试

测试 M11 修复：审计日志防篡改完整性保护

覆盖场景:
1. 哈希链生成：第一条日志 prev_hash=GENESIS，后续日志链式关联
2. 完整性验证：正常日志通过验证
3. 篡改检测：修改日志内容后检测到篡改
4. 删除检测：删除日志条目后检测到链断裂
5. 脱敏兼容：脱敏后的日志仍能正确验证
6. 多文件验证：verify_all_files 正确工作
"""
import json
import os
import pytest
from datetime import datetime

from api.audit_log import (
    AuditLogger,
    AuditEntry,
    OperationType,
    OperationResult,
    _compute_entry_hash,
    _GENESIS_HASH,
)


@pytest.fixture()
def audit_logger(tmp_path):
    """创建临时审计日志实例"""
    return AuditLogger(log_dir=str(tmp_path))


@pytest.fixture()
def clean_audit_logger(tmp_path):
    """创建全新的审计日志实例（确保无历史数据）"""
    log_dir = str(tmp_path / "audit_test")
    os.makedirs(log_dir, exist_ok=True)
    return AuditLogger(log_dir=log_dir)


class TestHashChainGeneration:
    """测试哈希链生成逻辑"""

    def test_first_entry_prev_hash_is_genesis(self, clean_audit_logger):
        """第一条日志的 prev_hash 应为 GENESIS"""
        logger = clean_audit_logger

        logger.log_success(
            operation_type=OperationType.SYSTEM,
            action="系统启动",
        )

        log_path = logger._get_log_file_path()
        with open(log_path, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())

        assert entry["prev_hash"] == _GENESIS_HASH
        assert entry["current_hash"] != ""
        assert len(entry["current_hash"]) == 64  # SHA256 hex 长度

    def test_chain_linkage_between_entries(self, clean_audit_logger):
        """连续日志的 prev_hash 应等于前一条的 current_hash"""
        logger = clean_audit_logger

        # 写入3条日志
        for i in range(3):
            logger.log_success(
                operation_type=OperationType.SYSTEM,
                action=f"操作{i}",
            )

        log_path = logger._get_log_file_path()
        entries = []
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                entries.append(json.loads(line.strip()))

        assert len(entries) == 3
        # 第一条 prev_hash = GENESIS
        assert entries[0]["prev_hash"] == _GENESIS_HASH
        # 第二条 prev_hash = 第一条 current_hash
        assert entries[1]["prev_hash"] == entries[0]["current_hash"]
        # 第三条 prev_hash = 第二条 current_hash
        assert entries[2]["prev_hash"] == entries[1]["current_hash"]

    def test_current_hash_is_deterministic(self, clean_audit_logger):
        """相同内容 + 相同 prev_hash 应产生相同 current_hash"""
        logger = clean_audit_logger

        entry1 = AuditEntry(
            operation_type=OperationType.AUTH,
            action="登录",
            user_id="user001",
            username="admin",
            ip_address="192.168.1.1",
        )
        entry_dict = entry1.to_dict()
        # 脱敏处理（与 log 方法一致）
        entry_dict["details"] = {}

        hash1 = _compute_entry_hash(_GENESIS_HASH, entry_dict)
        hash2 = _compute_entry_hash(_GENESIS_HASH, entry_dict)

        assert hash1 == hash2
        assert len(hash1) == 64

    def test_different_content_produces_different_hash(self):
        """不同内容应产生不同哈希"""
        entry1 = {"entry_id": "aaa", "action": "登录", "timestamp": "2026-01-01"}
        entry2 = {"entry_id": "bbb", "action": "登出", "timestamp": "2026-01-02"}

        hash1 = _compute_entry_hash(_GENESIS_HASH, entry1)
        hash2 = _compute_entry_hash(_GENESIS_HASH, entry2)

        assert hash1 != hash2

    def test_hash_excludes_hash_fields(self):
        """哈希计算应排除 prev_hash 和 current_hash 字段"""
        entry_base = {"entry_id": "aaa", "action": "登录"}

        # 带 hash 字段
        entry_with_hash = {
            **entry_base,
            "prev_hash": "somehash",
            "current_hash": "anotherhash",
        }

        hash1 = _compute_entry_hash(_GENESIS_HASH, entry_base)
        hash2 = _compute_entry_hash(_GENESIS_HASH, entry_with_hash)

        # 两者应相同，因为哈希字段被排除
        assert hash1 == hash2


class TestIntegrityVerification:
    """测试完整性验证"""

    def test_valid_chain_passes_verification(self, clean_audit_logger):
        """正常日志链应通过完整性验证"""
        logger = clean_audit_logger

        for i in range(5):
            logger.log_success(
                operation_type=OperationType.ANALYSIS,
                action=f"分析任务{i}",
                user_id="user001",
                username="analyst",
            )

        result = logger.verify_integrity()

        assert result["valid"] is True
        assert result["total"] == 5
        assert result["verified"] == 5
        assert result["tampered"] == 0

    def test_tampered_content_detected(self, clean_audit_logger):
        """篡改日志内容后应检测到篡改"""
        logger = clean_audit_logger

        # 写入3条日志
        for i in range(3):
            logger.log_success(
                operation_type=OperationType.ANALYSIS,
                action=f"分析任务{i}",
            )

        # 篡改第2条日志的内容
        log_path = logger._get_log_file_path()
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        tampered_entry = json.loads(lines[1])
        tampered_entry["action"] = "恶意操作"  # 篡改内容
        lines[1] = json.dumps(tampered_entry, ensure_ascii=False) + "\n"

        with open(log_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        result = logger.verify_integrity()

        assert result["valid"] is False
        assert result["tampered"] >= 1
        assert result["verified"] < result["total"]

    def test_deleted_entry_detected(self, clean_audit_logger):
        """删除中间日志条目后应检测到链断裂"""
        logger = clean_audit_logger

        # 写入4条日志
        for i in range(4):
            logger.log_success(
                operation_type=OperationType.SYSTEM,
                action=f"操作{i}",
            )

        # 删除第3条日志（索引2）
        log_path = logger._get_log_file_path()
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        del lines[2]  # 删除第3行

        with open(log_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        result = logger.verify_integrity()

        assert result["valid"] is False
        assert result["tampered"] >= 1

    def test_empty_file_passes_verification(self, clean_audit_logger):
        """空日志文件应通过验证（无条目）"""
        result = clean_audit_logger.verify_integrity()

        assert result["valid"] is True
        assert result["total"] == 0
        assert result["verified"] == 0
        assert result["tampered"] == 0

    def test_corrupted_json_detected(self, clean_audit_logger):
        """JSON解析失败的行应被标记为篡改"""
        logger = clean_audit_logger

        logger.log_success(
            operation_type=OperationType.SYSTEM,
            action="正常操作",
        )

        # 写入一行损坏的JSON
        log_path = logger._get_log_file_path()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("{invalid json content\n")

        result = logger.verify_integrity()

        assert result["valid"] is False
        assert result["tampered"] >= 1


class TestDesensitizeCompatibility:
    """测试脱敏与哈希链的兼容性"""

    def test_desensitized_log_passes_verification(self, clean_audit_logger):
        """脱敏后的日志应能通过完整性验证"""
        logger = clean_audit_logger

        # 写入包含敏感信息的日志
        logger.log_success(
            operation_type=OperationType.AUTH,
            action="用户登录",
            username="admin",
            ip_address="192.168.1.100",
            details={
                "account_number": "6222021234567890123",
                "phone": "13812345678",
                "email": "admin@example.com",
            },
        )

        result = logger.verify_integrity()

        assert result["valid"] is True
        assert result["verified"] == 1

    def test_multiple_desensitized_entries_chain(self, clean_audit_logger):
        """多条脱敏日志的哈希链应完整"""
        logger = clean_audit_logger

        sensitive_details = [
            {"card_no": "6222021234567890123", "name": "张三"},
            {"id_card": "110101199001011234", "phone": "13912345678"},
            {"email": "test@bank.com", "account_no": "6217001234567890"},
        ]

        for i, details in enumerate(sensitive_details):
            logger.log_success(
                operation_type=OperationType.REPORT,
                action=f"报告生成{i}",
                details=details,
            )

        result = logger.verify_integrity()

        assert result["valid"] is True
        assert result["total"] == 3
        assert result["verified"] == 3


class TestVerifyAllFiles:
    """测试多文件验证"""

    def test_verify_all_files_multiple_dates(self, tmp_path):
        """验证多个日期的日志文件"""
        import glob

        log_dir = str(tmp_path / "audit_multi")
        os.makedirs(log_dir, exist_ok=True)
        logger = AuditLogger(log_dir=log_dir)

        # 写入当日日志
        logger.log_success(
            operation_type=OperationType.SYSTEM,
            action="当日操作",
        )

        # 手动创建另一个日期的日志文件
        other_date_path = os.path.join(log_dir, "audit_2026-01-01.jsonl")
        entry = {
            "entry_id": "test-001",
            "timestamp": "2026-01-01T10:00:00",
            "operation_type": "SYSTEM",
            "action": "历史操作",
            "user_id": None,
            "username": None,
            "ip_address": None,
            "request_id": None,
            "details": {},
            "result": "SUCCESS",
            "error_message": None,
            "prev_hash": _GENESIS_HASH,
            "current_hash": _compute_entry_hash(_GENESIS_HASH, {
                "entry_id": "test-001",
                "timestamp": "2026-01-01T10:00:00",
                "operation_type": "SYSTEM",
                "action": "历史操作",
                "user_id": None,
                "username": None,
                "ip_address": None,
                "request_id": None,
                "details": {},
                "result": "SUCCESS",
                "error_message": None,
            }),
        }
        with open(other_date_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        results = logger.verify_all_files()

        assert len(results) >= 2
        for r in results:
            assert r["valid"] is True


class TestAuditEntryFields:
    """测试 AuditEntry 哈希字段"""

    def test_entry_to_dict_contains_hash_fields(self):
        """to_dict 应包含 prev_hash 和 current_hash"""
        entry = AuditEntry(
            operation_type=OperationType.AUTH,
            action="登录",
        )

        d = entry.to_dict()
        assert "prev_hash" in d
        assert "current_hash" in d
        assert d["prev_hash"] == _GENESIS_HASH
        assert d["current_hash"] == ""

    def test_entry_with_custom_prev_hash(self):
        """支持自定义 prev_hash"""
        entry = AuditEntry(
            operation_type=OperationType.AUTH,
            action="登录",
            prev_hash="abc123",
        )

        assert entry.prev_hash == "abc123"
