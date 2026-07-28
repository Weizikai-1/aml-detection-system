# AML-Agent 反洗钱分析系统 部署指南

本指南基于项目实际的 `Dockerfile`、`docker-compose.yml`、`.env.example`、`requirements.txt`、`requirements-production.txt`、`deploy/init-db.sql` 与 `config.py` 编写，涵盖 Docker Compose 一键部署与手动部署两种方式。

---

## 1. 部署架构概述

系统采用容器化微服务架构，由以下四个核心服务组成（定义于 `docker-compose.yml`）：

```
┌──────────────────────────────────────────────────────────────┐
│                     aml-network (bridge)                     │
│                                                              │
│  ┌────────────┐   ┌────────────┐   ┌────────────────────┐   │
│  │  postgres  │   │   redis    │   │   app (FastAPI)    │   │
│  │  15-alpine │   │ 7-alpine   │   │ :8000 API          │   │
│  │  :5432     │   │ :6379      │   │ :8501 Streamlit    │   │
│  └────────────┘   └────────────┘   └────────────────────┘   │
│                         ▲                      ▲             │
│                         │                      │             │
│                         └────────┬─────────────┘            │
│                                  │                          │
│                          ┌────────────────┐                 │
│                          │  celery worker │                 │
│                          │ --concurrency=2│                 │
│                          └────────────────┘                 │
└──────────────────────────────────────────────────────────────┘
```

### 组件说明

| 组件 | 镜像 / 来源 | 端口 | 职责 |
|------|------------|------|------|
| **app** | 本地构建 `Dockerfile` | 8000 / 8501 | FastAPI + Uvicorn 提供 API 服务，Streamlit 提供 Web 界面 |
| **worker** | 本地构建 `Dockerfile` | - | Celery 异步任务处理（分析任务、报告生成等） |
| **postgres** | `postgres:15-alpine` | 5432 | 持久化账户画像、分析历史、告警历史、用户、审计日志 |
| **redis** | `redis:7-alpine` | 6379 | Celery 消息队列与缓存（appendonly 持久化） |

### 数据持久化

- `postgres_data`：PostgreSQL 数据卷
- `redis_data`：Redis AOF 持久化数据卷
- `./data` → `/app/data`：账户画像、历史、缓存、告警等 JSON 文件（双写机制）
- `./reports` → `/app/reports`：生成的 STR 报告
- `./logs` → `/app/logs`：应用与告警日志

---

## 2. 环境要求

### 软件版本

| 依赖 | 最低版本 | 说明 |
|------|---------|------|
| Python | 3.10 | Dockerfile 基础镜像 `python:3.10-slim` |
| Docker | 20.10+ | 需支持 Compose v3.8 |
| Docker Compose | v2+ 或 v1.29+ | 编排定义使用 `version: '3.8'` |
| PostgreSQL | 15 | 容器镜像 `postgres:15-alpine` |
| Redis | 7 | 容器镜像 `redis:7-alpine` |

### 系统资源建议

| 资源 | 最低配置 | 推荐配置（生产） |
|------|---------|-----------------|
| CPU | 2 核 | 4 核+ |
| 内存 | 4 GB | 8 GB+ |
| 磁盘 | 20 GB | 50 GB+ SSD（含数据卷与日志轮转） |

资源依据：
- Celery worker 默认 `--concurrency=2`（`docker-compose.yml` 第 106 行）
- PostgreSQL 连接池 `pool_size=5, max_overflow=10`（`api/database.py`）
- API 日志轮转 `maxBytes=100MB × backupCount=10` ≈ 1GB（`api/main.py`）

### 网络

- 应用对外暴露端口：`8000`（API）、`8501`（Streamlit Web 界面）
- PostgreSQL、Redis 仅在 `aml-network` 内部通信，不对外暴露
- 监控指标端口：`9090`（环境变量 `PROMETHEUS_PORT`，需通过 `/metrics` 端点抓取）

---

## 3. 快速部署（Docker Compose 一键部署）

### 3.1 准备配置

```bash
# 进入项目根目录
cd c:\trae\反洗钱

# 复制环境变量模板
cp .env.example .env
```

编辑 `.env` 文件，**必须**填写以下变量（无默认值，未设置将导致启动失败）：

```ini
# DeepSeek LLM API
DEEPSEEK_API_KEY=sk-your-real-api-key

# 数据库密码
POSTGRES_PASSWORD=your_strong_db_password

# Redis 密码
REDIS_PASSWORD=your_strong_redis_password

# JWT 密钥（生产环境必须固定，否则每次重启旧 token 失效）
JWT_SECRET_KEY=your_jwt_secret_at_least_32_chars

# 加密密钥（用于加密 API 密钥等敏感数据）
ENCRYPT_KEY=your_encrypt_key

# 应用环境
APP_ENV=production
LOG_LEVEL=INFO
```

生成强随机密钥的命令：

```bash
# 生成 JWT_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"

# 生成 ENCRYPT_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 3.2 一键启动

```bash
# 构建镜像并启动所有服务（后台运行）
docker compose up -d --build

# 查看启动状态
docker compose ps

# 等待健康检查通过（约 60 秒 start_period）
docker compose logs -f app
```

### 3.3 验证部署

```bash
# 健康检查
curl http://localhost:8000/health

# 预期返回
# {"status":"healthy","timestamp":"...","version":"1.0.0","database":{"mode":"postgres","connected":true,"info":"PostgreSQL ..."}}

# Prometheus 指标
curl http://localhost:8000/metrics

# API 根路径
curl http://localhost:8000/
```

### 3.4 停止与清理

```bash
# 停止所有服务（保留数据卷）
docker compose down

# 停止并删除数据卷（⚠️ 会丢失所有数据）
docker compose down -v
```

---

## 4. 手动部署（不使用 Docker）

适用于无法使用 Docker 或需要自定义部署的场景。

### 4.1 安装系统依赖

#### Ubuntu / Debian

```bash
sudo apt-get update
sudo apt-get install -y python3.10 python3.10-venv python3-pip \
    libpq-dev gcc postgresql-client redis-server
```

#### CentOS / RHEL

```bash
sudo yum install -y python3.10 python3-pip gcc postgresql-devel redis
```

### 4.2 安装 PostgreSQL 与 Redis

```bash
# 安装 PostgreSQL 15
sudo apt-get install -y postgresql-15

# 启动服务
sudo systemctl enable --now postgresql
sudo systemctl enable --now redis-server
```

### 4.3 创建数据库与用户

```bash
sudo -u postgres psql <<'EOF'
CREATE USER aml_user WITH PASSWORD 'your_strong_db_password';
CREATE DATABASE aml_db OWNER aml_user ENCODING 'UTF8' LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8';
GRANT ALL PRIVILEGES ON DATABASE aml_db TO aml_user;
EOF
```

### 4.4 配置 Redis 密码

编辑 `/etc/redis/redis.conf`：

```conf
requirepass your_strong_redis_password
appendonly yes
```

重启 Redis：

```bash
sudo systemctl restart redis-server
```

### 4.5 创建 Python 虚拟环境并安装依赖

```bash
cd c:\trae\反洗钱

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows PowerShell

# 升级 pip
pip install --upgrade pip

# 安装核心依赖
pip install -r requirements.txt

# 安装生产环境额外依赖
pip install -r requirements-production.txt
```

依赖说明：
- `requirements.txt`：核心框架（langgraph、langchain、pandas、torch、streamlit 等）
- `requirements-production.txt`：生产组件（fastapi、uvicorn、celery、redis、psycopg2-binary、sqlalchemy、alembic、prometheus-client、gunicorn 等）

### 4.6 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填写所有必填项（见 3.1 节）

# 显式启用 PostgreSQL 模式（取消注释）
# DATABASE_URL=postgresql://aml_user:your_password@localhost:5432/aml_db
# REDIS_URL=redis://:your_password@localhost:6379/0
```

### 4.7 创建数据目录

```bash
# Dockerfile 第 52-58 行定义的目录
mkdir -p data/history data/profiles data/cache data/alerts \
         data/audit data/feedback data/rule_configs \
         data/ground_truth data/evaluations \
         reports logs exports
```

### 4.8 初始化数据库表

```bash
# 方式一：使用项目自带初始化脚本
psql -U aml_user -d aml_db -f deploy/init-db.sql

# 方式二：应用启动时自动创建（api/main.py startup_event 调用 create_tables）
# 首次启动应用即可
```

### 4.9 启动服务

建议使用 `gunicorn` + `uvicorn` worker 模式启动 API（生产推荐）：

```bash
# 启动 API 服务（4 worker，生产推荐）
gunicorn api.main:app \
    -w 4 \
    -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:8000 \
    --timeout 120 \
    --access-logfile logs/gunicorn-access.log \
    --error-logfile logs/gunicorn-error.log
```

或使用 `uvicorn` 直接启动（开发/测试）：

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

启动 Celery worker（另开终端）：

```bash
celery -A api.celery_app worker --loglevel=info --concurrency=2
```

启动 Streamlit Web 界面（可选，另开终端）：

```bash
streamlit run app.py --server.port 8501
```

### 4.10 配置 systemd 服务（生产建议）

`/etc/systemd/system/aml-api.service`：

```ini
[Unit]
Description=AML-Agent API Service
After=network.target postgresql.service redis-server.service

[Service]
Type=exec
User=aml
WorkingDirectory=/opt/aml-agent
EnvironmentFile=/opt/aml-agent/.env
ExecStart=/opt/aml-agent/venv/bin/gunicorn api.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 --timeout 120
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/aml-worker.service`：

```ini
[Unit]
Description=AML-Agent Celery Worker
After=network.target postgresql.service redis-server.service

[Service]
Type=exec
User=aml
WorkingDirectory=/opt/aml-agent
EnvironmentFile=/opt/aml-agent/.env
ExecStart=/opt/aml-agent/venv/bin/celery -A api.celery_app worker --loglevel=info --concurrency=2
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now aml-api aml-worker
```

---

## 5. 环境变量配置说明

完整变量列表（来源于 `.env.example` 与 `config.py`）：

### 5.1 LLM 配置

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `DEEPSEEK_API_KEY` | ✅ | - | DeepSeek API Key，申请地址 https://platform.deepseek.com/ |
| `DEEPSEEK_BASE_URL` | ❌ | `https://api.deepseek.com/v1` | DeepSeek API 基础 URL |
| `DEEPSEEK_MODEL` | ❌ | `deepseek-chat` | 使用的模型名称 |

> 占位符值（如 "在这里填入你的DeepSeek API Key"）会被视为未配置，见 `config.py` 第 26-31 行 `_PLACEHOLDER_KEYS`。

### 5.2 认证与加密配置

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `JWT_SECRET_KEY` | ✅ 生产 | 空（每次启动随机生成） | JWT 签名密钥，生产环境必须固定 |
| `JWT_ALGORITHM` | ❌ | `HS256` | JWT 签名算法 |
| `JWT_EXPIRATION_HOURS` | ❌ | `24` | Token 过期时间（小时） |
| `ENCRYPT_KEY` | ✅ 生产 | 空 | 用于加密 API 密钥等敏感数据 |

### 5.3 数据库配置

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `DATABASE_URL` | ❌ | 空（使用 JSON 模式） | PostgreSQL 连接串，格式 `postgresql://user:pass@host:5432/db` |
| `POSTGRES_DB` | ❌ | `aml_db` | 数据库名（Docker 初始化用） |
| `POSTGRES_USER` | ❌ | `aml_user` | 数据库用户（Docker 初始化用） |
| `POSTGRES_PASSWORD` | ✅ Docker | 空 | 数据库密码 |

> 不配置 `DATABASE_URL` 时，系统自动降级为 JSON 文件模式（`api/database.py` 第 65-68 行）。

### 5.4 Redis 配置

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `REDIS_URL` | ❌ | 空（不启用 Celery） | Redis 连接串，格式 `redis://:pass@host:6379/0` |
| `REDIS_PASSWORD` | ✅ Docker | 空 | Redis 密码 |

### 5.5 应用配置

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `APP_ENV` | ❌ | `development` | 环境标识，`production` 时关闭 `/docs` 与 `/redoc`，异常不泄露详情 |
| `LOG_LEVEL` | ❌ | `INFO` | 日志级别 |
| `CORS_ORIGINS` | ❌ | `http://localhost:8501,http://localhost:8000` | CORS 允许源（逗号分隔） |
| `PROMETHEUS_PORT` | ❌ | `9090` | Prometheus 监控端口标识 |

### 5.6 开发环境专用

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `DEV_ADMIN_PASSWORD` | ❌ | 空 | 仅开发模式，管理员密码 |
| `DEV_ANALYST_PASSWORD` | ❌ | 空 | 仅开发模式，分析师密码 |

---

## 6. 数据库初始化

### 6.1 自动初始化（Docker Compose）

Docker Compose 首次启动 PostgreSQL 时，会自动执行 `deploy/init-db.sql`（挂载至 `/docker-entrypoint-initdb.d/01-init.sql`，见 `docker-compose.yml` 第 22 行）。

### 6.2 手动初始化

```bash
psql -U aml_user -d aml_db -f deploy/init-db.sql
```

### 6.3 初始化内容

`deploy/init-db.sql` 创建以下扩展与表：

**扩展：**
- `uuid-ossp`：UUID 生成
- `pgcrypto`：加密支持

**数据表：**

| 表名 | 主键 | 说明 |
|------|------|------|
| `accounts` | `account_id VARCHAR(50)` | 账户风险画像表 |
| `analysis_history` | `execution_id VARCHAR(20)` | 分析历史记录表（含 `value_metrics`、`config_snapshot` JSONB） |
| `evaluation_results` | `eval_id VARCHAR(20)` | 评估结果表（外键关联 `analysis_history`） |
| `alert_history` | `alert_id VARCHAR(36)` | 告警历史表（UUID 默认值） |
| `users` | `user_id VARCHAR(36)` | 用户认证表 |
| `audit_logs` | `log_id SERIAL` | 审计日志表 |

**索引：**
- `idx_history_timestamp`、`idx_history_seq`（分析历史）
- `idx_eval_execution`（评估结果）
- `idx_alert_triggered`、`idx_alert_rule`、`idx_alert_seq`（告警）
- `idx_users_username`（用户）
- `idx_audit_timestamp`、`idx_audit_user`、`idx_audit_action`（审计）

**触发器：**
- `trigger_accounts_updated`：`accounts` 表更新时自动刷新 `updated_at`

**初始数据：**
- 默认管理员账号 `admin`（密码 `admin123` 的 bcrypt hash，**生产环境请立即修改**）

### 6.4 应用层自动建表

即便未执行 `init-db.sql`，应用启动时 `api/main.py` 的 `startup_event` 会调用 `create_tables()`（基于 SQLAlchemy 模型 `api/models.py`）自动创建表结构。

---

## 7. 数据迁移（JSON 到 PostgreSQL）

系统采用**双写机制**实现平滑迁移（`api/dual_write.py`），核心原则：数据零丢失、可降级、可追溯。

### 7.1 双写机制说明

当配置 `DATABASE_URL` 后，应用启动时（`api/main.py` 第 172-175 行）初始化三个双写适配器：

| 适配器 | JSON 来源 | PostgreSQL 目标表 |
|--------|----------|-------------------|
| `HistoryDualWrite` | `HistoryManager` | `analysis_history` |
| `AccountDualWrite` | `AccountProfileManager` | `accounts` |
| `AlertDualWrite` | `AlertHistory` | `alert_history` |

**写入策略：** 先写 JSON（保证可靠性），再写 PostgreSQL（错误隔离，失败不影响 JSON）。

**读取策略：** 优先从 PostgreSQL 读取，失败时降级到 JSON。

### 7.2 迁移步骤

1. **确保 JSON 数据完整**：在原 JSON 模式下运行一段时间，确保 `data/` 目录有完整数据。

2. **配置 PostgreSQL**：在 `.env` 中设置 `DATABASE_URL`。

3. **初始化数据库表**：
   ```bash
   psql -U aml_user -d aml_db -f deploy/init-db.sql
   ```

4. **启动应用**：双写机制自动将新数据同步到 PostgreSQL。

5. **历史数据回填**（如需迁移存量 JSON 数据）：

   ```python
   # 迁移脚本示例：将 JSON 历史记录导入 PostgreSQL
   from api.database import init_db, session_scope
   from api.models import AnalysisHistory
   from tools.history_manager import HistoryManager
   from datetime import datetime
   import os

   init_db(os.getenv("DATABASE_URL"))

   hm = HistoryManager()
   runs = hm.list_runs(limit=10000)

   with session_scope() as session:
       if session is None:
           print("PostgreSQL 未就绪")
           exit(1)
       for run in runs:
           existing = session.query(AnalysisHistory).filter_by(
               execution_id=run["execution_id"]
           ).first()
           if existing:
               continue
           record = AnalysisHistory(
               execution_id=run["execution_id"],
               timestamp=datetime.fromisoformat(run["timestamp"]) if run.get("timestamp") else datetime.now(),
               transactions_count=run.get("transactions_count", 0),
               rule_hit_count=run.get("rule_hit_count", 0),
               str_reports_count=run.get("str_reports_count", 0),
               compliance_score=run.get("compliance_score", 0),
               total_processing_time_sec=run.get("total_processing_time_sec", 0),
               value_metrics=run.get("value_metrics", {}),
               config_snapshot=run.get("config_snapshot", {}),
               _seq=run.get("_seq", 0),
           )
           session.add(record)
   print("历史数据迁移完成")
   ```

6. **验证一致性**：对比 JSON 与 PostgreSQL 中的记录数。

7. **切换到纯 PostgreSQL 模式**：双写机制在过渡期结束后可无缝切换（修改 `api/dual_write.py` 的 `_should_write_postgres` 逻辑或移除 JSON 写入）。

### 7.3 降级保护

PostgreSQL 连接失败时，`api/database.py` 第 99-104 行会自动降级为 JSON 模式：

```python
except Exception as e:
    _db_mode = "json"
    _engine = None
    _SessionFactory = None
    logger.warning(f"[数据库] PostgreSQL 连接失败，降级为 JSON 模式: {e}")
    return False
```

---

## 8. 健康检查与验证

### 8.1 健康检查端点

应用提供 `GET /health` 端点（`api/main.py` 第 190-205 行）：

```bash
curl http://localhost:8000/health
```

返回示例：

```json
{
  "status": "healthy",
  "timestamp": "2026-07-27T10:00:00.000000",
  "version": "1.0.0",
  "database": {
    "mode": "postgres",
    "connected": true,
    "info": "PostgreSQL 15.x ..."
  }
}
```

`database` 字段含义：
- `mode: "postgres"`：使用 PostgreSQL
- `mode: "json"`：使用 JSON 文件模式
- `connected: false`：PostgreSQL 连接异常

### 8.2 Docker 健康检查

`Dockerfile` 第 77-78 行定义健康检查：

```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5)" || exit 1
```

`docker-compose.yml` 为 `postgres` 与 `redis` 也配置了健康检查（`pg_isready` 与 `redis-cli ping`），`app` 服务 `depends_on` 使用 `condition: service_healthy` 确保依赖就绪后再启动。

### 8.3 完整部署验证清单

```bash
# 1. 容器状态
docker compose ps
# 所有服务应显示 "healthy"

# 2. API 健康
curl -s http://localhost:8000/health | python -m json.tool

# 3. Prometheus 指标
curl -s http://localhost:8000/metrics | head -20

# 4. 数据库连接
docker compose exec postgres psql -U aml_user -d aml_db -c "SELECT count(*) FROM users;"

# 5. Redis 连接
docker compose exec redis redis-cli -a your_redis_password ping
# 预期：PONG

# 6. Celery worker 状态
docker compose logs worker | tail -20
# 应显示 "celery@... ready"

# 7. 审计日志已记录启动事件
docker compose exec app ls -la /app/data/audit/
# 应存在 audit_YYYY-MM-DD.jsonl 文件
```

---

## 9. 常见部署问题排查

### 9.1 容器启动失败

**现象：** `docker compose up` 后 `app` 容器立即退出。

**排查：**

```bash
# 查看应用日志
docker compose logs app

# 常见原因：
# 1. JWT_SECRET_KEY 或 ENCRYPT_KEY 未设置
# 2. DEEPSEEK_API_KEY 为占位符
# 3. POSTGRES_PASSWORD 或 REDIS_PASSWORD 未设置
```

**解决：** 检查 `.env` 文件，确保所有必填变量已配置真实值。

### 9.2 PostgreSQL 连接失败

**现象：** 健康检查返回 `database.connected: false`。

**排查：**

```bash
# 检查 postgres 容器健康状态
docker compose ps postgres

# 检查 postgres 日志
docker compose logs postgres

# 手动测试连接
docker compose exec postgres pg_isready -U aml_user -d aml_db
```

**常见原因：**
- `POSTGRES_PASSWORD` 在 `.env` 与 `docker-compose.yml` 中不一致
- 数据卷损坏：尝试 `docker compose down -v` 后重新启动（⚠️ 会丢数据）
- `DATABASE_URL` 格式错误：应为 `postgresql://user:pass@postgres:5432/db`（容器内主机名为 `postgres`）

### 9.3 Redis 连接失败

**现象：** Celery worker 启动失败，日志显示 `ConnectionError`。

**排查：**

```bash
docker compose logs redis
docker compose exec redis redis-cli -a your_redis_password ping
```

**解决：** 确认 `REDIS_PASSWORD` 一致，`REDIS_URL` 格式为 `redis://:password@redis:6379/0`。

### 9.4 健康检查不通过

**现象：** `docker compose ps` 显示 `app` 状态为 `unhealthy`。

**排查：**

```bash
# 手动执行健康检查命令
docker compose exec app python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5)"

# 查看应用启动日志
docker compose logs app --tail 100
```

**常见原因：**
- `start_period=60s` 内应用未启动完成（机器资源不足）
- 端口 8000 被占用：`netstat -tlnp | grep 8000`
- 应用启动时数据库初始化失败

### 9.5 端口冲突

**现象：** 容器启动报 `port is already allocated`。

**解决：**

```bash
# 查看占用端口的进程
# Linux
sudo lsof -i :8000
sudo lsof -i :8501

# 修改 docker-compose.yml 中 ports 映射，例如：
# ports:
#   - "8001:8000"  # 主机 8001 映射容器 8000
```

### 9.6 Celery worker 不处理任务

**现象：** 提交分析任务后一直处于 `pending` 状态。

**排查：**

```bash
# 查看 worker 日志
docker compose logs worker

# 确认 worker 已注册
docker compose exec app celery -A api.celery_app inspect active
```

**常见原因：**
- Redis 连接异常
- worker 容器未启动：`docker compose up -d worker`
- `--concurrency=2` 已满载，等待现有任务完成

### 9.7 内存不足（OOM）

**现象：** 容器被系统 OOM Killer 杀死，日志中出现 `Killed`。

**排查：**

```bash
# 查看容器资源使用
docker stats

# 查看系统日志
dmesg | grep -i oom
```

**解决：**
- 增加 `docker-compose.yml` 中 `deploy.resources.limits`
- 降低 Celery `--concurrency`（默认 2）
- 减小 Gunicorn worker 数量
- 检查 `torch` 模型内存占用（`requirements.txt` 中的 GNN 依赖）

### 9.8 日志文件过大

**现象：** 磁盘空间被日志占满。

**说明：** `api/main.py` 第 41-55 行已配置 `RotatingFileHandler`：
- 单文件最大 `100MB`
- 保留 `10` 个备份
- 日志路径：`logs/api.log`

**排查：**

```bash
docker compose exec app ls -lh /app/logs/
```

**解决：** 如仍过大，调整 `api/main.py` 中的 `maxBytes` 或 `backupCount`。

### 9.9 时区问题

**现象：** 日志时间与实际时间不一致。

**说明：** Dockerfile 第 20-21 行设置 `LANG=C.UTF-8 LC_ALL=C.UTF-8`，容器默认使用 UTC。

**解决：** 在 `docker-compose.yml` 的 `app` 服务环境变量中添加：

```yaml
environment:
  TZ: Asia/Shanghai
```

并安装时区数据（修改 Dockerfile）：

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone
```

### 9.10 镜像构建失败

**现象：** `docker compose build` 报错。

**常见原因：**
- `torch` 下载体积大、网络超时：配置 pip 镜像源
- `psycopg2-binary` 编译失败：Dockerfile 已安装 `libpq-dev` 与 `gcc`，确认未被移除

**解决（国内网络）：** 在 Dockerfile 的 `pip install` 前添加镜像源：

```dockerfile
RUN pip install --no-cache-dir --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir -r requirements-production.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```
