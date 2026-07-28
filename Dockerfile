# AML-Agent 反洗钱分析系统 - 生产环境 Docker 镜像
# 基于 Python 3.10 slim，符合生产环境安全规范

# ===== 基础镜像 =====
FROM python:3.10-slim

# ===== 元数据 =====
LABEL maintainer="AML-Agent Team"
LABEL version="1.0.0"
LABEL description="Anti-Money Laundering Multi-Agent System"

# ===== 环境变量 =====
# Python 不缓冲输出（日志实时显示）
ENV PYTHONUNBUFFERED=1 \
    # 禁用 Python 字节码缓存（镜像更小）
    PYTHONDONTWRITEBYTECODE=1 \
    # 设置工作目录
    WORKDIR=/app \
    # 语言设置（中文支持）
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# ===== 系统依赖安装 =====
# 安装必要的系统库（PostgreSQL 客户端、编译工具）
RUN apt-get update && apt-get install -y --no-install-recommends \
    # PostgreSQL 客户端库
    libpq-dev \
    # 编译依赖（某些 Python 包需要）
    gcc \
    # 清理缓存（减少镜像大小）
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ===== 工作目录设置 =====
WORKDIR /app

# ===== 依赖安装 =====
# 先复制依赖文件（利用 Docker 缓存）
COPY requirements.txt requirements-production.txt ./

# 安装 Python 依赖
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r requirements-production.txt

# ===== 应用代码复制 =====
# 复制所有源代码
COPY . /app/

# ===== 数据目录创建 =====
# 创建必要的数据目录（符合 M1: 使用真实数据）
RUN mkdir -p /app/data/history \
             /app/data/profiles \
             /app/data/cache \
             /app/data/alerts \
             /app/reports \
             /app/logs \
             /app/exports

# ===== 创建非 root 用户 =====
# 安全最佳实践：不以 root 运行应用
RUN addgroup --system app && adduser --system --ingroup app app
RUN chown -R app:app /app

# ===== 端口暴露 =====
# API 服务端口（FastAPI + Uvicorn）
EXPOSE 8000

# Streamlit 端口（Web 界面）
EXPOSE 8501

# ===== 切换非 root 用户 =====
USER app

# ===== 健康检查 =====
# 使用 Python 标准库（不依赖 requests 库）
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5)" || exit 1

# ===== 启动命令 =====
# 默认启动 API 服务（可通过 docker-compose 覆盖）
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]