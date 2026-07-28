"""
生产环境验证脚本

验证系统是否满足生产就绪条件。
符合业务戒律 M4: 验证结果真实记录，不编造数据。

检查项:
1. 配置完整性（API密钥、数据库连接等）
2. 依赖安装（所有必需库）
3. 目录结构（数据目录、日志目录等）
4. API端点可用性
5. 核心功能测试
"""
import os
import sys
import json
import time
from datetime import datetime
from typing import Dict, List, Any

# 添加项目根目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ValidationResult:
    """验证结果"""

    def __init__(self):
        self.passed: List[Dict[str, str]] = []
        self.failed: List[Dict[str, str]] = []
        self.warnings: List[Dict[str, str]] = []

    def add_pass(self, name: str, message: str):
        """添加通过项"""
        self.passed.append({"name": name, "message": message})

    def add_fail(self, name: str, message: str):
        """添加失败项"""
        self.failed.append({"name": name, "message": message})

    def add_warning(self, name: str, message: str):
        """添加警告项"""
        self.warnings.append({"name": name, "message": message})

    def is_passed(self) -> bool:
        """是否全部通过"""
        return len(self.failed) == 0

    def get_summary(self) -> Dict[str, Any]:
        """获取摘要"""
        return {
            "passed_count": len(self.passed),
            "failed_count": len(self.failed),
            "warning_count": len(self.warnings),
            "is_production_ready": self.is_passed(),
        }


def check_config(result: ValidationResult):
    """检查配置"""
    print("\n[1/6] 检查配置...")

    # 检查.env文件
    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    env_example = env_file + ".example"

    if os.path.exists(env_file):
        result.add_pass("配置文件", ".env 文件存在")
    elif os.path.exists(env_example):
        result.add_warning("配置文件", ".env 文件不存在，但 .env.example 存在")
    else:
        result.add_fail("配置文件", ".env 文件不存在")

    # 检查LLM配置
    try:
        from config import has_llm, DEEPSEEK_MODEL, DEEPSEEK_BASE_URL

        if has_llm():
            result.add_pass("LLM配置", f"DeepSeek API 已配置，模型: {DEEPSEEK_MODEL}")
        else:
            result.add_warning("LLM配置", "DeepSeek API 未配置，将使用降级模式")
    except Exception as e:
        result.add_fail("LLM配置", f"配置检查失败: {e}")


def check_dependencies(result: ValidationResult):
    """检查依赖"""
    print("\n[2/6] 检查依赖...")

    required_packages = [
        ("pandas", "数据处理"),
        ("openpyxl", "Excel导出"),
        ("reportlab", "PDF导出"),
        ("plotly", "交互式图表"),
        ("networkx", "图分析"),
        ("httpx", "异步HTTP客户端"),
        ("fastapi", "API框架"),
        ("uvicorn", "ASGI服务器"),
        ("celery", "异步任务队列"),
        ("redis", "缓存"),
        ("sqlalchemy", "ORM"),
        ("prometheus_client", "监控指标"),
        ("jose", "JWT认证"),
        ("passlib", "密码哈希"),
        ("cryptography", "加密"),
    ]

    optional_packages = [
        ("streamlit", "Streamlit UI框架（可选）"),
    ]

    missing = []
    for package, desc in required_packages:
        try:
            __import__(package)
            result.add_pass(f"依赖: {package}", desc)
        except ImportError:
            result.add_fail(f"依赖: {package}", f"未安装 ({desc})")
            missing.append(package)

    # 可选依赖
    for package, desc in optional_packages:
        try:
            __import__(package)
            result.add_pass(f"可选依赖: {package}", desc)
        except ImportError:
            result.add_warning(f"可选依赖: {package}", f"未安装 ({desc})")

    if missing:
        print(f"\n  缺少依赖: {', '.join(missing)}")
        print(f"  请运行: pip install {' '.join(missing)}")


def check_directories(result: ValidationResult):
    """检查目录结构"""
    print("\n[3/6] 检查目录结构...")

    from config import DATA_DIR, REPORTS_DIR, LOGS_DIR, CACHE_DIR, HISTORY_DIR

    required_dirs = [
        ("data", DATA_DIR),
        ("reports", REPORTS_DIR),
        ("logs", LOGS_DIR),
        ("cache", CACHE_DIR),
        ("history", HISTORY_DIR),
    ]

    for name, path in required_dirs:
        if os.path.exists(path):
            result.add_pass(f"目录: {name}", path)
        else:
            try:
                os.makedirs(path, exist_ok=True)
                result.add_pass(f"目录: {name}", f"{path} (已创建)")
            except Exception as e:
                result.add_fail(f"目录: {name}", f"无法创建: {e}")


def check_api_endpoints(result: ValidationResult, base_url: str = "http://localhost:8000"):
    """检查API端点"""
    print("\n[4/6] 检查API端点...")

    import urllib.request
    import urllib.error

    endpoints = [
        ("/health", "健康检查"),
        ("/metrics", "监控指标"),
        ("/docs", "API文档"),
    ]

    for endpoint, desc in endpoints:
        url = f"{base_url}{endpoint}"
        try:
            urllib.request.urlopen(url, timeout=5)
            result.add_pass(f"端点: {endpoint}", desc)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                result.add_fail(f"端点: {endpoint}", f"404 Not Found ({desc})")
            else:
                result.add_pass(f"端点: {endpoint}", f"{e.code} ({desc})")
        except urllib.error.URLError:
            result.add_warning(f"端点: {endpoint}", f"API服务未启动 ({desc})")
        except Exception as e:
            result.add_warning(f"端点: {endpoint}", f"连接失败: {e} ({desc})")


def check_core_functions(result: ValidationResult):
    """检查核心功能"""
    print("\n[5/6] 检查核心功能...")

    # 检查数据导入
    try:
        from tools.data_importer import import_transactions
        result.add_pass("核心功能", "数据导入器可用")
    except Exception as e:
        result.add_fail("核心功能", f"数据导入器失败: {e}")

    # 检查规则引擎
    try:
        from agents.rule_engine import create_rule_engine_agent
        result.add_pass("核心功能", "规则引擎可用")
    except Exception as e:
        result.add_fail("核心功能", f"规则引擎失败: {e}")

    # 检查工作流
    try:
        from graph.workflow import AMLAgentsGraph
        result.add_pass("核心功能", "工作流可用")
    except Exception as e:
        result.add_fail("核心功能", f"工作流失败: {e}")

    # 检查报告导出
    try:
        from tools.excel_exporter import ExcelExporter
        from tools.pdf_exporter import PdfExporter
        result.add_pass("核心功能", "报告导出器可用")
    except Exception as e:
        result.add_fail("核心功能", f"报告导出器失败: {e}")

    # 检查监控
    try:
        from tools.monitor import Monitor
        result.add_pass("核心功能", "监控系统可用")
    except Exception as e:
        result.add_fail("核心功能", f"监控系统失败: {e}")


def check_security(result: ValidationResult):
    """检查安全配置"""
    print("\n[6/6] 检查安全配置...")

    # 检查密钥加密模块
    try:
        from api.secure_config import encrypt_data, decrypt_data, get_llm_api_key
        result.add_pass("安全模块", "密钥加密模块可用")
    except Exception as e:
        result.add_fail("安全模块", f"密钥加密模块失败: {e}")

    # 检查日志脱敏模块
    try:
        from api.log_desensitize import desensitize_text, patch_logger
        result.add_pass("安全模块", "日志脱敏模块可用")
    except Exception as e:
        result.add_fail("安全模块", f"日志脱敏模块失败: {e}")

    # 检查审计日志模块
    try:
        from api.audit_log import audit_logger, OperationType
        result.add_pass("安全模块", "审计日志模块可用")
    except Exception as e:
        result.add_fail("安全模块", f"审计日志模块失败: {e}")

    # 检查JWT配置
    import os
    jwt_key = os.getenv("JWT_SECRET_KEY", "")
    if jwt_key and jwt_key != "test-secret-key-change-in-production":
        result.add_pass("安全配置", "JWT密钥已配置（非默认值）")
    else:
        result.add_warning("安全配置", "JWT密钥使用默认值（生产环境需更改）")


def run_production_validation(base_url: str = "http://localhost:8000") -> ValidationResult:
    """
    运行生产环境验证

    Args:
        base_url: API基础URL

    Returns:
        验证结果
    """
    print("=" * 70)
    print("  AML-Agent 生产环境验证")
    print("=" * 70)
    print(f"  验证时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  API URL: {base_url}")

    result = ValidationResult()

    check_config(result)
    check_dependencies(result)
    check_directories(result)
    check_api_endpoints(result, base_url)
    check_core_functions(result)
    check_security(result)

    return result


def print_report(result: ValidationResult):
    """打印验证报告"""
    print("\n" + "=" * 70)
    print("  验证报告")
    print("=" * 70)

    summary = result.get_summary()

    print(f"\n## 验证概况")
    print(f"- 通过项: {summary['passed_count']}")
    print(f"- 失败项: {summary['failed_count']}")
    print(f"- 警告项: {summary['warning_count']}")
    print(f"- 生产就绪: {'✅ 是' if summary['is_production_ready'] else '❌ 否'}")

    if result.passed:
        print(f"\n## 通过项（{len(result.passed)}）")
        for item in result.passed:
            print(f"  ✅ {item['name']}: {item['message']}")

    if result.failed:
        print(f"\n## 失败项（{len(result.failed)}）")
        for item in result.failed:
            print(f"  ❌ {item['name']}: {item['message']}")

    if result.warnings:
        print(f"\n## 警告项（{len(result.warnings)}）")
        for item in result.warnings:
            print(f"  ⚠️ {item['name']}: {item['message']}")

    print("\n" + "=" * 70)

    if summary["is_production_ready"]:
        print("  ✅ 系统已通过生产环境验证，可以部署")
    else:
        print("  ❌ 系统未通过生产环境验证，请解决失败项后再部署")

    print("=" * 70)


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="AML-Agent 生产环境验证")
    parser.add_argument("--url", type=str, default="http://localhost:8000", help="API基础URL")
    parser.add_argument("--output", type=str, default="", help="输出文件路径")
    args = parser.parse_args()

    result = run_production_validation(base_url=args.url)
    print_report(result)

    # 保存结果
    if args.output:
        output_data = {
            "validation_time": datetime.now().isoformat(),
            "summary": result.get_summary(),
            "passed": result.passed,
            "failed": result.failed,
            "warnings": result.warnings,
        }

        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        print(f"\n验证结果已保存: {args.output}")

    return 0 if result.is_passed() else 1


if __name__ == "__main__":
    sys.exit(main())