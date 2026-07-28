# 智能反洗钱检测系统 (AML Detection System)

基于 LangGraph + 多智能体协作的企业级反洗钱交易检测系统，支持 10 种可疑模式识别、LLM 深度审核、STR 报告自动生成、参数自动优化闭环。

---

## 功能特性

### 核心检测能力
- **10 条反洗钱规则引擎**：分拆转账、快进快出、对敲交易、大额交易、基线偏离、备注关键词、空壳公司、制裁名单、跨境异常、虚拟货币模式
- **6 Agent 协作工作流**（LangGraph）：数据预处理 → 规则引擎 → 图分析 → LLM 深审 → 报告生成 → 合规审核
- **智能主涉案方识别**：根据命中规则类型动态确定主涉案账户
- **双层证据链结构**：结构化证据（机器可验证）+ 文本证据（人类可读）

### 使用方式
- 🖥️ **命令行**：`python main.py`，适合脚本/自动化
- 🌐 **Web 界面**：`streamlit run app.py`，6 个 Tab 可视化操作
- 🔌 **REST API**：`uvicorn api.main:app`，生产级接口（JWT + 限流 + 审计）

### 反馈与优化闭环
- ✅ **反馈质量三层校验**：格式 → 内容质量 → 一致性
- ⏳ **反馈权重时间衰减**：误报 90 天 / 漏报 365 天 / 确认 180 天半衰期
- 📚 **真值集版本管理**：快照 / 对比 / 回滚 / 变更日志
- 📈 **反馈效果追踪**：指标对比 / 趋势分析 / 改进评估
- 🎯 **多目标参数优化**：precision + recall + f1 帕累托前沿
- 🏭 **行业差异化参数**：按行业定制规则阈值
- 🧪 **A/B 测试框架**：参数对比 + 戒律守护决策
- 🔄 **交叉影响分析**：识别参数变更对其他规则的副作用
- ♻️ **五阶段自动优化闭环**：数据 → 评估 → 反馈调权 → 调参 → 验证

### 生产就绪
- 🔐 JWT 认证 + 密码加密 + API Key 加密存储
- 📊 Prometheus 监控指标 + 审计日志
- 🗄️ 双模式存储：JSON 文件（零配置）/ PostgreSQL（生产）
- 🐳 Docker Compose 一键部署（App + PG + Redis + Nginx）
- 🛡️ 业务戒律守护：P1 不遗漏 / P2 不误报 / M2 证据完整 / M4 可追溯

---

## 快速开始

### 环境要求
- Python 3.10+
- DeepSeek API Key（[免费申请](https://platform.deepseek.com/)）

### 安装

```bash
git clone <your-repo-url>
cd 反洗钱
pip install -r requirements.txt
```

### 配置

```bash
cp .env.example .env
```

编辑 `.env`，至少填入：
```
DEEPSEEK_API_KEY=your_deepseek_api_key
```

其他参数（JWT 密钥、数据库、Redis 等）均为可选，留空即使用默认值。

### 运行

#### 方式一：命令行（最快）

```bash
# 使用模拟数据 + LLM 深审
python main.py

# 不调用 LLM（离线模式，走降级评分）
python main.py --no-llm

# 分析自己的交易文件
python main.py --file your_transactions.csv
```

#### 方式二：Web 界面

```bash
streamlit run app.py
```

浏览器打开 http://localhost:8501

#### 方式三：API 服务

```bash
uvicorn api.main:app --reload --port 8000
```

打开 http://localhost:8000/docs 查看 Swagger 文档。

---

## 项目结构

```
反洗钱/
├── agents/                  # 智能体层
│   ├── rule_engine.py       # 规则引擎（10条规则 + 证据链结构化）
│   ├── graph_analyst.py     # 图分析智能体
│   ├── llm_reviewer.py      # LLM 深度审核（含降级评分一致性）
│   ├── report_generator.py  # STR 报告生成（智能主涉案方识别）
│   └── compliance_auditor.py# 合规审核智能体
├── graph/                   # LangGraph 工作流
│   └── workflow.py          # 6 Agent 协作编排
├── tools/                   # 工具层
│   ├── rule_tuner.py        # 规则参数调优
│   ├── multi_objective_optimizer.py  # 多目标优化（帕累托前沿）
│   ├── feedback_manager.py  # 反馈管理（三层校验 + 时间衰减）
│   ├── ground_truth_versioner.py     # 真值集版本管理
│   ├── feedback_effect_tracker.py    # 反馈效果追踪
│   ├── industry_param_resolver.py    # 行业差异化参数
│   ├── ab_test_runner.py    # A/B 测试框架
│   ├── cross_impact_analyzer.py      # 交叉影响分析
│   ├── optimization_loop.py # 五阶段自动优化闭环
│   └── invariant_checker.py # 端到端不变量检查
├── api/                     # FastAPI 服务层
│   ├── main.py              # API 入口
│   ├── routes/              # 路由（auth/analysis/reports/upload）
│   ├── models.py            # SQLAlchemy 模型
│   ├── database.py          # 数据库双模式（JSON/PostgreSQL）
│   └── ...
├── config/                  # 配置模块（11个分类文件）
├── data/                    # 数据目录（运行时生成，不提交）
├── reports/                 # 报告输出（运行时生成，不提交）
├── tests/                   # 测试（1200+ 用例）
│   ├── unit/                # 单元测试
│   ├── integration/         # 集成测试
│   └── benchmarks/          # 性能测试
├── scripts/                 # 工具脚本
├── docs/                    # 文档
├── deploy/                  # 部署配置（Docker/Nginx）
├── static/                  # 静态资源
├── templates/               # 报告模板
├── main.py                  # 命令行入口
├── app.py                   # Streamlit Web 入口
├── requirements.txt         # Python 依赖
├── docker-compose.yml       # Docker Compose 部署
├── .env.example             # 环境变量示例
└── pytest.ini               # 测试配置
```

---

## 支持的 10 条检测规则

| 规则 | 代码 | 说明 | 默认阈值 |
|------|------|------|---------|
| 分拆转账 | smurfing | 同一收款人多笔相近金额，规避大额上报 | 5 笔 / 1 小时 / 4-5 万 |
| 快进快出 | fast_in_fast_out | 资金快速中转，金额相近 | 10 分钟 / 95% 金额匹配 |
| 对敲交易 | round_trip | 资金转出后原路返回 | 7 天 / 金额差 < 20% |
| 大额交易 | large_amount | 单笔超大额 | 10 万 |
| 基线偏离 | baseline_deviation | 交易行为偏离历史均值 | 偏离 3 倍标准差 |
| 备注关键词 | remark_keywords | 备注含敏感词 | 关键词库匹配 |
| 空壳公司 | shell_companies | 多层嵌套、注册资本不实 | 股权穿透分析 |
| 制裁名单 | sanction_list | 涉及制裁实体 | 名单匹配 |
| 跨境异常 | cross_border | 跨境交易异常模式 | 金额/频率/地区 |
| 虚拟货币模式 | crypto_pattern | 类虚拟货币交易特征 | 多对一 / 分散归集 |

---

## 五阶段自动优化闭环

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│ Stage 1 │───▶│ Stage 2 │───▶│ Stage 3 │───▶│ Stage 4 │───▶│ Stage 5 │
│数据收集 │    │参数评估 │    │反馈调权 │    │参数调优 │    │验证推荐 │
└─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘
                                                               │
                                                               ▼
                                                          推荐 action
                                                       (apply/keep/review)
```

- **Stage 1**：加载交易 + 真值集 + 当前参数
- **Stage 2**：计算 precision / recall / f1 / 混淆矩阵
- **Stage 3**：基于反馈类型+时间衰减动态调整目标权重（漏报多→提 recall，误报多→提 precision）
- **Stage 4**：多目标网格搜索 + 帕累托前沿 + 交叉影响分析
- **Stage 5**：A/B 测试 + 不变量检查 + 戒律守护 → 输出推荐

运行示例：
```bash
python scripts/run_optimization_loop_demo.py
```

---

## 业务戒律

代码层强制保证的核心规则：

| 编号 | 戒律 | 说明 |
|------|------|------|
| **M1** | 真实数据 | 评估基于真实交易+真值集，不编造指标 |
| **M2** | 证据完整 | 评分≥60 的可疑交易证据链不为空；行业画像必须标注适用理由 |
| **M3** | 评分范围 | 所有风险评分在 [0, 100] 范围内 |
| **M4** | 可追溯 | 全过程原子持久化（tmp + os.replace），索引可查 |
| **P1** | 不遗漏 | 漏报反馈多时提高 recall 权重；候选 recall 下降≥30% 拒绝 |
| **P2** | 不误报 | 误报反馈多时提高 precision 权重；总命中激增≥200% 警告 |
| **P4** | 非破坏性 | 只推荐候选参数，不自动应用 |

---

## 测试

```bash
# 运行全部测试
python -m pytest

# 只跑单元测试
python -m pytest tests/unit/

# 只跑集成测试
python -m pytest tests/integration/
```

**1200+ 测试用例**，覆盖规则边界条件、LLM 降级、证据链、不变量、反馈管理、参数优化、A/B 测试、交叉影响、五阶段闭环等。

---

## 生产部署

### Docker Compose（推荐）

```bash
cp .env.example .env
# 编辑 .env，填入生产配置
docker compose up -d
```

包含：AML API + PostgreSQL + Redis + Nginx（HTTPS）

### 详细部署文档
见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 工作流编排 | LangGraph / LangChain |
| LLM | DeepSeek API（可切换任意 OpenAI 兼容接口） |
| Web 框架 | FastAPI + Streamlit |
| 数据库 | SQLAlchemy + PostgreSQL（可选，默认 JSON 文件） |
| 异步任务 | Celery + Redis（可选） |
| 图算法 | NetworkX（可选 PyG 图神经网络） |
| 可视化 | Plotly + PyVis |
| 测试 | pytest（1200+ 用例） |
| 部署 | Docker + Nginx |

---

## 许可证

MIT License

---

## 免责声明

本项目为技术演示和学习用途，不构成任何金融或法律建议。实际反洗钱合规请遵循当地监管要求并咨询专业人士。
