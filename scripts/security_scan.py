#!/usr/bin/env python3
"""
供应链安全扫描脚本（D2 修复）

使用 pip-audit 或 safety 扫描已安装依赖的已知漏洞（CVE）。
可在 CI/CD 流水线中集成，阻断含已知 CVE 的依赖进入生产环境。

用法:
    python scripts/security_scan.py              # 扫描并输出报告
    python scripts/security_scan.py --strict     # 发现漏洞时退出码非零（CI 用）
    python scripts/security_scan.py --file requirements-lock.txt  # 扫描指定文件

依赖:
    pip install pip-audit   # 或 pip install safety
"""
import sys
import subprocess
import json
from pathlib import Path
from datetime import datetime


def run_pip_audit(strict: bool = False, requirements_file: str = None) -> dict:
    """使用 pip-audit 扫描依赖漏洞

    Args:
        strict: 是否严格模式（发现漏洞退出码非零）
        requirements_file: 指定 requirements 文件

    Returns:
        扫描结果字典
    """
    result = {
        "scanner": "pip-audit",
        "timestamp": datetime.now().isoformat(),
        "vulnerabilities": [],
        "total_vulns": 0,
        "status": "success",
    }

    try:
        cmd = ["pip-audit", "--format", "json"]
        if requirements_file:
            cmd.extend(["-r", requirements_file])

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        if proc.returncode == 0:
            result["status"] = "clean"
            return result

        # pip-audit 发现漏洞时返回码 1，输出 JSON
        try:
            output = json.loads(proc.stdout)
            deps = output.get("dependencies", [])
            for dep in deps:
                for vuln in dep.get("vulns", []):
                    result["vulnerabilities"].append({
                        "package": dep.get("name", ""),
                        "version": dep.get("version", ""),
                        "id": vuln.get("id", ""),
                        "fix_versions": vuln.get("fix_versions", []),
                        "description": vuln.get("description", ""),
                    })
            result["total_vulns"] = len(result["vulnerabilities"])
            result["status"] = "vulnerable" if result["total_vulns"] > 0 else "clean"
        except json.JSONDecodeError:
            result["status"] = "error"
            result["error"] = f"无法解析 pip-audit 输出: {proc.stdout[:200]}"

    except FileNotFoundError:
        result["status"] = "scanner_not_found"
        result["error"] = "pip-audit 未安装，请运行: pip install pip-audit"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


def run_safety_scan(strict: bool = False, requirements_file: str = None) -> dict:
    """使用 safety 扫描依赖漏洞

    Args:
        strict: 是否严格模式
        requirements_file: 指定 requirements 文件

    Returns:
        扫描结果字典
    """
    result = {
        "scanner": "safety",
        "timestamp": datetime.now().isoformat(),
        "vulnerabilities": [],
        "total_vulns": 0,
        "status": "success",
    }

    try:
        cmd = ["safety", "check", "--json"]
        if requirements_file:
            cmd.extend(["--file", requirements_file])
        else:
            cmd.append("--full-report")

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        try:
            output = json.loads(proc.stdout)
            vulns = output.get("vulnerabilities", [])
            for vuln in vulns:
                result["vulnerabilities"].append({
                    "package": vuln.get("package_name", ""),
                    "version": vuln.get("analyzed_version", ""),
                    "id": vuln.get("vulnerability_id", ""),
                    "advisory": vuln.get("advisory", ""),
                    "severity": vuln.get("severity", ""),
                })
            result["total_vulns"] = len(result["vulnerabilities"])
            result["status"] = "vulnerable" if result["total_vulns"] > 0 else "clean"
        except json.JSONDecodeError:
            result["status"] = "error"
            result["error"] = f"无法解析 safety 输出: {proc.stdout[:200]}"

    except FileNotFoundError:
        result["status"] = "scanner_not_found"
        result["error"] = "safety 未安装，请运行: pip install safety"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


def generate_report(result: dict) -> str:
    """生成人类可读的扫描报告

    Args:
        result: 扫描结果

    Returns:
        报告文本
    """
    lines = []
    lines.append("=" * 60)
    lines.append("  供应链安全扫描报告")
    lines.append("=" * 60)
    lines.append(f"扫描器: {result['scanner']}")
    lines.append(f"扫描时间: {result['timestamp']}")
    lines.append(f"扫描状态: {result['status']}")
    lines.append("")

    if result.get("error"):
        lines.append(f"错误: {result['error']}")
        return "\n".join(lines)

    lines.append(f"发现漏洞数: {result['total_vulns']}")
    lines.append("")

    if result["vulnerabilities"]:
        lines.append("漏洞详情:")
        lines.append("-" * 60)
        for i, vuln in enumerate(result["vulnerabilities"], 1):
            lines.append(f"  {i}. {vuln['package']}=={vuln['version']}")
            lines.append(f"     漏洞ID: {vuln.get('id', 'N/A')}")
            if vuln.get("fix_versions"):
                lines.append(f"     修复版本: {', '.join(vuln['fix_versions'])}")
            if vuln.get("severity"):
                lines.append(f"     严重程度: {vuln['severity']}")
            if vuln.get("description"):
                lines.append(f"     描述: {vuln['description'][:100]}...")
            lines.append("")
    else:
        lines.append("未发现已知漏洞 ✓")

    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="供应链安全扫描")
    parser.add_argument("--strict", action="store_true", help="严格模式，发现漏洞时退出码非零")
    parser.add_argument("--file", type=str, help="指定 requirements 文件")
    parser.add_argument("--scanner", type=str, choices=["pip-audit", "safety"],
                        default="pip-audit", help="选择扫描器（默认: pip-audit）")
    parser.add_argument("--output", type=str, help="输出 JSON 报告到指定文件")
    args = parser.parse_args()

    # 执行扫描
    if args.scanner == "pip-audit":
        result = run_pip_audit(strict=args.strict, requirements_file=args.file)
    else:
        result = run_safety_scan(strict=args.strict, requirements_file=args.file)

    # 输出报告
    print(generate_report(result))

    # 保存 JSON 报告
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 报告已保存到: {args.output}")

    # 严格模式退出码
    if args.strict and result.get("total_vulns", 0) > 0:
        sys.exit(1)

    if result["status"] in ("error", "scanner_not_found"):
        sys.exit(2)


if __name__ == "__main__":
    main()
