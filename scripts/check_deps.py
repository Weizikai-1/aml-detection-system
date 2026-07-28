"""检查所有 Python 文件的第三方依赖导入，对比 requirements.txt 完整性"""
import ast
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 标准库（Python 3.10）
STDLIB = {
    "abc", "argparse", "ast", "asyncio", "base64", "bisect", "calendar",
    "collections", "concurrent", "configparser", "contextlib", "copy",
    "csv", "ctypes", "dataclasses", "datetime", "decimal", "difflib",
    "email", "enum", "errno", "hashlib", "heapq", "hmac", "html",
    "http", "importlib", "inspect", "io", "ipaddress", "itertools",
    "json", "keyword", "logging", "math", "mimetypes", "multiprocessing",
    "numbers", "operator", "os", "pathlib", "pickle", "platform",
    "pprint", "queue", "random", "re", "shutil", "signal", "site",
    "smtplib", "socket", "sqlite3", "ssl", "stat", "string", "struct",
    "subprocess", "sys", "tempfile", "textwrap", "threading", "time",
    "traceback", "types", "typing", "unittest", "urllib", "uuid",
    "warnings", "weakref", "xml", "zipfile", "zlib", "functools",
    "getpass", "glob", "gzip", "jsonlines", "linecache",
}

# 项目内部模块前缀
INTERNAL_PREFIXES = {
    "agents", "api", "config", "data", "graph", "models", "reports",
    "services", "tests", "tools", "utils", "scripts", "main", "app",
}

# requirements.txt 中已知的包名 → 导入名映射
REQ_TO_IMPORT = {
    "langgraph": "langgraph",
    "langchain": "langchain",
    "langchain-openai": "langchain_openai",
    "langchain-core": "langchain_core",
    "openai": "openai",
    "pandas": "pandas",
    "numpy": "numpy",
    "openpyxl": "openpyxl",
    "reportlab": "reportlab",
    "networkx": "networkx",
    "python-dotenv": "dotenv",
    "loguru": "loguru",
    "pytest": "pytest",
    "torch": "torch",
    "torch-geometric": "torch_geometric",
    "plotly": "plotly",
    "streamlit": "streamlit",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "python-multipart": "multipart",
    "sqlalchemy": "sqlalchemy",
    "psycopg2-binary": "psycopg2",
    "python-jose": "jose",
    "passlib": "passlib",
    "bcrypt": "bcrypt",
    "cryptography": "cryptography",
    "celery": "celery",
    "redis": "redis",
    "prometheus-client": "prometheus_client",
    "slowapi": "slowapi",
}

# 反向：导入名 → requirements 包名
IMPORT_TO_REQ = {v: k for k, v in REQ_TO_IMPORT.items()}

# 手动补充的一些特殊情况
IMPORT_TO_REQ.update({
    "pydantic": "pydantic",  # fastapi 依赖，通常不单独列
    "starlette": "starlette",  # fastapi 依赖
    "httpx": "httpx",  # openai 依赖
    "tiktoken": "tiktoken",  # langchain 依赖
    "tenacity": "tenacity",  # langchain 依赖
    "yaml": "pyyaml",  # 常见
    "PIL": "pillow",  # 常见
    "jinja2": "jinja2",  # 常见
})


def collect_imports(filepath: str) -> set:
    """收集一个 Python 文件中的所有顶级导入"""
    imports = set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    imports.add(top)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    top = node.module.split(".")[0]
                    imports.add(top)
    except SyntaxError:
        pass
    return imports


def main():
    all_imports = set()
    py_files = []

    for root, dirs, files in os.walk(PROJECT_ROOT):
        # 跳过不需要的目录
        dirs[:] = [
            d for d in dirs
            if d not in ("__pycache__", ".git", "venv", "env", ".venv", "node_modules")
        ]
        for f in files:
            if f.endswith(".py"):
                py_files.append(os.path.join(root, f))

    print(f"扫描 {len(py_files)} 个 Python 文件...")

    for filepath in py_files:
        all_imports.update(collect_imports(filepath))

    # 过滤掉标准库和内部模块
    third_party = set()
    for imp in sorted(all_imports):
        if imp in STDLIB:
            continue
        if imp in INTERNAL_PREFIXES:
            continue
        if imp.startswith("_"):
            continue
        third_party.add(imp)

    # 读取 requirements.txt
    req_path = os.path.join(PROJECT_ROOT, "requirements.txt")
    req_packages = set()
    with open(req_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # 去掉版本号
            pkg = line.split(">=")[0].split("==")[0].split("<")[0].split("[")[0].strip()
            req_packages.add(pkg.lower())

    print(f"\nrequirements.txt 中声明的包 ({len(req_packages)} 个):")
    for pkg in sorted(req_packages):
        print(f"  - {pkg}")

    # 检查哪些第三方导入在 requirements 中找不到
    missing = []
    for imp in sorted(third_party):
        req_name = IMPORT_TO_REQ.get(imp, imp).lower()
        # 检查是否在 requirements 中（模糊匹配）
        found = any(req_name in r or r in req_name for r in req_packages)
        if not found:
            # 再检查包名中含有关键字的
            alt = imp.replace("_", "-")
            found2 = any(alt in r or r in alt for r in req_packages)
            if not found2:
                missing.append(imp)

    print(f"\n发现 {len(third_party)} 个第三方导入")
    print(f"其中 {len(missing)} 个可能未在 requirements.txt 中声明:")
    for imp in missing:
        print(f"  ⚠  import {imp}")

    # 反向：检查 requirements 中有没有代码里根本不用的
    unused = []
    for req_pkg in req_packages:
        # 找对应的导入名
        import_name = REQ_TO_IMPORT.get(req_pkg, req_pkg.replace("-", "_"))
        # 检查是否有任何导入使用了这个包
        found = False
        for imp in third_party:
            if imp == import_name or imp.startswith(import_name + "_") or import_name.startswith(imp + "_"):
                found = True
                break
        # 被注释掉的也算
        if not found and req_pkg not in ("python-dotenv",):
            unused.append(req_pkg)

    if unused:
        print(f"\n{len(unused)} 个 requirements 中的包可能未被代码直接使用（可能是间接依赖）:")
        for pkg in sorted(unused):
            print(f"  - {pkg}")

    print("\n" + "=" * 60)
    if missing:
        print("⚠ 有缺失依赖，请检查！")
        return 1
    else:
        print("✅ 所有第三方导入都能在 requirements.txt 中找到对应声明")
        return 0


if __name__ == "__main__":
    sys.exit(main())
