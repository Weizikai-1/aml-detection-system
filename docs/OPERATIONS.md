# AML-Agent 反洗钱分析系统 运维手册

本手册基于项目实际的 `api/monitor.py`、`api/audit_log.py`、`api/log_desensitize.py`、`tools/monitor.py`、`tools/notifier.py`、`tools/alert_rules.py`、`tools/alert_history.py`、`api/main.py`、`api/database.py` 与 `config.py` 编写，涵盖系统日常运维、监控告警、审计日志、日志管理、数据库维护、性能优化、故障排查与安全运维等内容。

---

## 1. 系统架构与组件说明

### 1.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         客户端 / 前端                            │
│              Streamlit Web (8501) / API 调用方                   │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI 应用 (api/main.py)                    │
│  ┌─────────────┐  ┌─────────────┐  ┌────────────────────────┐  │
│  │  限流中间件  │→│  请求日志    │→│  业务路由 (auth/analysis │  │
│  │  (slowapi)  │  │  中间件      │  │  /reports)             │  │
│  └─────────────┘  └─────────────┘  └────────────────────────┘  │
│         │                │                    │                  │
│         ▼                ▼                    ▼                  │
│  ┌───────────┐  ┌───────────────┐  ┌────────────────────┐      │
│  │ Prometheus │  │  审计日志      │  │  日志脱敏           │      │
│  │  /metrics  │  │  audit_log.py │  │  log_desensitize.py│      │
│  └───────────┘  └───────────────┘  └────────────────────┘      │
└──────────────────────────────┬──────────────────────────────────┘
                               │ 异步任务
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│              Celery Worker (worker 容器, concurrency=2)          │
│   分析任务 / 报告生成 / 监控告警评估 (tools/monitor.py)           │
└──────────────────────────────┬──────────────────────────────────┘
                               │
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
   ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
   │  PostgreSQL  │   │    Redis     │   │  DeepSeek LLM    │
   │  (持久化)     │   │  (队列/缓存)  │   │  (外部 API)      │
   └──────────────┘   └──────────────┘   └──────────────────┘
```

### 1.2 核心组件

| 组件 | 文件位置 | 职责 |
|------|---------|------|
| **API 服务** | `api/main.py` | FastAPI 应用入口，提供 REST API、健康检查、Prometheus 指标端点 |
| **Celery Worker** | `api/celery_app`（worker 容器） | 异步执行分析任务、报告生成，`--concurrency=2` |
| **监控指标** | `api/monitor.py` | Prometheus 指标采集（Counter/Histogram/Gauge） |
| **告警引擎** | `tools/monitor.py` | `Monitor` 类，串联规则注册中心、告警历史、通知管理器 |
| **告警规则** | `tools/alert_rules.py` | 内置 15 条默认告警规则，支持启用/禁用、阈值配置 |
| **告警历史** | `tools/alert_history.py` | 告警持久化（JSON 文件 + 索引）、查询、统计、抑制窗口管理 |
| **通知分发** | `tools/notifier.py` | 多渠道通知：控制台、文件、Webhook、邮件 |
| **审计日志** | `api/audit_log.py` | 关键操作记录，JSONL 格式按日切分 |
| **日志脱敏** | `api/log_desensitize.py` | 银行卡、身份证、手机号、邮箱、API 密钥等自动脱敏 |
| **数据库** | `api/database.py` | 双后端模式（JSON / PostgreSQL），自动降级 |
| **双写机制** | `api/dual_write.py` | JSON 与 PostgreSQL 双写，平滑迁移 |

### 1.3 业务戒律（运维相关）

系统遵循 4 条强制戒律（`config.py` 第 147-206 行 `AML_RULES`），运维操作不得违反：

| 戒律 | 名称 | 运维含义 |
|------|------|---------|
| M1 | 必须使用真实数据 | 监控、告警、审计均基于真实运行数据，不编造 |
| M2 | 必须标注可疑理由 | 告警消息必须包含具体原因与上下文 |
| M3 | 风险评分范围 0-100 | 监控指标中的风险分值必须在此范围 |
| M4 | 证据链完整可追溯 | 审计日志、告警历史、跳过/降级操作均需记录 |

---

## 2. 日常运维操作

### 2.1 Docker Compose 部署模式

#### 启动服务

```bash
cd c:\trae\反洗钱

# 启动所有服务（后台）
docker compose up -d

# 仅启动特定服务
docker compose up -d app
docker compose up -d worker
```

#### 停止服务

```bash
# 停止所有服务（保留数据）
docker compose stop

# 停止特定服务
docker compose stop app worker
```

#### 重启服务

```bash
# 重启所有服务
docker compose restart

# 重启特定服务（如代码更新后）
docker compose restart app worker

# 重新构建并重启（代码变更后）
docker compose up -d --build app worker
```

#### 查看状态与日志

```bash
# 查看容器状态
docker compose ps

# 查看实时日志（所有服务）
docker compose logs -f

# 查看特定服务日志
docker compose logs -f app
docker compose logs -f worker
docker compose logs -f postgres
docker compose logs -f redis

# 查看最近 100 行日志
docker compose logs --tail 100 app
```

#### 进入容器

```bash
# 进入应用容器
docker compose exec app bash

# 进入 PostgreSQL
docker compose exec postgres psql -U aml_user -d aml_db

# 进入 Redis
docker compose exec redis redis-cli -a your_redis_password
```

### 2.2 手动部署模式（systemd）

```bash
# 启动
sudo systemctl start aml-api aml-worker

# 停止
sudo systemctl stop aml-api aml-worker

# 重启
sudo systemctl restart aml-api aml-worker

# 查看状态
sudo systemctl status aml-api aml-worker

# 查看日志
journalctl -u aml-api -f --since "10 min ago"
journalctl -u aml-worker -f --since "10 min ago"
```

### 2.3 Streamlit Web 界面

```bash
# 手动启动（开发）
streamlit run app.py --server.port 8501

# 访问地址
# http://localhost:8501
```

---

## 3. 监控指标说明

### 3.1 Prometheus 指标端点

应用在 `api/main.py` 第 210-218 行暴露 `/metrics` 端点：

```bash
curl http://localhost:8000/metrics
```

### 3.2 指标列表

所有指标定义于 `api/monitor.py`：

| 指标名称 | 类型 | 标签 | 说明 |
|---------|------|------|------|
| `api_requests_total` | Counter | `endpoint`, `method`, `status` | API 请求总数 |
| `api_request_duration_seconds` | Histogram | `endpoint`, `method` | API 请求耗时（秒） |
| `analysis_tasks_total` | Counter | `status` | 分析任务总数（status: pending/running/completed/failed） |
| `active_tasks` | Gauge | - | 当前活跃任务数 |
| `rule_hits_total` | Counter | `rule_name` | 规则命中总数 |
| `reports_generated_total` | Counter | `risk_level` | 生成报告总数（按风险等级） |

### 3.3 指标采集函数

`api/monitor.py` 提供以下采集函数，业务代码调用它们记录指标：

| 函数 | 作用 | 调用位置 |
|------|------|---------|
| `record_request(endpoint, method, status, duration)` | 记录 API 请求 | `api/main.py` 请求日志中间件（第 122-123 行） |
| `record_analysis_task(status)` | 记录分析任务状态 | 分析任务执行流程 |
| `set_active_tasks(count)` | 设置活跃任务数 | 任务调度器 |
| `record_rule_hit(rule_name)` | 记录规则命中 | 规则引擎 |
| `record_report_generated(risk_level)` | 记录报告生成 | 报告生成模块 |

### 3.4 Prometheus 抓取配置示例

`prometheus.yml`：

```yaml
scrape_configs:
  - job_name: 'aml-agent'
    scrape_interval: 15s
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
```

### 3.5 关键 PromQL 查询示例

```promql
# API QPS（每秒请求数）
rate(api_requests_total[1m])

# API 平均延迟（秒）
rate(api_request_duration_seconds_sum[5m]) / rate(api_request_duration_seconds_count[5m])

# API P99 延迟
histogram_quantile(0.99, rate(api_request_duration_seconds_bucket[5m]))

# 错误率（5xx 占比）
sum(rate(api_requests_total{status=~"5.."}[5m])) / sum(rate(api_requests_total[5m]))

# 活跃任务数
active_tasks

# 各规则命中次数
topk(10, sum by (rule_name) (increase(rule_hits_total[1h])))

# 各风险等级报告数
sum by (risk_level) (reports_generated_total)
```

---

## 4. 告警系统配置

### 4.1 告警系统架构

告警系统由三个组件串联（`tools/monitor.py` 第 39-54 行 `Monitor` 类）：

```
工作流状态 ──→ Monitor.trigger() ──→ AlertRuleRegistry（规则匹配）
                                       │
                                       ▼
                                  抑制窗口检查
                                  （emergency 不抑制）
                                       │
                                       ▼
                                  AlertHistory（持久化）
                                       │
                                       ▼
                                  NotificationManager（分发）
                                       │
                          ┌────────────┼────────────┐
                          ▼            ▼            ▼
                    ConsoleNotifier FileNotifier WebhookNotifier/EmailNotifier
```

### 4.2 告警严重级别

定义于 `tools/alert_rules.py` 第 20-25 行 `AlertSeverity`：

| 级别 | 值 | 说明 | 抑制行为 |
|------|-----|------|---------|
| `INFO` | `info` | 通知性 | 默认 300 秒抑制窗口 |
| `WARNING` | `warning` | 警告 | 默认 300 秒抑制窗口 |
| `CRITICAL` | `critical` | 严重 | 走 `notify_critical` 多渠道兜底 |
| `EMERGENCY` | `emergency` | 紧急 | **不抑制**，多渠道兜底 |

### 4.3 内置告警规则

定义于 `tools/alert_rules.py` 第 68-204 行 `DEFAULT_ALERT_RULES`，共 15 条：

#### 风险检测告警（RISK_DETECTION）

| 规则 ID | 名称 | 严重级别 | 阈值 | 抑制窗口 |
|---------|------|---------|------|---------|
| `risk_high_count_threshold` | 高风险交易超阈值 | CRITICAL | 10 笔 | 300s |
| `risk_critical_transaction` | 发现极严重可疑交易 | EMERGENCY | 85 分 | 0s（不抑制） |
| `risk_shell_company_detected` | 空壳公司识别命中 | WARNING | - | 300s |
| `risk_repeat_offender` | 累犯账户高活跃 | WARNING | - | 300s |

#### 系统健康告警（SYSTEM_HEALTH）

| 规则 ID | 名称 | 严重级别 | 阈值 |
|---------|------|---------|------|
| `health_node_failure` | Agent 节点执行失败 | WARNING | - |
| `health_data_quality_low` | 数据质量低 | WARNING | 0.6 |
| `health_cache_miss_rate_high` | 缓存命中率过低 | INFO | 0.3 |

#### 合规告警（COMPLIANCE）

| 规则 ID | 名称 | 严重级别 | 阈值 |
|---------|------|---------|------|
| `compliance_report_rejected` | 报告被驳回 | WARNING | - |
| `compliance_human_review_high` | 需人工审核报告过多 | INFO | 0.3（30%） |

#### 性能告警（PERFORMANCE）

| 规则 ID | 名称 | 严重级别 | 阈值 |
|---------|------|---------|------|
| `perf_analysis_too_slow` | 分析耗时过长 | WARNING | 60 秒 |
| `perf_node_too_slow` | 单节点耗时过长 | INFO | 30 秒 |

#### 评估指标告警（EVALUATION）

| 规则 ID | 名称 | 严重级别 | 阈值 |
|---------|------|---------|------|
| `eval_precision_drop` | 精度下降 | WARNING | 0.10（10%） |
| `eval_recall_drop` | 召回率下降 | CRITICAL | 0.10（10%） |
| `eval_f1_drop` | F1 下降 | WARNING | 0.05（5%） |

#### 工作流告警（WORKFLOW）

| 规则 ID | 名称 | 严重级别 | 抑制窗口 |
|---------|------|---------|---------|
| `workflow_interrupted` | 分析流程被中断 | WARNING | 0s（不抑制） |
| `workflow_no_suspicious` | 未发现可疑交易 | INFO | 300s |

### 4.4 规则管理

`tools/alert_rules.py` 第 207-248 行 `AlertRuleRegistry` 提供规则管理 API：

```python
from tools.alert_rules import default_registry

# 列出所有规则
default_registry.list_all()

# 列出已启用规则
default_registry.list_enabled()

# 禁用某条规则
default_registry.disable("health_cache_miss_rate_high")

# 启用某条规则
default_registry.enable("health_cache_miss_rate_high")

# 按类别查询
from tools.alert_rules import AlertCategory
default_registry.by_category(AlertCategory.PERFORMANCE)
```

### 4.5 通知渠道配置

定义于 `tools/notifier.py`，提供 4 种通知器：

#### ConsoleNotifier（控制台，默认启用）

```python
from tools.notifier import ConsoleNotifier

notifier = ConsoleNotifier(
    enabled=True,
    min_severity="info"  # info/warning/critical/emergency
)
```

#### FileNotifier（文件，默认启用）

```python
from tools.notifier import FileNotifier

notifier = FileNotifier(
    log_path="logs/alerts.log",  # 默认 {LOGS_DIR}/alerts.log
    enabled=True
)
```

#### WebhookNotifier（Webhook，默认禁用）

```python
from tools.notifier import WebhookNotifier

notifier = WebhookNotifier(
    url="https://your-webhook.example.com/alert",
    enabled=True,
    timeout=10
)
# 关键告警（critical/emergency）自动重试 2 次（_CRITICAL_RETRIES=2）
```

#### EmailNotifier（邮件，默认禁用）

```python
from tools.notifier import EmailNotifier

notifier = EmailNotifier(
    smtp_host="smtp.example.com",
    smtp_port=587,
    username="alert@example.com",
    password="smtp_password",
    from_addr="alert@example.com",
    to_addrs=["admin@example.com", "ops@example.com"],
    enabled=True
)
```

#### 自定义通知管理器

```python
from tools.notifier import NotificationManager, ConsoleNotifier, FileNotifier, WebhookNotifier, EmailNotifier

mgr = NotificationManager()
mgr.add_notifier(ConsoleNotifier(enabled=True))
mgr.add_notifier(FileNotifier(enabled=True))
mgr.add_notifier(WebhookNotifier(url="https://hook.example.com", enabled=True))
mgr.add_notifier(EmailNotifier(smtp_host="smtp.example.com", to_addrs=["ops@example.com"], enabled=True))
```

**关键告警兜底机制**（`tools/notifier.py` 第 256-276 行 `notify_critical`）：
- `critical` / `emergency` 级别告警通过所有已配置渠道并发发送
- 若全部渠道失败，强制使用 `ConsoleNotifier` 兜底，确保关键事件不丢失

### 4.6 告警历史管理

定义于 `tools/alert_history.py`：

- **存储位置**：`data/alerts/`（`config.py` 的 `DATA_DIR/alerts`）
- **索引文件**：`data/alerts/index.json`
- **单条告警文件**：`data/alerts/{alert_id}.json`

查询与统计：

```python
from tools.alert_history import AlertHistory

history = AlertHistory()

# 列出告警（支持筛选）
history.list_alerts(severity="critical", limit=50)
history.list_alerts(category="risk_detection", limit=100)
history.list_alerts(rule_id="risk_critical_transaction", limit=20)

# 获取单条告警详情
history.get_alert("alert_20260727_100000_abc123")

# 统计信息
history.stats()
# 返回: {"total": N, "by_severity": {...}, "by_category": {...}, "by_rule": {...}}

# 清空所有告警（谨慎使用）
history.clear()
```

### 4.7 告警触发流程

`tools/monitor.py` 的 `Monitor` 类提供两种触发方式：

1. **实时工作流监控**（`check_workflow_state`，第 135-346 行）：分析任务完成后，自动检查 13 类告警条件，包括：
   - 高风险交易超阈值
   - 极严重可疑交易（≥85 分，每笔触发，不抑制）
   - 空壳公司识别
   - 累犯账户高活跃
   - 数据质量低
   - 分析耗时过长 / 单节点耗时过长
   - 合规报告驳回 / 人工审核比例过高
   - 节点执行失败
   - 缓存命中率过低
   - 工作流被中断
   - 未发现可疑交易

2. **离线评估回归**（`check_evaluation_regression`，第 368-406 行）：对比基线与当前评估结果，触发精度/召回率/F1 下降告警。

获取默认监控器单例：

```python
from tools.monitor import get_monitor
monitor = get_monitor()
```

---

## 5. 审计日志查询与管理

### 5.1 审计日志模块

定义于 `api/audit_log.py`，记录所有关键操作，符合业务戒律 M4（审计可追溯）。

### 5.2 日志存储

- **存储位置**：`data/audit/`（`api/audit_log.py` 第 33 行 `AUDIT_LOG_DIR`）
- **文件格式**：JSONL（每行一条 JSON），按日切分
- **文件命名**：`audit_YYYY-MM-DD.jsonl`
- **自动创建**：模块导入时自动创建目录（第 34 行 `os.makedirs`）

### 5.3 操作类型

定义于 `api/audit_log.py` 第 37-44 行 `OperationType`：

| 类型 | 值 | 说明 |
|------|-----|------|
| `AUTH` | `AUTH` | 认证相关（登录、登出、token 验证） |
| `ANALYSIS` | `ANALYSIS` | 分析相关（提交、执行、完成） |
| `REPORT` | `REPORT` | 报告相关（生成、导出、删除） |
| `CONFIG` | `CONFIG` | 配置相关（修改、回滚） |
| `ADMIN` | `ADMIN` | 管理相关（用户管理、权限变更） |
| `FEEDBACK` | `FEEDBACK` | 反馈相关（误报标记、漏报标记） |
| `SYSTEM` | `SYSTEM` | 系统事件（如服务启动） |

### 5.4 操作结果

定义于 `api/audit_log.py` 第 47-50 行 `OperationResult`：

| 结果 | 值 |
|------|-----|
| `SUCCESS` | `SUCCESS` |
| `FAILED` | `FAILED` |
| `PENDING` | `PENDING` |

### 5.5 审计日志条目字段

每条审计日志（`AuditEntry`，第 53-94 行）包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `entry_id` | UUID | 唯一标识 |
| `timestamp` | ISO 字符串 | 操作时间 |
| `operation_type` | string | 操作类型 |
| `action` | string | 操作描述 |
| `user_id` | string | 操作人 ID |
| `username` | string | 操作人用户名 |
| `ip_address` | string | IP 地址 |
| `request_id` | string | 请求 ID（用于追踪） |
| `details` | dict | 详细信息 |
| `result` | string | 操作结果 |
| `error_message` | string | 错误信息（失败时） |

### 5.6 查询审计日志

使用全局 `audit_logger` 实例（`api/audit_log.py` 第 322 行）：

```python
from api.audit_log import audit_logger, OperationType, OperationResult

# 查询登录失败记录
result = audit_logger.query(
    operation_type=OperationType.AUTH,
    result=OperationResult.FAILED,
    limit=50,
)

# 查询某用户的所有操作
result = audit_logger.query(
    username="admin",
    start_time="2026-07-01T00:00:00",
    end_time="2026-07-31T23:59:59",
    limit=100,
)

# 查询分析相关操作
result = audit_logger.query(
    operation_type=OperationType.ANALYSIS,
    limit=100,
    offset=0,
)

# 返回结构：{"total": 总数, "entries": [日志列表]}

# 获取单条日志详情
entry = audit_logger.get_entry("entry-uuid-here")
```

### 5.7 记录审计日志

```python
from api.audit_log import audit_logger, OperationType

# 记录成功操作
audit_logger.log_success(
    operation_type=OperationType.ANALYSIS,
    action="提交分析任务",
    user_id="user-123",
    username="analyst01",
    ip_address="192.168.1.100",
    request_id="req-456",
    details={"transaction_count": 1000, "execution_id": "exec-789"},
)

# 记录失败操作
audit_logger.log_failed(
    operation_type=OperationType.REPORT,
    action="导出报告",
    error_message="磁盘空间不足",
    user_id="user-123",
    username="analyst01",
    ip_address="192.168.1.100",
)
```

### 5.8 命令行查看审计日志

```bash
# 查看今日审计日志
cat data/audit/audit_$(date +%Y-%m-%d).jsonl | python -m json.tool

# 查看特定日期
cat data/audit/audit_2026-07-27.jsonl | python -m json.tool

# 统计今日各操作类型数量
cat data/audit/audit_$(date +%Y-%m-%d).jsonl | \
    python -c "import sys,json,collections; c=collections.Counter(json.loads(l)['operation_type'] for l in sys.stdin); print(dict(c))"

# Docker 环境
docker compose exec app cat /app/data/audit/audit_$(date +%Y-%m-%d).jsonl
```

---

## 6. 日志管理

### 6.1 日志位置

| 日志类型 | 位置 | 说明 |
|---------|------|------|
| API 应用日志 | `logs/api.log` | FastAPI 请求与错误日志 |
| Gunicorn 日志 | `logs/gunicorn-access.log`、`logs/gunicorn-error.log` | 手动部署时的访问与错误日志 |
| 告警日志 | `logs/alerts.log` | `FileNotifier` 写入的告警记录 |
| 审计日志 | `data/audit/audit_YYYY-MM-DD.jsonl` | 按日切分的审计日志 |
| 告警历史 | `data/alerts/{alert_id}.json`、`data/alerts/index.json` | 告警持久化 |

### 6.2 日志轮转策略

`api/main.py` 第 41-55 行配置 `RotatingFileHandler`：

```python
RotatingFileHandler(
    "logs/api.log",
    encoding="utf-8",
    maxBytes=100 * 1024 * 1024,  # 100MB
    backupCount=10,
)
```

- **单文件最大**：100 MB
- **保留备份数**：10 个
- **总占用上限**：约 1 GB（`api.log` + 10 个轮转文件）
- **轮转文件命名**：`api.log.1`、`api.log.2`、...、`api.log.10`

### 6.3 日志级别

通过环境变量 `LOG_LEVEL` 控制（默认 `INFO`）：

```bash
# 开发环境
LOG_LEVEL=DEBUG

# 生产环境
LOG_LEVEL=INFO

# 仅记录错误（降低磁盘占用）
LOG_LEVEL=ERROR
```

日志格式（`api/main.py` 第 45 行）：
```
%(asctime)s - %(name)s - %(levelname)s - %(message)s
```

### 6.4 日志脱敏机制

定义于 `api/log_desensitize.py`，在日志输出前自动对敏感信息脱敏。

#### 脱敏规则

| 敏感类型 | 正则模式 | 脱敏方式 | 示例 |
|---------|---------|---------|------|
| 银行卡号 | `62\d{14,18}` | 保留前 4 + `****` + 后 4 | `6222021234567890` → `6222****7890` |
| 身份证号 | `\d{17}[\dXx]` 或 `\d{15}` | 保留前 6 + `**********` + 后 4 | `110101199001011234` → `110101**********1234` |
| 手机号 | `1[3-9]\d{9}` | 保留前 3 + `****` + 后 4 | `13812345678` → `138****5678` |
| 邮箱 | `用户名@域名` | 用户名首字符 + `***@` + 域名 | `zhangsan@example.com` → `z***@example.com` |
| API 密钥 | `[A-Za-z0-9]{8}[A-Za-z0-9]{20,}` | 保留前 8 + `***` | `skabcdefg...` → `skabcdefg***` |
| 账户号 | `\d{4}\d{4,12}\d{4}` | 保留前 4 + `****` + 后 4 | `1234567890123456` → `1234****3456` |

#### 字典敏感字段

`desensitize_dict` 函数（第 74-108 行）对以下 key 自动脱敏：

```
card_no, card_number, bank_card, account_no, account_number,
id_card, id_number, identity_card, phone, mobile, tel,
email, mail, username, name, api_key, password, secret,
token, access_token, jwt
```

#### 安装脱敏格式化器

`api/main.py` 第 147 行在启动时自动安装：

```python
from api.log_desensitize import patch_logger
patch_logger("api")
```

手动安装到其他 logger：

```python
from api.log_desensitize import patch_logger
patch_logger("my_module")   # 特定 logger
patch_logger()              # 根 logger
```

### 6.5 日志查看与排查

```bash
# 实时查看 API 日志
docker compose exec app tail -f /app/logs/api.log

# 查看错误日志
docker compose exec app grep -i "error" /app/logs/api.log | tail -50

# 查看告警日志
docker compose exec app cat /app/logs/alerts.log

# 按时间范围过滤（如 10:00-11:00）
docker compose exec app awk '/2026-07-27 10:/{p=1} /2026-07-27 11:/{p=0} p' /app/logs/api.log
```

### 6.6 日志清理

```bash
# 手动清理旧日志（保留最近 7 天）
find logs/ -name "api.log.*" -mtime +7 -delete

# 清理旧审计日志（保留最近 90 天，合规要求）
find data/audit/ -name "audit_*.jsonl" -mtime +90 -delete

# 清理旧告警文件（保留最近 30 天）
find data/alerts/ -name "alert_*.json" -mtime +30 -delete
```

---

## 7. 数据库维护

### 7.1 数据库模式

系统支持双模式（`api/database.py`）：
- **JSON 模式**：默认，无需 PostgreSQL，数据存于 `data/` 目录
- **PostgreSQL 模式**：生产环境，通过 `DATABASE_URL` 启用

连接池配置（`api/database.py` 第 75-86 行）：
- `pool_size=5`：常驻连接数
- `max_overflow=10`：最大溢出连接数
- `pool_timeout=30`：获取连接超时（秒）
- `pool_recycle=3600`：连接回收时间（秒）
- `pool_pre_ping=True`：连接前检查可用性
- `isolation_level="READ_COMMITTED"`：事务隔离级别

### 7.2 备份

#### PostgreSQL 逻辑备份

```bash
# 完整备份（Docker 环境）
docker compose exec postgres pg_dump -U aml_user -d aml_db -F c -f /tmp/aml_backup.dump
docker compose cp postgres:/tmp/aml_backup.dump ./backups/aml_$(date +%Y%m%d_%H%M%S).dump

# 完整备份（手动部署）
pg_dump -U aml_user -d aml_db -F c -f backups/aml_$(date +%Y%m%d_%H%M%S).dump

# 仅备份特定表
pg_dump -U aml_user -d aml_db -t audit_logs -t users -F c -f backups/aml_critical_$(date +%Y%m%d).dump
```

#### JSON 文件备份

```bash
# 备份 data 目录（包含账户画像、历史、告警、审计日志）
tar -czf backups/data_$(date +%Y%m%d_%H%M%S).tar.gz data/

# 备份报告目录
tar -czf backups/reports_$(date +%Y%m%d_%H%M%S).tar.gz reports/
```

#### 自动备份脚本（crontab）

```bash
# 编辑 crontab
crontab -e

# 每日凌晨 2 点备份
0 2 * * * cd /opt/aml-agent && docker compose exec -T postgres pg_dump -U aml_user -d aml_db -F c | gzip > backups/pg_$(date +\%Y\%m\%d).dump.gz
0 2 * * * cd /opt/aml-agent && tar -czf backups/data_$(date +\%Y\%m\%d).tar.gz data/

# 保留最近 30 天备份
30 2 * * * find /opt/aml-agent/backups/ -mtime +30 -delete
```

### 7.3 恢复

#### PostgreSQL 恢复

```bash
# 恢复（Docker 环境）
docker compose cp ./backups/aml_20260727.dump postgres:/tmp/aml_backup.dump
docker compose exec postgres pg_restore -U aml_user -d aml_db -c /tmp/aml_backup.dump

# 恢复（手动部署）
pg_restore -U aml_user -d aml_db -c backups/aml_20260727.dump
```

#### JSON 文件恢复

```bash
# 停止服务
docker compose stop app worker

# 恢复 data 目录
tar -xzf backups/data_20260727.tar.gz

# 重启服务
docker compose start app worker
```

### 7.4 数据清理

#### 审计日志清理

```bash
# 连接 PostgreSQL
docker compose exec postgres psql -U aml_user -d aml_db

# 清理 90 天前的审计日志
DELETE FROM audit_logs WHERE timestamp < NOW() - INTERVAL '90 days';

# 清理 90 天前的告警历史
DELETE FROM alert_history WHERE triggered_at < NOW() - INTERVAL '90 days';

# 清理 180 天前的分析历史（保留评估结果）
DELETE FROM analysis_history WHERE timestamp < NOW() - INTERVAL '180 days' 
  AND execution_id NOT IN (SELECT execution_id FROM evaluation_results);

# VACUUM 回收空间
VACUUM ANALYZE audit_logs;
VACUUM ANALYZE alert_history;
VACUUM ANALYZE analysis_history;
```

#### Redis 清理

```bash
# 进入 Redis
docker compose exec redis redis-cli -a your_redis_password

# 查看内存使用
INFO memory

# 清空当前数据库（谨慎）
FLUSHDB

# 清空所有数据库（极其谨慎）
FLUSHALL
```

### 7.5 数据库健康检查

```sql
-- 表大小
SELECT schemaname, relname, pg_size_pretty(pg_total_relation_size(relid)) AS size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC;

-- 索引大小
SELECT schemaname, relname, indexrelname, pg_size_pretty(pg_total_relation_size(indexrelid)) AS size
FROM pg_catalog.pg_statio_user_indexes
ORDER BY pg_total_relation_size(indexrelid) DESC;

-- 活跃连接数
SELECT count(*) FROM pg_stat_activity WHERE datname = 'aml_db';

-- 长事务
SELECT pid, now() - xact_start AS duration, query, state
FROM pg_stat_activity 
WHERE state IN ('active', 'idle in transaction') 
  AND now() - xact_start > INTERVAL '5 minutes';
```

### 7.6 双写一致性检查

由于系统采用 JSON + PostgreSQL 双写机制（`api/dual_write.py`），建议定期检查一致性：

```python
# 一致性检查脚本
from api.database import init_db, session_scope
from api.models import AnalysisHistory, Account, AlertHistoryRecord
from tools.history_manager import HistoryManager
from tools.alert_history import AlertHistory

init_db("postgresql://aml_user:password@localhost:5432/aml_db")

# 检查分析历史
hm = HistoryManager()
json_runs = hm.list_runs(limit=10000)
with session_scope() as session:
    pg_count = session.query(AnalysisHistory).count()
print(f"历史记录: JSON={len(json_runs)}, PG={pg_count}")

# 检查告警
ah = AlertHistory()
json_alerts = ah.list_alerts(limit=10000)
with session_scope() as session:
    pg_alert_count = session.query(AlertHistoryRecord).count()
print(f"告警记录: JSON={len(json_alerts)}, PG={pg_alert_count}")
```

---

## 8. 性能优化建议

### 8.1 API 服务优化

**Gunicorn worker 配置**（手动部署）：

```bash
# CPU 密集型：worker 数 = CPU 核数 × 2 + 1
gunicorn api.main:app -w 5 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 --timeout 120

# I/O 密集型（LLM 调用为主）：可适当增加 worker
gunicorn api.main:app -w 9 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 --timeout 120 --worker-connections 1000
```

**限流配置**（`api/main.py` 第 32-38 行使用 `slowapi`）：
- 根据业务流量调整限流阈值
- 关键接口（如 `/api/analyze`）可设置更严格限流

### 8.2 Celery Worker 优化

```bash
# 增加 worker 数量（注意内存）
celery -A api.celery_app worker --loglevel=info --concurrency=4

# 使用 prefork 模式（CPU 密集型）
celery -A api.celery_app worker --loglevel=info --concurrency=4 --pool=prefork

# 设置任务超时
celery -A api.celery_app worker --loglevel=info --concurrency=2 --time-limit=600 --soft-time-limit=540
```

### 8.3 数据库优化

**连接池调优**（`api/database.py` 第 75-86 行）：

```python
_engine = create_engine(
    database_url,
    pool_size=10,         # 生产环境可增加
    max_overflow=20,      # 应对突发流量
    pool_timeout=30,
    pool_recycle=3600,
    pool_pre_ping=True,
)
```

**索引优化**（`deploy/init-db.sql` 已创建关键索引）：
- `idx_history_timestamp`：按时间查询历史
- `idx_alert_triggered`：按时间查询告警
- `idx_audit_timestamp`：按时间查询审计

如查询模式有变，可通过 `EXPLAIN ANALYZE` 分析并补充索引。

### 8.4 LLM 调用优化

`config.py` 第 16-23 行 `LLM_CONFIG`：

```python
LLM_CONFIG = {
    "temperature": 0,          # 反洗钱分析需要确定性结论
    "max_tokens": 2000,
    "timeout": 60,
    "retry_times": 3,
    "max_concurrency": 5,      # 最大并发数
    "concurrency_enabled": True,
}
```

优化建议：
- 高并发场景可增加 `max_concurrency`（注意 DeepSeek API 速率限制）
- `retry_times=3` 已配置重试，避免网络抖动失败
- `timeout=60` 秒，可根据实际响应时间调整

### 8.5 缓存优化

`config.py` 第 54-59 行 `CACHE_CONFIG`：

```python
CACHE_CONFIG = {
    "enabled": False,           # 默认关闭，需显式启用
    "expire_days": 7,
    "max_size_mb": 100,
    "skip_when_profile": True,  # 启用账户画像时跳过缓存
}
```

> 缓存默认关闭，避免误用过期结果。仅在数据稳定且性能需求明确时启用。

### 8.6 Redis 优化

```conf
# redis.conf 优化建议
maxmemory 1gb
maxmemory-policy allkeys-lru
appendonly yes
appendfsync everysec
```

---

## 9. 故障排查指南

### 9.1 API 服务无响应

**排查步骤：**

```bash
# 1. 检查容器状态
docker compose ps app

# 2. 检查健康端点
curl -v http://localhost:8000/health

# 3. 查看应用日志
docker compose logs app --tail 100

# 4. 检查端口占用
docker compose exec app netstat -tlnp | grep 8000

# 5. 检查内存
docker stats aml-app
```

**常见原因：**
- 内存不足导致 OOM（查看 `dmesg | grep oom`）
- 数据库连接池耗尽（检查 `pg_stat_activity`）
- LLM API 超时（检查 `DEEPSEEK_API_KEY` 有效性）

### 9.2 分析任务卡在 pending

```bash
# 1. 检查 worker 状态
docker compose ps worker
docker compose logs worker --tail 50

# 2. 检查 Redis 连接
docker compose exec redis redis-cli -a your_redis_password ping

# 3. 检查 Celery 队列
docker compose exec app celery -A api.celery_app inspect active
docker compose exec app celery -A api.celery_app inspect reserved

# 4. 检查 Redis 队列长度
docker compose exec redis redis-cli -a your_redis_password llen celery
```

**解决：**
- worker 容器未启动：`docker compose up -d worker`
- worker 全部忙碌：增加 `--concurrency` 或启动更多 worker
- Redis 队列堆积：检查是否有死任务，`celery purge` 清空队列（谨慎）

### 9.3 数据库连接失败

```bash
# 1. 健康检查
curl -s http://localhost:8000/health | python -m json.tool
# 关注 database.connected 字段

# 2. 检查 PostgreSQL 状态
docker compose ps postgres
docker compose exec postgres pg_isready -U aml_user -d aml_db

# 3. 检查连接数
docker compose exec postgres psql -U aml_user -d aml_db -c \
  "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"

# 4. 查看错误日志
docker compose logs postgres --tail 50
```

**常见原因：**
- 连接池耗尽（`max_connections` 默认 100，连接池 `pool_size=5 + max_overflow=10`）
- 密码错误（检查 `POSTGRES_PASSWORD` 与 `DATABASE_URL` 一致性）
- 数据卷损坏

**降级保护：** PostgreSQL 连接失败时，`api/database.py` 第 99-104 行自动降级为 JSON 模式，业务不中断。

### 9.4 告警未触发

```python
# 1. 检查规则是否启用
from tools.alert_rules import default_registry
for rule in default_registry.list_all():
    print(f"{rule.rule_id}: enabled={rule.enabled}, severity={rule.severity}")

# 2. 检查抑制窗口
from tools.alert_history import AlertHistory
history = AlertHistory()
last_time = history.get_last_trigger_time("risk_high_count_threshold")
print(f"最后触发时间: {last_time}")

# 3. 检查告警历史
history.stats()

# 4. 检查通知器配置
from tools.notifier import create_default_manager
mgr = create_default_manager()
for n in mgr.notifiers:
    print(f"{n.name()}: enabled={n.enabled}")
```

**常见原因：**
- 规则被禁用：`default_registry.enable("rule_id")` 重新启用
- 抑制窗口内（默认 300 秒，`emergency` 除外）
- 通知器未启用或配置错误

### 9.5 LLM 调用失败

```bash
# 1. 验证 API Key 有效性
docker compose exec app python -c "
from config import has_llm, DEEPSEEK_API_KEY
print(f'LLM 可用: {has_llm()}')
print(f'API Key 前缀: {DEEPSEEK_API_KEY[:8]}...')
"

# 2. 测试连通性
docker compose exec app python -c "
from langchain_openai import ChatOpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
llm = ChatOpenAI(model=DEEPSEEK_MODEL, api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
print(llm.invoke('hello').content[:100])
"
```

**常见原因：**
- API Key 为占位符（`config.py` 第 26-31 行 `_PLACEHOLDER_KEYS`）
- 网络不通（DeepSeek API 需外网访问）
- 速率限制（`max_concurrency=5`，可降低）

### 9.6 审计日志未记录

```bash
# 1. 检查审计日志目录
docker compose exec app ls -la /app/data/audit/

# 2. 检查文件内容
docker compose exec app cat /app/data/audit/audit_$(date +%Y-%m-%d).jsonl | head -5

# 3. 检查目录权限
docker compose exec app ls -ld /app/data/audit/
```

**常见原因：**
- 目录权限不足（Dockerfile 已创建 `app` 用户，确保 `data` 卷权限正确）
- 磁盘空间不足

### 9.7 日志脱敏失效

```python
# 验证脱敏是否生效
from api.log_desensitize import desensitize_text
print(desensitize_text("银行卡: 6222021234567890, 手机: 13812345678"))
# 预期: 银行卡: 6222****7890, 手机: 138****5678

# 检查脱敏格式化器是否安装
import logging
logger = logging.getLogger("api")
for handler in logger.handlers:
    print(handler.formatter)
```

---

## 10. 安全运维 Checklist

### 10.1 认证与授权

- [ ] **修改默认管理员密码**：`init-db.sql` 创建的 `admin/admin123` 必须立即修改
  ```bash
  docker compose exec postgres psql -U aml_user -d aml_db -c \
    "UPDATE users SET hashed_password='新bcrypt hash' WHERE username='admin';"
  ```
- [ ] **JWT_SECRET_KEY 已固定**：生产环境必须设置，否则每次重启旧 token 失效
- [ ] **ENCRYPT_KEY 已配置**：用于加密 API 密钥等敏感数据
- [ ] **定期轮换密钥**：建议每 90 天轮换 `JWT_SECRET_KEY` 与 `ENCRYPT_KEY`
- [ ] **JWT 过期时间合理**：默认 24 小时（`JWT_EXPIRATION_HOURS=24`），根据安全策略调整

### 10.2 网络安全

- [ ] **CORS 配置限制**：`CORS_ORIGINS` 仅允许必要的前端域名
- [ ] **PostgreSQL 不对外暴露**：`docker-compose.yml` 中 postgres 未映射主机端口
- [ ] **Redis 不对外暴露**：`docker-compose.yml` 中 redis 未映射主机端口
- [ ] **Redis 密码已设置**：`REDIS_PASSWORD` 必须为强密码
- [ ] **PostgreSQL 密码已设置**：`POSTGRES_PASSWORD` 必须为强密码
- [ ] **使用 HTTPS**：生产环境应在 API 前配置反向代理（Nginx）启用 TLS

### 10.3 容器安全

- [ ] **非 root 运行**：Dockerfile 第 62-73 行已创建 `app` 用户并切换
- [ ] **镜像最小化**：使用 `python:3.10-slim` 基础镜像
- [ ] **无编译缓存**：`pip install --no-cache-dir`、`apt-get clean`
- [ ] **依赖固定版本**：`requirements.txt` 与 `requirements-production.txt` 使用 `>=`，建议生产环境固定确切版本

### 10.4 数据安全

- [ ] **日志脱敏已启用**：`api/main.py` 第 147 行 `patch_logger("api")` 在启动时自动安装
- [ ] **审计日志完整**：所有关键操作（认证、分析、报告、配置、管理、反馈）均已记录
- [ ] **敏感字段加密**：`ENCRYPT_KEY` 用于加密 API 密钥等
- [ ] **数据备份定期执行**：建议每日备份，保留 30 天
- [ ] **备份文件加密**：备份包含敏感数据，应加密存储
- [ ] **备份文件权限**：`backups/` 目录权限设置为仅运维人员可访问

### 10.5 应用安全

- [ ] **生产环境关闭文档端点**：`APP_ENV=production` 时自动关闭 `/docs` 与 `/redoc`（`api/main.py` 第 64-65 行）
- [ ] **异常不泄露详情**：生产环境异常返回 `"内部错误"`（`api/main.py` 第 98 行）
- [ ] **API 限流已启用**：`slowapi` 已集成（`api/main.py` 第 32-38 行）
- [ ] **输入参数校验**：FastAPI 自动校验请求体，校验失败返回 422

### 10.6 监控与告警

- [ ] **Prometheus 指标可抓取**：`/metrics` 端点正常返回
- [ ] **告警规则已审核**：15 条内置规则按需启用/禁用
- [ ] **通知渠道已配置**：至少配置控制台 + 文件，生产环境建议增加 Webhook/邮件
- [ ] **关键告警兜底**：`critical`/`emergency` 级别告警多渠道发送，全失败时控制台兜底
- [ ] **告警抑制窗口合理**：避免告警风暴（默认 300 秒，`emergency` 不抑制）

### 10.7 合规审计

- [ ] **审计日志保留期达标**：建议保留至少 180 天（金融合规要求通常为 5 年）
- [ ] **审计日志不可篡改**：JSONL 文件应设置仅追加权限（`chattr +a`）
- [ ] **定期审计审计日志**：检查异常登录、权限变更、敏感操作
- [ ] **告警历史可追溯**：`data/alerts/` 目录保留完整告警记录

### 10.8 应急响应

- [ ] **制定故障响应流程**：明确告警接收人、响应时间、处理步骤
- [ ] **定期演练**：至少每季度进行一次故障恢复演练
- [ ] **备份恢复验证**：定期验证备份可恢复性
- [ ] **文档更新**：本手册与部署指南随系统变更及时更新

### 10.9 安全检查命令

```bash
# 1. 检查弱密码用户（PostgreSQL）
docker compose exec postgres psql -U aml_user -d aml_db -c \
  "SELECT username, last_login FROM users WHERE is_active = true;"

# 2. 检查异常登录（审计日志）
docker compose exec app python -c "
from api.audit_log import audit_logger, OperationType, OperationResult
result = audit_logger.query(operation_type=OperationType.AUTH, result=OperationResult.FAILED, limit=20)
for e in result['entries']:
    print(f\"{e['timestamp']} {e['ip_address']} {e['action']}\")
"

# 3. 检查容器以非 root 运行
docker compose exec app whoami
# 预期: app

# 4. 检查 Redis 是否需要密码
docker compose exec redis redis-cli ping
# 预期: NOAUTH Authentication required.

# 5. 检查 API 文档端点是否关闭（生产环境）
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/docs
# 生产环境预期: 404

# 6. 检查日志脱敏是否生效
docker compose exec app grep -c "[0-9]\{16\}" /app/logs/api.log
# 预期: 0 或极少（银行卡号应已脱敏）

# 7. 检查审计日志目录权限
docker compose exec app ls -ld /app/data/audit/
# 预期: drwxr-xr-x ... app app ...

# 8. 检查备份完整性
ls -la backups/
# 确认有最近的备份文件
```
