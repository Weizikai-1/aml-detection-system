# ---- 构建阶段 ----
FROM python:3.10-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- 运行阶段 ----
FROM python:3.10-slim

WORKDIR /app

# 复制依赖
COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 复制代码
COPY . .

# 创建必要目录
RUN mkdir -p data reports

EXPOSE 8000 8501

# 默认启动 FastAPI 服务
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
