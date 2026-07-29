# 反洗钱多智能体检测系统

基于 LangGraph 多智能体架构的反洗钱交易检测系统，采用 **规则引擎 + 图神经网络 + LLM 语义分析** 三重检测体系，提供从数据预处理到 STR 报告生成的全流程自动化分析。

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-1288%20passed-brightgreen.svg)](tests/)
[![Coverage](https://img.shields.io/badge/Coverage-83%25-brightgreen.svg)](tests/)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](docker-compose.yml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)

## 核心特性

| 模块 | 技术实现 | 说明 |
|------|----------|------|
| **规则引擎** | 10 条反洗钱检测规则 | 分拆转账、快进快出、对敲交易等，阈值外置 YAML 动态配置 |
| **图分析** | PyTorch Geometric | GCN/GAT/GraphSAGE 三模型支持，注意力权重可解释性 |
| **边特征 GNN** | EdgeAwareGAT | 集成 PaySim 真实交易数据集，边特征增强模型感知交易属性 |
| **LLM 语义分析** | DeepSeek API | 交易语义异常检测，规则+GNN+语义混合裁决 |
| **YAML 规则配置** | RuleYAMLManager | 规则阈值外置、热更新、版本管理、自适应调优 |
| **多智能体编排** | LangGraph | 7 个 Agent 协作，支持条件分支、检查点恢复 |
| **参数优化闭环** | 反馈→评估→优化 | 时间衰减权重、多目标帕累托优化、A/B 测试 |
| **反思记忆系统** | 四类长期记忆 | 相似度检索、误报/漏报模式反思、规则调优建议 |
| **报告生成** | STR 报告 | 结构化报告、PDF/Excel 导出、资金流向可视化 |

## 三重检测体系

```
┌─────────────────────────────────────────────────────────────┐
│                    反洗钱三重检测体系                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Layer 1: 规则引擎 (Rule Engine)                            │
│  ├── 10 条反洗钱规则 (YAML 配置, 可热更新)                    │
│  ├── 滑动窗口检测算法                                        │
│  └── 输出: rule_hits (可疑交易列表)                         │
│                           ↓                                  │
│  Layer 2: 图神经网络 (GNN)                                   │
│  ├── 资金流向图构建 (同构图 / 异构图)                         │
│  ├── EdgeAwareGAT (边特征增强注意力机制)                     │
│  ├── 模型可解释性 (注意力权重可视化)                          │
│  └── 输出: gnn_scores (节点风险评分)                        │
│                           ↓                                  │
│  Layer 3: LLM 语义分析 (Semantic Analysis)                   │
│  ├── 交易语义异常检测 (金额-备注不匹配, 异常时间交易)         │
│  ├── 混合裁决 (规则 × 0.40 + GNN × 0.35 + 语义 × 0.25)      │
│  ├── 自然语言风险报告生成                                    │
│  └── 输出: adjudications (最终裁决结果)                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 工作流程

```
数据预处理 → 规则引擎 → 图分析 → LLM深审 → 语义裁决 → 报告生成 → 合规审核
    │            │          │         │          │          │          │
    │            │          │         │          │          │          │
    └────────────┴──────────┴─────────┴──────────┴──────────┘
                         LangGraph 多智能体编排
              (条件分支: 无可疑/全误报时提前结束)
```

| Agent | 职责 |
|-------|------|
| 数据预处理 | 缺失值处理、特征工程、PaySim 数据集加载 |
| 规则引擎 | 10 条规则检测，滑动窗口匹配 |
| 图分析 | 资金流向图构建，GNN 推理，风险评分 |
| LLM 深审 | 深度语义分析，过滤误报，输出高置信度可疑交易 |
| **语义裁决** | 规则+GNN+语义混合裁决，生成自然语言风险报告 |
| 报告生成 | STR 报告生成，PDF/Excel 导出 |
| 合规审核 | 最终合规检查，评分，历史记忆参考 |

## 快速开始

### 环境要求

- Python 3.10+
- DeepSeek API Key（[免费申请](https://platform.deepseek.com/)）

### 安装运行

```bash
# 克隆项目
git clone https://github.com/Weizikai-1/aml-detection-system.git
cd aml-detection-system

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

启动方式有三种：

**命令行**

```bash
# 使用模拟数据（运行完整演示）
python scripts/run_deep_aml_demo.py

# 使用模拟数据（简单模式）
python main.py

# 离线模式（不走 LLM）
python main.py --no-llm

# 分析自己的数据
python main.py --file your_data.csv
```

**Web 界面**

```bash
streamlit run app.py
# 打开 http://localhost:8501
```

**API 服务**

```bash
uvicorn api.main:app --reload --port 8000
# 打开 http://localhost:8000/docs 查看接口文档
```

**Docker 部署**

```bash
cp .env.example .env
# 编辑 .env 填入生产配置
docker compose up -d
```

## 配置管理

系统支持 YAML 规则配置，编辑 `config/rules/aml_rules.yaml` 即可动态调整规则参数，无需修改代码。

```yaml
# 示例: 调整大额交易阈值
large_amount:
  enabled: true
  threshold: 200000        # 修改阈值
  report_only: true
  risk_score: 60

# 示例: 调整混合裁决权重
adjudication_weights:
  rule_engine: 0.40        # 规则引擎权重
  gnn_model: 0.35          # GNN 模型权重
  semantic_analysis: 0.25  # 语义分析权重
```

### 规则热更新

系统支持运行时热更新规则，修改 YAML 文件后自动生效（无需重启）：

```python
from config.rules_yaml import RuleYAMLManager

manager = RuleYAMLManager()
manager.load()  # 加载规则
manager.reload_if_needed()  # 热更新（检查文件变更）
manager.update_rule("large_amount", {"threshold": 300000})  # 动态更新
```

### 自适应调优

系统可基于历史检测数据自动优化规则参数：

```python
from config.rules_yaml import AdaptiveRuleTuner

tuner = AdaptiveRuleTuner(manager)
tuner.record_feedback("smurfing", "txn_001", is_correct=True, was_flagged=True, actual_fraud=True)
suggestions = tuner.suggest_optimizations()  # 获取优化建议
tuner.apply_optimization("smurfing", suggestions[0])  # 应用优化
```

## 运行演示

运行完整集成演示，验证所有模块协作：

```bash
python scripts/run_deep_aml_demo.py
```

演示内容包括：
1. 加载 PaySim 模拟数据（如无真实数据集）
2. 数据预处理与特征工程
3. 10 条规则检测
4. GNN 图构建与推理（EdgeAwareGAT）
5. LLM 深审与语义裁决
6. STR 报告生成
7. YAML 规则管理演示

## 测试

项目包含 1288+ 个测试用例，覆盖所有核心模块：

```bash
# 运行全部测试
python -m pytest

# 运行特定模块测试
python -m pytest tests/unit/agents/test_rule_engine.py
python -m pytest tests/unit/tools/test_gnn_edge_model.py
python -m pytest tests/unit/config/test_rules_yaml.py

# 带覆盖率报告
python -m pytest --cov=./ --cov-report=html
```

## 项目结构

```
aml-detection-system/
├── agents/                          # 智能体层
│   ├── rule_engine.py               # 规则引擎 Agent (10条规则)
│   ├── graph_analyst.py             # 图分析 Agent (GNN推理)
│   ├── llm_reviewer.py              # LLM 深审 Agent
│   ├── llm_semantic_analyzer.py     # LLM 语义裁决 Agent (新增)
│   ├── report_generator.py          # 报告生成 Agent
│   ├── compliance_auditor.py        # 合规审核 Agent
│   └── data_preprocessor.py         # 数据预处理 Agent
├── api/                             # FastAPI 服务层
├── config/                          # 配置模块
│   ├── rules/aml_rules.yaml         # YAML 规则配置 (权威来源)
│   ├── rules_yaml.py                # YAML 规则管理器
│   ├── aml_config.py                # 统一配置入口
│   └── ...                          # 其他配置
├── graph/                           # LangGraph 工作流编排
│   ├── workflow.py                  # 主工作流类
│   ├── graph_setup.py               # 图构建配置
│   ├── state.py                     # 状态定义
│   └── conditional_logic.py         # 条件分支逻辑
├── tools/                           # 工具层
│   ├── dataset_builder.py           # PaySim 数据集构建 (新增)
│   ├── gnn_edge_model.py            # 边特征 GNN 模型 (新增)
│   ├── gnn_model.py                 # 标准 GNN 模型
│   ├── gnn_explainer.py             # GNN 可解释性分析
│   ├── reflection_engine.py        # 反思引擎
│   ├── memory_manager.py            # 记忆管理器
│   ├── rule_tuner.py                # 规则调优器
│   └── ...                          # 其他工具
├── tests/                           # 测试
│   ├── unit/                        # 单元测试 (1288+ 用例)
│   ├── integration/                 # 集成测试
│   ├── e2e/                         # 端到端测试
│   └── benchmarks/                  # 性能基准测试
├── scripts/                         # 脚本
│   ├── run_deep_aml_demo.py         # 集成演示脚本
│   └── ...
├── docs/                            # 文档
└── deploy/                          # 部署配置
```

## 业务戒律

系统在代码层面强制保证以下规则（可在日志中搜索 `[戒律]` 标签）：

| 编号 | 规则 | 说明 |
|------|------|------|
| **M1** | 真实数据 | 评估基于真实交易和真值集，不编造指标；YAML 默认值来自代码硬编码真实值 |
| **M2** | 证据完整 | 评分≥60 的可疑交易必须有完整证据链 |
| **M3** | 评分范围 | 所有风险评分在 [0, 100] 范围内 |
| **M4** | 可追溯 | 全过程原子持久化，索引可查 |
| **P1** | 不遗漏 | 规则引擎/LLM 失败时降级处理，不阻塞主流程 |
| **P2** | 不误报 | 存疑时结合交易背景综合判断 |
| **P3** | 混合裁决 | 规则+GNN+语义三重信号加权裁决 |

## 依赖项

核心依赖：
- `langgraph` - 多智能体工作流编排
- `fastapi` - Web API 服务
- `pytorch-geometric` - 图神经网络
- `streamlit` - Web 可视化界面
- `pyyaml` - YAML 配置管理
- `pandas`, `numpy` - 数据处理

## 许可证

MIT License

## 免责声明

本项目为技术演示和学习用途，不构成任何金融或法律建议。实际反洗钱合规请遵循当地监管要求并咨询专业人士。
