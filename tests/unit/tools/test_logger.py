"""
统一日志工具单元测试
"""
from tools.logger import (
    info, warn, error, section, Timer,
    enable_logging, get_log_buffer, clear_log_buffer,
)


class TestLoggerBasic:
    def setup_method(self):
        clear_log_buffer()
        enable_logging(False)

    def test_info_logged_to_buffer(self):
        """INFO 日志写入缓冲区"""
        info("test_agent", "hello world", count=42)
        buf = get_log_buffer()
        assert len(buf) == 1
        assert buf[0]["level"] == "INFO"
        assert buf[0]["agent"] == "test_agent"
        assert buf[0]["message"] == "hello world"
        assert buf[0]["data"]["count"] == 42

    def test_warn_level(self):
        """WARN 级别日志正确"""
        warn("test_agent", "something wrong")
        buf = get_log_buffer()
        assert buf[0]["level"] == "WARN"

    def test_error_level(self):
        """ERROR 级别日志正确"""
        error("test_agent", "fatal")
        buf = get_log_buffer()
        assert buf[0]["level"] == "ERROR"

    def test_clear_buffer(self):
        """清空缓冲区正常工作"""
        info("a", "msg1")
        assert len(get_log_buffer()) == 1
        clear_log_buffer()
        assert len(get_log_buffer()) == 0

    def test_multiple_logs_ordered(self):
        """多条日志按顺序记录"""
        info("a", "first")
        warn("a", "second")
        error("a", "third")
        buf = get_log_buffer()
        assert len(buf) == 3
        assert buf[0]["message"] == "first"
        assert buf[1]["message"] == "second"
        assert buf[2]["message"] == "third"


class TestTimer:
    def setup_method(self):
        clear_log_buffer()
        enable_logging(False)

    def test_timer_records_elapsed(self):
        """计时器正确记录耗时"""
        import time
        with Timer("test_agent", "test_op") as t:
            time.sleep(0.01)
        assert t.elapsed >= 0.01
        assert t.elapsed < 1.0

    def test_timer_no_label(self):
        """无 label 计时器不输出日志"""
        with Timer("test_agent") as t:
            pass
        assert t.elapsed >= 0

    def test_section_output(self):
        """section 函数不报错"""
        enable_logging(True)
        section("Test", "Test Section")
        enable_logging(False)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
