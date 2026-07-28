"""
数据导入器单元测试
"""
import sys
import os
import json
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.data_importer import (
    import_transactions,
    detect_file_format,
    _auto_detect_mapping,
    _parse_amount,
    _parse_timestamp,
    _validate_transaction,
)


class TestDetectFileFormat:
    def test_csv(self):
        assert detect_file_format("data.csv") == "csv"

    def test_excel_xlsx(self):
        assert detect_file_format("data.xlsx") == "excel"

    def test_excel_xls(self):
        assert detect_file_format("data.xls") == "excel"

    def test_json(self):
        assert detect_file_format("data.json") == "json"

    def test_unknown(self):
        assert detect_file_format("data.txt") == "unknown"


class TestParseAmount:
    def test_normal_number(self):
        assert _parse_amount("1234.56") == 1234.56

    def test_with_comma(self):
        assert _parse_amount("12,345.67") == 12345.67

    def test_with_currency(self):
        assert _parse_amount("¥1000") == 1000.0

    def test_int(self):
        assert _parse_amount(5000) == 5000.0

    def test_float(self):
        assert _parse_amount(3.14) == 3.14

    def test_empty(self):
        assert _parse_amount("") is None

    def test_none(self):
        assert _parse_amount(None) is None

    def test_invalid(self):
        assert _parse_amount("abc") is None


class TestParseTimestamp:
    def test_iso_format(self):
        result = _parse_timestamp("2026-07-27T10:30:00")
        assert result is not None
        assert "2026-07-27" in result

    def test_standard_format(self):
        result = _parse_timestamp("2026-07-27 10:30:00")
        assert result is not None
        assert "2026-07-27" in result

    def test_slash_format(self):
        result = _parse_timestamp("2026/07/27 10:30:00")
        assert result is not None

    def test_date_only(self):
        result = _parse_timestamp("2026-07-27")
        assert result is not None
        assert "2026-07-27" in result

    def test_datetime_object(self):
        from datetime import datetime
        dt = datetime(2026, 7, 27, 10, 30)
        result = _parse_timestamp(dt)
        assert result is not None
        assert "2026-07-27" in result

    def test_invalid(self):
        assert _parse_timestamp("not a date") is None

    def test_empty(self):
        assert _parse_timestamp("") is None


class TestAutoDetectMapping:
    def test_chinese_headers(self):
        columns = ["交易流水号", "付款账号", "收款账号", "交易金额", "交易时间", "摘要"]
        mapping = _auto_detect_mapping(columns)
        assert mapping["transaction_id"] == "交易流水号"
        assert mapping["from_account"] == "付款账号"
        assert mapping["to_account"] == "收款账号"
        assert mapping["amount"] == "交易金额"
        assert mapping["timestamp"] == "交易时间"
        assert mapping["remark"] == "摘要"

    def test_english_headers(self):
        columns = ["transaction_id", "from_account", "to_account", "amount", "timestamp", "remark"]
        mapping = _auto_detect_mapping(columns)
        assert mapping["transaction_id"] == "transaction_id"
        assert mapping["from_account"] == "from_account"

    def test_partial_match(self):
        columns = ["流水号", "付款方账号", "收款方账号", "金额", "交易日期"]
        mapping = _auto_detect_mapping(columns)
        assert "transaction_id" in mapping
        assert "from_account" in mapping
        assert "to_account" in mapping


class TestValidateTransaction:
    def test_valid_transaction(self):
        txn = {
            "transaction_id": "T1",
            "from_account": "A001",
            "to_account": "A002",
            "amount": 1000,
            "timestamp": "2026-07-27T10:00:00",
        }
        is_valid, errors = _validate_transaction(txn, 0)
        assert is_valid
        assert len(errors) == 0

    def test_missing_from_account(self):
        txn = {
            "to_account": "A002",
            "amount": 1000,
            "timestamp": "2026-07-27T10:00:00",
        }
        is_valid, errors = _validate_transaction(txn, 0)
        assert not is_valid
        assert any("from_account" in e for e in errors)

    def test_negative_amount(self):
        txn = {
            "from_account": "A001",
            "to_account": "A002",
            "amount": -100,
            "timestamp": "2026-07-27T10:00:00",
        }
        is_valid, errors = _validate_transaction(txn, 0)
        assert not is_valid
        assert any("金额" in e for e in errors)

    def test_amount_string_converted(self):
        txn = {
            "from_account": "A001",
            "to_account": "A002",
            "amount": "12,345.67",
            "timestamp": "2026-07-27T10:00:00",
        }
        is_valid, _ = _validate_transaction(txn, 0)
        assert is_valid
        assert txn["amount"] == 12345.67


class TestImportCSV:
    def _make_csv(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8-sig")
        f.write(content)
        f.close()
        return f.name

    def test_valid_csv_import(self):
        csv_content = """交易流水号,付款账号,收款账号,交易金额,交易时间,摘要
T001,A001,A002,10000,2026-07-27 10:00:00,工资
T002,A002,A003,20000,2026-07-27 11:00:00,货款
T003,A003,A001,5000,2026-07-27 12:00:00,转账
"""
        path = self._make_csv(csv_content)
        try:
            result = import_transactions(path)
            assert result["total"] == 3
            assert result["valid"] == 3
            assert len(result["transactions"]) == 3
            assert result["source_format"] == "csv"
            assert result["transactions"][0]["from_account"] == "A001"
            assert result["transactions"][0]["amount"] == 10000
        finally:
            os.unlink(path)

    def test_csv_missing_field_strict(self):
        csv_content = """付款账号,收款账号,金额
A001,A002,10000
"""
        path = self._make_csv(csv_content)
        try:
            with pytest.raises(ValueError, match="缺少必填字段映射"):
                import_transactions(path, strict=True)
        finally:
            os.unlink(path)

    def test_csv_with_amount_comma(self):
        csv_content = """交易流水号,付款账号,收款账号,交易金额,交易时间,摘要
T001,A001,A002,"12,345.67",2026-07-27 10:00:00,测试
"""
        path = self._make_csv(csv_content)
        try:
            result = import_transactions(path)
            assert result["valid"] == 1
            assert result["transactions"][0]["amount"] == 12345.67
        finally:
            os.unlink(path)


class TestImportJSON:
    def _make_json(self, data) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(data, f, ensure_ascii=False)
        f.close()
        return f.name

    def test_json_list_import(self):
        data = [
            {"transaction_id": "T1", "from_account": "A1", "to_account": "A2", "amount": 1000, "timestamp": "2026-07-27T10:00:00"},
            {"transaction_id": "T2", "from_account": "A2", "to_account": "A3", "amount": 2000, "timestamp": "2026-07-27T11:00:00"},
        ]
        path = self._make_json(data)
        try:
            result = import_transactions(path)
            assert result["total"] == 2
            assert result["valid"] == 2
            assert result["source_format"] == "json"
        finally:
            os.unlink(path)

    def test_json_nested_transactions(self):
        data = {
            "transactions": [
                {"transaction_id": "T1", "from_account": "A1", "to_account": "A2", "amount": 1000, "timestamp": "2026-07-27T10:00:00"},
            ]
        }
        path = self._make_json(data)
        try:
            result = import_transactions(path)
            assert result["total"] == 1
            assert result["valid"] == 1
        finally:
            os.unlink(path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
