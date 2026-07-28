# AML-Agent API 文档

> 反洗钱多 Agent 分析系统 API
> 版本：1.0.0
> 框架：FastAPI

---

## 目录

- [1. API 概述](#1-api-概述)
- [2. 认证说明](#2-认证说明)
- [3. 系统端点](#3-系统端点)
- [4. 认证端点](#4-认证端点)
- [5. 分析端点](#5-分析端点)
- [6. 报告端点](#6-报告端点)
- [7. 错误处理](#7-错误处理)
- [8. 速率限制](#8-速率限制)
- [9. 数据模型](#9-数据模型)

---

## 1. API 概述

### Base URL

```
http://localhost:8000
```

> 生产环境请替换为实际部署域名。CORS 默认允许源通过环境变量 `CORS_ORIGINS` 配置（默认 `http://localhost:8501,http://localhost:8000`）。

### 版本

当前版本：`1.0.0`

### 认证方式

- **认证类型**：JWT Bearer Token（OAuth2 Password Flow）
- **传输方式**：HTTP 请求头 `Authorization: Bearer <access_token>`
- **Token 获取**：通过 `POST /api/auth/login` 端点登录获取
- **Token 有效期**：默认 24 小时（由环境变量 `JWT_EXPIRATION_HOURS` 控制）
- **签名算法**：HS256（由环境变量 `JWT_ALGORITHM` 控制）

### 内容类型

- 请求体：`application/json`（登录端点使用 `application/x-www-form-urlencoded`）
- 响应体：`application/json`（文件下载端点除外）

### 交互式文档

开发环境提供交互式 API 文档（生产环境关闭）：
- Swagger UI：`/docs`
- ReDoc：`/redoc`

---

## 2. 认证说明

### JWT Bearer Token

系统采用 JWT（JSON Web Token）进行身份认证。除系统端点（健康检查、监控、根路径）外，所有业务端点均需在请求头中携带有效的 JWT：

```
Authorization: Bearer <access_token>
```

### 角色体系

系统支持以下角色：

| 角色 | 说明 | 默认用户（开发环境） |
|------|------|---------------------|
| `admin` | 管理员，可访问所有端点（含批量导出等管理功能） | `admin` |
| `analyst` | 分析师，可访问常规分析、查询、导出功能 | `analyst` |

> 开发环境默认用户密码通过环境变量 `DEV_ADMIN_PASSWORD` 与 `DEV_ANALYST_PASSWORD` 配置；生产环境必须使用 PostgreSQL 用户表。

### 登录获取 Token

通过 `POST /api/auth/login` 提交用户名密码，使用 OAuth2 标准表单格式获取 Token。详见 [4. 认证端点](#4-认证端点)。

### 角色权限标注说明

文档中端点权限标注含义：
- 🔓 **公开**：无需认证
- 🔐 **需认证**：需携带有效的 Bearer Token
- 👑 **需 admin 角色**：需携带 admin 角色用户的 Token

---

## 3. 系统端点

### 3.1 根路径 🔓

`GET /`

返回 API 基本信息。

**请求示例**

```bash
curl -X GET http://localhost:8000/
```

**响应示例**

```json
{
  "message": "AML-Agent API",
  "docs": "/docs",
  "health": "/health"
}
```

---

### 3.2 健康检查 🔓

`GET /health`

返回系统健康状态及数据库连接状态。

**请求示例**

```bash
curl -X GET http://localhost:8000/health
```

**响应示例**

```json
{
  "status": "healthy",
  "timestamp": "2026-07-27T10:00:00.000000",
  "version": "1.0.0",
  "database": true
}
```

**响应字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | 系统状态，`healthy` 表示正常 |
| `timestamp` | string | 当前服务器时间（ISO 8601） |
| `version` | string | API 版本号 |
| `database` | boolean | 数据库连接状态，`true` 已连接 PostgreSQL，`false` 使用 JSON 文件模式 |

---

### 3.3 Prometheus 监控指标 🔓

`GET /metrics`

返回 Prometheus 格式的监控指标，供 Prometheus 抓取。

**请求示例**

```bash
curl -X GET http://localhost:8000/metrics
```

**响应**

- **Content-Type**：`text/plain`
- **Body**：Prometheus 标准文本格式指标

---

## 4. 认证端点

### 4.1 用户登录 🔓

`POST /api/auth/login`

使用 OAuth2 标准表单提交用户名密码，登录成功后返回 JWT access token。

> **速率限制**：5 次/分钟（基于客户端 IP）

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| `username` | form-data | string | 是 | 用户名 |
| `password` | form-data | string | 是 | 密码 |

> ⚠️ 该端点使用 `application/x-www-form-urlencoded` 表单格式，非 JSON。

**请求示例**

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=your_password"
```

**成功响应** `200 OK`

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 86400.0,
  "user": {
    "user_id": "admin-001",
    "username": "admin",
    "role": "admin",
    "email": "admin@aml-agent.local"
  }
}
```

**响应字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `access_token` | string | JWT access token |
| `token_type` | string | Token 类型，固定为 `bearer` |
| `expires_in` | number | Token 有效期（秒），默认 86400 |
| `user.user_id` | string | 用户 ID |
| `user.username` | string | 用户名 |
| `user.role` | string | 用户角色（`admin` 或 `analyst`） |
| `user.email` | string | 用户邮箱 |

**错误响应**

| 状态码 | 说明 |
|--------|------|
| 401 | 用户名或密码错误 |
| 429 | 触发速率限制（5 次/分钟） |

```json
{
  "detail": "用户名或密码错误"
}
```

---

### 4.2 获取当前用户信息 🔐

`GET /api/auth/me`

返回当前登录用户的详细信息。

**请求示例**

```bash
curl -X GET http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer <access_token>"
```

**成功响应** `200 OK`

```json
{
  "user_id": "admin-001",
  "username": "admin",
  "role": "admin",
  "email": "admin@aml-agent.local",
  "is_active": true
}
```

**响应字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_id` | string | 用户 ID |
| `username` | string | 用户名 |
| `role` | string | 用户角色 |
| `email` | string | 用户邮箱 |
| `is_active` | boolean | 是否激活 |

**错误响应**

| 状态码 | 说明 |
|--------|------|
| 401 | 未携带 Token 或 Token 无效 |

---

## 5. 分析端点

> 所有分析端点均需认证 🔐

### 5.1 提交异步分析任务 🔐

`POST /api/analysis/`

提交反洗钱分析任务到异步队列（基于 Celery + Redis），立即返回任务 ID。任务状态可通过 `GET /api/analysis/tasks/{task_id}` 查询。

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| `transactions` | body | array[object] | 是 | 交易数据列表 |
| `auto_evaluate` | query | boolean | 否 | 是否自动评估，默认 `false` |

**请求体示例**

```json
[
  {
    "transaction_id": "txn_001",
    "account_id": "acc_001",
    "counterparty_account": "acc_002",
    "amount": 50000,
    "timestamp": "2026-07-27T10:00:00",
    "type": "transfer",
    "remark": "货款"
  }
]
```

**请求示例**

```bash
curl -X POST "http://localhost:8000/api/analysis/?auto_evaluate=false" \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '[
    {
      "transaction_id": "txn_001",
      "account_id": "acc_001",
      "counterparty_account": "acc_002",
      "amount": 50000,
      "timestamp": "2026-07-27T10:00:00",
      "type": "transfer"
    }
  ]'
```

**成功响应** `202 Accepted`

```json
{
  "task_id": "0cc3570e",
  "status": "pending",
  "message": "分析任务已提交",
  "transactions_count": 1,
  "submitted_at": "2026-07-27T10:00:00.000000"
}
```

**响应字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | string | 任务 ID（即 execution_id，用于后续查询） |
| `status` | string | 任务状态，初始为 `pending` |
| `message` | string | 描述信息 |
| `transactions_count` | integer | 提交的交易笔数 |
| `submitted_at` | string | 提交时间（ISO 8601） |

**错误响应**

| 状态码 | 说明 |
|--------|------|
| 400 | 交易数据不能为空 |
| 401 | 未认证 |
| 422 | 请求参数格式错误 |

---

### 5.2 获取任务列表 🔐

`GET /api/analysis/tasks`

返回历史分析任务列表。

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| `limit` | query | integer | 否 | 返回数量限制，默认 `20` |

**请求示例**

```bash
curl -X GET "http://localhost:8000/api/analysis/tasks?limit=20" \
  -H "Authorization: Bearer <access_token>"
```

**成功响应** `200 OK`

```json
[
  {
    "task_id": "0cc3570e",
    "status": "completed",
    "transactions_count": 10,
    "rule_hit_count": 3,
    "report_count": 2,
    "duration_seconds": 12.5,
    "timestamp": "2026-07-27T10:00:00",
    "risk_distribution": {
      "critical": 1,
      "high": 1,
      "medium": 0,
      "low": 0
    }
  }
]
```

**响应字段（数组元素）**

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | string | 任务 ID（execution_id） |
| `status` | string | 任务状态 |
| `transactions_count` | integer | 交易笔数 |
| `rule_hit_count` | integer | 规则命中数 |
| `report_count` | integer | 生成的报告数 |
| `duration_seconds` | number | 分析耗时（秒） |
| `timestamp` | string | 运行时间（ISO 8601） |
| `risk_distribution` | object | 风险等级分布 |

---

### 5.3 获取任务详情 🔐

`GET /api/analysis/tasks/{task_id}`

根据任务 ID（execution_id）查询任务详情。

**路径参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `task_id` | string | 任务 ID（execution_id） |

**请求示例**

```bash
curl -X GET http://localhost:8000/api/analysis/tasks/0cc3570e \
  -H "Authorization: Bearer <access_token>"
```

**成功响应** `200 OK`

```json
{
  "task_id": "0cc3570e",
  "status": "completed",
  "progress": 100,
  "message": "任务已完成",
  "transactions_count": 10,
  "transactions_hash": "abc123...",
  "rule_hit_count": 3,
  "report_count": 2,
  "duration_seconds": 12.5,
  "timestamp": "2026-07-27T10:00:00",
  "risk_distribution": {
    "critical": 1,
    "high": 1,
    "medium": 0,
    "low": 0
  },
  "value_metrics": {},
  "primary_accounts": ["acc_001", "acc_002"],
  "rule_details": {},
  "interrupted": false,
  "error": ""
}
```

**响应字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | string | 任务 ID |
| `status` | string | 任务状态（`pending`/`running`/`completed`/`failed`） |
| `progress` | integer | 进度百分比（0-100） |
| `message` | string | 状态描述 |
| `transactions_count` | integer | 交易笔数 |
| `transactions_hash` | string | 交易数据哈希（用于去重/校验） |
| `rule_hit_count` | integer | 规则命中数 |
| `report_count` | integer | 报告数 |
| `duration_seconds` | number | 耗时（秒） |
| `timestamp` | string | 运行时间 |
| `risk_distribution` | object | 风险等级分布 |
| `value_metrics` | object | 价值指标 |
| `primary_accounts` | array[string] | 主要涉案账户 |
| `rule_details` | object | 规则命中详情 |
| `interrupted` | boolean | 是否被中断 |
| `error` | string | 错误信息（无错误时为空字符串） |

**错误响应**

| 状态码 | 说明 |
|--------|------|
| 401 | 未认证 |
| 404 | 任务不存在 |

---

### 5.4 同步执行分析 🔐

`POST /api/analysis/run`

同步阻塞执行反洗钱分析，等待分析完成后返回完整结果。适用于少量交易或调试场景。

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| `transactions` | body | array[object] | 是 | 交易数据列表 |
| `auto_evaluate` | query | boolean | 否 | 是否自动评估，默认 `false` |

**请求示例**

```bash
curl -X POST "http://localhost:8000/api/analysis/run?auto_evaluate=false" \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '[
    {
      "transaction_id": "txn_001",
      "account_id": "acc_001",
      "counterparty_account": "acc_002",
      "amount": 50000,
      "timestamp": "2026-07-27T10:00:00",
      "type": "transfer"
    }
  ]'
```

**成功响应** `200 OK`

```json
{
  "task_id": "0cc3570e",
  "status": "completed",
  "message": "分析完成",
  "transactions_count": 10,
  "rule_hit_count": 3,
  "report_count": 2,
  "risk_distribution": {
    "critical": 1,
    "high": 1,
    "medium": 0,
    "low": 0
  },
  "value_metrics": {},
  "compliance_score": 85.5,
  "total_processing_time": 12.5
}
```

**响应字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | string | 任务 ID（execution_id） |
| `status` | string | 任务状态，固定为 `completed` |
| `message` | string | 描述信息 |
| `transactions_count` | integer | 交易笔数 |
| `rule_hit_count` | integer | 规则命中数 |
| `report_count` | integer | 报告数 |
| `risk_distribution` | object | 风险等级分布 |
| `value_metrics` | object | 价值指标 |
| `compliance_score` | number | 合规评分 |
| `total_processing_time` | number | 总处理时间（秒） |

**错误响应**

| 状态码 | 说明 |
|--------|------|
| 400 | 交易数据不能为空 |
| 401 | 未认证 |
| 422 | 请求参数格式错误 |
| 500 | 分析失败 |

---

## 6. 报告端点

> 所有报告端点均需认证 🔐；批量导出端点需 admin 角色 👑

### 6.1 获取报告列表 🔐

`GET /api/reports/`

返回历史分析生成的报告列表。

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| `limit` | query | integer | 否 | 返回数量限制，默认 `20` |

**请求示例**

```bash
curl -X GET "http://localhost:8000/api/reports/?limit=20" \
  -H "Authorization: Bearer <access_token>"
```

**成功响应** `200 OK`

```json
[
  {
    "report_id": "0cc3570e",
    "execution_id": "0cc3570e",
    "timestamp": "2026-07-27T10:00:00",
    "report_count": 2,
    "risk_distribution": {
      "critical": 1,
      "high": 1,
      "medium": 0,
      "low": 0
    },
    "primary_accounts": ["acc_001", "acc_002"],
    "transactions_count": 10,
    "rule_hit_count": 3
  }
]
```

---

### 6.2 获取报告详情 🔐

`GET /api/reports/{report_id}`

根据报告 ID（即 execution_id）查询报告详情，包含 STR（可疑交易报告）原始内容。

**路径参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `report_id` | string | 报告 ID（execution_id） |

**请求示例**

```bash
curl -X GET http://localhost:8000/api/reports/0cc3570e \
  -H "Authorization: Bearer <access_token>"
```

**成功响应** `200 OK`

```json
{
  "report_id": "0cc3570e",
  "execution_id": "0cc3570e",
  "timestamp": "2026-07-27T10:00:00",
  "transactions_count": 10,
  "rule_hit_count": 3,
  "report_count": 2,
  "risk_distribution": {
    "critical": 1,
    "high": 1
  },
  "primary_accounts": ["acc_001", "acc_002"],
  "str_reports": [
    {
      "report_id": "str_001",
      "account_id": "acc_001",
      "risk_score": 85,
      "risk_level": "critical",
      "evidence": []
    }
  ],
  "duration_seconds": 12.5,
  "interrupted": false
}
```

**错误响应**

| 状态码 | 说明 |
|--------|------|
| 401 | 未认证 |
| 404 | 报告不存在 |

---

### 6.3 导出报告为 Excel 🔐

`GET /api/reports/{report_id}/export/excel`

将指定报告导出为 Excel 文件下载。

**路径参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `report_id` | string | 报告 ID（execution_id） |

**请求示例**

```bash
curl -X GET http://localhost:8000/api/reports/0cc3570e/export/excel \
  -H "Authorization: Bearer <access_token>" \
  -o report_0cc3570e.xlsx
```

**成功响应** `200 OK`

- **Content-Type**：`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- **Content-Disposition**：`attachment; filename=report_{report_id}.xlsx`
- **Body**：Excel 文件二进制流

**错误响应**

| 状态码 | 说明 |
|--------|------|
| 401 | 未认证 |
| 404 | 报告不存在 / 报告数据为空 |
| 500 | 报告导出失败 |

---

### 6.4 导出报告为 PDF 🔐

`GET /api/reports/{report_id}/export/pdf`

将指定报告导出为 PDF 文件下载。

**路径参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `report_id` | string | 报告 ID（execution_id） |

**请求示例**

```bash
curl -X GET http://localhost:8000/api/reports/0cc3570e/export/pdf \
  -H "Authorization: Bearer <access_token>" \
  -o report_0cc3570e.pdf
```

**成功响应** `200 OK`

- **Content-Type**：`application/pdf`
- **Content-Disposition**：`attachment; filename=report_{report_id}.pdf`
- **Body**：PDF 文件二进制流

**错误响应**

| 状态码 | 说明 |
|--------|------|
| 401 | 未认证 |
| 404 | 报告不存在 / 报告数据为空 |
| 500 | 报告导出失败 |

---

### 6.5 批量导出报告 👑

`GET /api/reports/export/batch`

批量导出多个报告为 ZIP 压缩包（含汇总文件）。**仅 admin 角色可调用**。

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| `report_ids` | query | string | 否 | 报告 ID 列表（逗号分隔），为空则导出所有报告 |

**请求示例**

```bash
# 导出指定报告
curl -X GET "http://localhost:8000/api/reports/export/batch?report_ids=0cc3570e,316682b2" \
  -H "Authorization: Bearer <admin_access_token>" \
  -o reports_batch.zip

# 导出所有报告
curl -X GET "http://localhost:8000/api/reports/export/batch" \
  -H "Authorization: Bearer <admin_access_token>" \
  -o reports_batch.zip
```

**成功响应** `200 OK`

- **Content-Type**：`application/zip`
- **Content-Disposition**：`attachment; filename=reports_batch.zip`
- **Body**：ZIP 文件二进制流

**错误响应**

| 状态码 | 说明 |
|--------|------|
| 401 | 未认证 |
| 403 | 权限不足（需要 admin 角色） |
| 404 | 没有可导出的报告 |
| 500 | 批量导出失败 |

---

### 6.6 获取报告统计信息 🔐

`GET /api/reports/stats`

返回所有历史报告的汇总统计信息。

**请求示例**

```bash
curl -X GET http://localhost:8000/api/reports/stats \
  -H "Authorization: Bearer <access_token>"
```

**成功响应** `200 OK`

```json
{
  "total_runs": 8,
  "total_reports": 15,
  "total_transactions": 120,
  "avg_duration": 12.5,
  "first_run": "2026-07-01T10:00:00",
  "last_run": "2026-07-27T10:00:00"
}
```

**响应字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `total_runs` | integer | 总运行次数 |
| `total_reports` | integer | 总报告数 |
| `total_transactions` | integer | 总交易笔数 |
| `avg_duration` | number | 平均耗时（秒） |
| `first_run` | string | 首次运行时间（ISO 8601） |
| `last_run` | string | 最近运行时间（ISO 8601） |

---

## 7. 错误处理

### 统一错误响应格式

所有错误响应均使用统一的 JSON 格式：

```json
{
  "detail": "错误描述信息"
}
```

对于参数校验错误，响应包含详细的字段级错误信息：

```json
{
  "detail": [
    {
      "loc": ["body"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ],
  "msg": "请求参数格式错误"
}
```

### HTTP 状态码

| 状态码 | 说明 | 触发场景 |
|--------|------|---------|
| 200 | 成功 | 常规查询、同步分析、文件下载 |
| 202 | 已接受 | 异步任务提交成功 |
| 400 | 请求错误 | 请求参数业务校验失败（如交易数据为空） |
| 401 | 未认证 | 未携带 Token / Token 无效 / 用户名密码错误 |
| 403 | 禁止访问 | 角色权限不足（如非 admin 调用 admin 端点） |
| 404 | 资源不存在 | 任务/报告不存在 |
| 422 | 参数格式错误 | 请求体格式不符合 FastAPI 校验规则 |
| 429 | 请求过多 | 触发速率限制 |
| 500 | 服务器内部错误 | 未捕获异常 / 分析失败 / 导出失败 |

### 全局异常处理

系统注册了以下全局异常处理器：

1. **`RequestValidationError` 处理器**：捕获参数校验错误，返回 422 状态码及详细错误信息。
2. **全局 `Exception` 处理器**：捕获所有未处理异常，返回 500 状态码。生产环境（`APP_ENV=production`）不泄露内部异常详情，仅返回 `"内部错误"`；开发环境返回具体异常信息。

---

## 8. 速率限制

### 限制规则

系统使用 `slowapi`（基于客户端 IP）实现速率限制。

| 端点 | 限制 | 说明 |
|------|------|------|
| `POST /api/auth/login` | 5 次/分钟 | 防止暴力破解密码 |

### 限制前提

- 速率限制功能依赖 `slowapi` 库。若未安装（`ImportError`），限流装饰器降级为空操作，不生效。
- 限制基于客户端 IP 地址（`get_remote_address`）。

### 触发限流的响应

触发速率限制时返回 `429 Too Many Requests`：

```json
{
  "error": "Rate limit exceeded: 5 per 1 minute"
}
```

---

## 9. 数据模型

### 9.1 任务状态枚举

| 状态 | 说明 |
|------|------|
| `pending` | 等待处理 |
| `running` | 分析中 |
| `completed` | 已完成 |
| `failed` | 失败 |

### 9.2 风险等级

风险评分范围 0-100，分值越高可疑程度越高。

| 等级 | 最低分 | 说明 |
|------|--------|------|
| `critical` | 85 | 严重可疑 |
| `high` | 70 | 高风险 |
| `medium` | 50 | 中等风险 |
| `low` | 0 | 低风险 |

### 9.3 用户角色

| 角色 | 说明 |
|------|------|
| `admin` | 管理员，拥有全部权限 |
| `analyst` | 分析师，常规业务权限 |

### 9.4 用户模型（User）

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_id` | string(36) | 用户 ID（主键） |
| `username` | string(50) | 用户名（唯一） |
| `hashed_password` | string(255) | 密码哈希（bcrypt） |
| `email` | string(100) | 邮箱（唯一，可为空） |
| `role` | string(20) | 角色，默认 `analyst` |
| `is_active` | boolean | 是否激活，默认 `true` |
| `created_at` | datetime | 创建时间 |
| `last_login` | datetime | 最近登录时间（可为空） |

### 9.5 分析历史模型（AnalysisHistory）

| 字段 | 类型 | 说明 |
|------|------|------|
| `execution_id` | string(20) | 执行 ID（主键，即 task_id） |
| `timestamp` | datetime | 运行时间 |
| `transactions_count` | integer | 交易笔数 |
| `rule_hit_count` | integer | 规则命中数 |
| `str_reports_count` | integer | STR 报告数 |
| `compliance_score` | numeric(5,2) | 合规评分 |
| `total_processing_time_sec` | numeric(10,3) | 总耗时（秒） |
| `value_metrics` | JSON | 价值指标 |
| `config_snapshot` | JSON | 配置快照 |
| `created_at` | datetime | 创建时间 |

### 9.6 账户风险画像模型（Account）

| 字段 | 类型 | 说明 |
|------|------|------|
| `account_id` | string(50) | 账户 ID（主键） |
| `risk_multiplier` | numeric(10,4) | 风险乘数，默认 1.0 |
| `suspicious_count` | integer | 可疑命中次数 |
| `false_positive_count` | integer | 误报次数 |
| `false_negative_count` | integer | 漏报次数 |
| `last_suspicious_time` | datetime | 最近可疑时间 |
| `last_feedback_time` | datetime | 最近反馈时间 |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |
| `metadata` | JSON | 扩展元数据 |

### 9.7 审计日志模型（AuditLog）

| 字段 | 类型 | 说明 |
|------|------|------|
| `log_id` | integer | 日志 ID（主键，自增） |
| `user_id` | string(36) | 操作用户 ID |
| `action` | string(100) | 操作类型 |
| `resource_type` | string(50) | 资源类型 |
| `resource_id` | string(100) | 资源 ID |
| `ip_address` | string(50) | IP 地址 |
| `timestamp` | datetime | 操作时间 |
| `details` | JSON | 操作详情 |

### 9.8 告警历史模型（AlertHistoryRecord）

| 字段 | 类型 | 说明 |
|------|------|------|
| `alert_id` | string(36) | 告警 ID（主键） |
| `rule_id` | string(50) | 规则 ID |
| `severity` | string(20) | 严重级别 |
| `category` | string(50) | 告警类别 |
| `message` | text | 告警消息 |
| `triggered_at` | datetime | 触发时间 |
| `acknowledged_at` | datetime | 确认时间 |
| `acknowledged_by` | string(50) | 确认人 |
| `metadata` | JSON | 扩展元数据 |

### 9.9 评估结果模型（EvaluationResult）

| 字段 | 类型 | 说明 |
|------|------|------|
| `eval_id` | string(20) | 评估 ID（主键） |
| `execution_id` | string(20) | 关联执行 ID |
| `ground_truth_name` | string(100) | 真值集名称 |
| `precision_score` | numeric(5,4) | 精确率 |
| `recall_score` | numeric(5,4) | 召回率 |
| `f1_score` | numeric(5,4) | F1 分数 |
| `tp` | integer | 真正例 |
| `fp` | integer | 假正例 |
| `tn` | integer | 真负例 |
| `fn` | integer | 假负例 |
| `scan_results` | JSON | 扫描结果 |
| `created_at` | datetime | 创建时间 |

---

## 附录：环境变量配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `JWT_SECRET_KEY` | （随机生成） | JWT 签名密钥，生产环境必须设置 |
| `JWT_ALGORITHM` | `HS256` | JWT 签名算法 |
| `JWT_EXPIRATION_HOURS` | `24` | Token 有效期（小时） |
| `APP_ENV` | `development` | 运行环境（`development`/`production`） |
| `CORS_ORIGINS` | `http://localhost:8501,http://localhost:8000` | CORS 允许源（逗号分隔） |
| `DATABASE_URL` | （空） | PostgreSQL 连接 URL，为空时使用 JSON 文件模式 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接 URL（Celery 异步任务） |
| `DEV_ADMIN_PASSWORD` | （空） | 开发环境 admin 密码 |
| `DEV_ANALYST_PASSWORD` | （空） | 开发环境 analyst 密码 |
