#!/bin/bash
# AML-Agent 生产环境启动脚本
# 使用方式: bash scripts/start_production.sh

set -e

echo "======================================"
echo "AML-Agent 生产环境启动"
echo "======================================"

# 检查环境变量文件
if [ ! -f .env.production ]; then
    echo "错误: 未找到 .env.production 文件"
    echo "请复制 .env.example 为 .env.production 并配置环境变量"
    exit 1
fi

# 加载环境变量
export $(grep -v '^#' .env.production | xargs)

# 检查关键配置
if [ -z "$JWT_SECRET_KEY" ]; then
    echo "警告: JWT_SECRET_KEY 未设置，将使用随机生成（重启后旧token失效）"
    export JWT_SECRET_KEY=$(openssl rand -hex 32)
fi

if [ -z "$POSTGRES_PASSWORD" ]; then
    echo "错误: POSTGRES_PASSWORD 未设置"
    exit 1
fi

if [ -z "$REDIS_PASSWORD" ]; then
    echo "错误: REDIS_PASSWORD 未设置"
    exit 1
fi

if [ -z "$DEEPSEEK_API_KEY" ]; then
    echo "警告: DEEPSEEK_API_KEY 未设置，LLM 功能将不可用"
fi

# 检查 SSL 证书
if [ ! -f deploy/nginx/ssl/cert.pem ] || [ ! -f deploy/nginx/ssl/key.pem ]; then
    echo "警告: SSL 证书不存在，将生成自签名证书（仅用于测试）"
    echo "生产环境请使用正式 CA 签发证书"
    
    mkdir -p deploy/nginx/ssl
    openssl req -x509 -newkey rsa:4096 -keyout deploy/nginx/ssl/key.pem \
        -out deploy/nginx/ssl/cert.pem -days 365 -nodes \
        -subj "/C=CN/ST=Shanghai/L=Shanghai/O=AML-Agent/OU=IT/CN=aml-agent.local"
    echo "自签名证书已生成"
fi

# 创建必要目录
mkdir -p data/reports logs exports

echo ""
echo "启动 Docker Compose..."
echo ""

# 启动服务
docker-compose up -d

echo ""
echo "======================================"
echo "服务启动完成！"
echo "======================================"
echo ""
echo "访问地址:"
echo "  API: https://localhost/api/docs"
echo "  Web: https://localhost"
echo ""
echo "查看日志:"
echo "  docker-compose logs -f"
echo ""
echo "停止服务:"
echo "  docker-compose down"