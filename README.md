<div align="center">

# 🔍 智能反洗钱检测系统 (AML Detection System)

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-1298%20passed-brightgreen.svg)](tests/)
[![Coverage](https://img.shields.io/badge/Coverage-83%25-brightgreen.svg)](tests/)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](docker-compose.yml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)

**基于 LangGraph + 多智能体协作的企业级反洗钱交易检测系统**

支持 10 种可疑模式识别 · LLM 深度审核 · STR 报告自动生成 · 参数自动优化闭环

[快速开始](#快速开始) · [在线演示](#在线演示) · [文档](docs/) · [贡献指南](CONTRIBUTING.md)

</div>

---

## 📸 产品预览

> **提示**：以下是功能截图占位符，部署后可替换为实际截图

<div align="center">

| Web 分析界面 | 报告生成 | API 文档 |
|:---:|:---:|:---:|
| ![Web界面](docs/screenshots/web-ui-placeholder.png) | ![报告](docs/screenshots/report-placeholder.png) | ![API](docs/screenshots/api-docs-placeholder.png) |
| Streamlit 可视化操作 | PDF/Excel 报告导出 | Swagger 交互文档 |

</div>

---

## ✨ 核心能力

### 🎯 10 条反洗钱规则引擎

| 规则 | 说明 | 场景 |
|------|------|------|
| 🔀 分拆转账 | 同一收款人多笔相近金额 | 规避大额上报 |
| ⚡ 快进快出 | 资金快速中转 | 洗钱通道 |
| 🔄 对敲交易 | 资金转出后原路返回 | 虚假交易 |
| 💰 大额交易 | 单笔超大额 | 异常资金 |
| 📊 基线偏离 | 交易行为偏离历史均值 | 行为突变 |
| 📝 备注关键词 | 敏感词匹配 | 隐蔽描述 |
| 🏢 空壳公司 | 多层嵌套股权 | 复杂架构 |
| 🚫 制裁名单 | 涉及制裁实体 | 合规风险 |
| 🌍 跨境异常 | 跨境交易模式异常 | 资金外逃 |
| ₿ 虚拟货币 | 类虚拟货币特征 | 新型洗钱 |

### 🤖 6 Agent 协作工作流

```
交易数据 → [数据预处理] → [规则引擎] → [图分析] → [LLM深审] → [报告生成] → [合规审核] → STR报告
```

- **数据预处理**：格式标准化、缺失值处理、异常检测
- **规则引擎**：10 条规则并行执行，双层证据链结构化
- **图分析**：NetworkX 关联分析，识别可疑链路
- **LLM 深度审核**：DeepSeek API 智能分析，降级评分一致性
- **报告生成**：智能主涉案方识别，多格式导出
- **合规审核**：业务戒律守护，审计日志完整

### 🔄 五阶段自动优化闭环

```
数据收集 → 参数评估 → 反馈调权 → 参数调优 → 验证推荐
```

- 反馈质量三层校验（格式 → 内容 → 一致性）
- 时间衰减权重（误报 90 天 / 漏报 365 天 / 确认 180 天）
- 多目标帕累托前沿优化
- A/B 测试 + 交叉影响分析

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- DeepSeek API Key（[免费申请](https://platform.deepseek.com/)）

### 1. 安装

```bash
git clone https://github.com/Weizikai-1/aml-detection-system.git
cd aml-detection-system
pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

### 3. 运行（三种方式）

**方式一：命令行** ⚡
```bash
python main.py                    # 模拟数据 + LLM 深审
python main.py --no-llm           # 离线模式（降级评分）
python main.py --file data.csv    # 分析自己的数据
```

**方式二：Web 界面** 🖥️
```bash
streamlit run app.py
# 浏览器打开 http://localhost:8501
```

**方式三：API 服务** 🔌
```bash
uvicorn api.main:app --reload --port 8000
# 打开 http://localhost:8000/docs 查看 Swagger 文档
```

### 4. Docker 一键部署 🐳

```bash
cp .env.example .env
# 编辑 .env，填入生产配置
docker compose up -d
```

包含：AML API + PostgreSQL + Redis + Nginx（HTTPS）

---

## 📊 测试覆盖

```bash
# 运行全部测试（1298 个用例）
python -m pytest

# 带覆盖率报告
python -m pytest --cov=./ --cov-report=term-missing
```

**当前指标**：
- ✅ 1298 个测试用例全部通过
- ✅ 代码覆盖率 83%
- ✅ 单元测试 + 集成测试 + E2E 测试 + 性能基准测试

---

## 🏗️ 项目架构

```
aml-detection-system/
├── agents/              # 智能体层（规则引擎/图分析/LLM审核/报告生成/合规审核）
├── api/                 # FastAPI 服务层（JWT认证/限流/审计日志）
├── graph/               # LangGraph 工作流编排
├── tools/               # 工具层（反馈管理/参数优化/A/B测试/交叉影响分析）
├── config/              # 配置模块（11个分类配置）
├── tests/               # 测试（单元/集成/E2E/基准）
├── deploy/              # 部署配置（Docker/Nginx）
├── docs/                # 文档
└── scripts/             # 工具脚本
```

---

## 📖 文档

- [部署指南](docs/DEPLOYMENT.md) - Docker Compose 生产部署
- [API 文档](docs/API.md) - REST API 接口说明
- [架构设计](docs/architecture.md) - 系统架构与技术选型
- [安全审计](docs/SECURITY_AUDIT.md) - 安全措施与合规检查
- [业务路线图](docs/business_roadmap.md) - 发展规划
- [贡献指南](CONTRIBUTING.md) - 如何参与贡献
- [变更日志](CHANGELOG.md) - 版本更新记录

---

## 🤝 贡献

欢迎贡献代码！请查看 [CONTRIBUTING.md](CONTRIBUTING.md) 了解如何参与。

### 贡献者

<a href="https://github.com/Weizikai-1/aml-detection-system/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Weizikai-1/aml-detection-system" alt="贡献者" />
</a>

---

## 📈 Star 历史

[![Star History Chart](https://api.star-history.com/svg?repos=Weizikai-1/aml-detection-system&type=Date)](https://star-history.com/#Weizikai-1/aml-detection-system&Date)

---

## 🛡️ 业务戒律

| 编号 | 戒律 | 说明 |
|------|------|------|
| **M1** | 真实数据 | 评估基于真实交易+真值集，不编造指标 |
| **M2** | 证据完整 | 评分≥60 的可疑交易证据链不为空 |
| **M3** | 评分范围 | 所有风险评分在 [0, 100] 范围内 |
| **M4** | 可追溯 | 全过程原子持久化，索引可查 |
| **P1** | 不遗漏 | 漏报反馈多时提高 recall 权重 |
| **P2** | 不误报 | 误报反馈多时提高 precision 权重 |

---

## 📄 许可证

[MIT License](LICENSE)

---

## ⚠️ 免责声明

本项目为技术演示和学习用途，不构成任何金融或法律建议。实际反洗钱合规请遵循当地监管要求并咨询专业人士。