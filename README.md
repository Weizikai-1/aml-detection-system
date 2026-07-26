# AML-Agent 反洗钱多智能体分析系统

基于 LangGraph 编排的多 Agent 反洗钱分析系统，模拟银行反洗钱团队从交易监控到可疑交易报告（STR）生成的完整流程。

## 解决什么问题

传统反洗钱系统依赖规则引擎，存在两个核心问题：

- **误报率高**：规则只能做到高召回，大量正常交易被标记为可疑，人工审核成本高
- **无法利用语义信息**：交易备注、时间规律、账户关系等非结构化信息被浪费

本系统的思路是 **规则初筛 + LLM 复核**：规则引擎负责高召回率地把可疑交易捞出来，LLM 负责高精度地过滤掉其中的误报。实测在 145 笔模拟交易上，规则引擎捞出 27 笔，LLM 过滤掉其中 29.6% 的误报。

## 系统架构

6 个 Agent 通过 LangGraph 的 StateGraph 编排，包含 2 个条件分支：

```
START
  │
  ▼
数据预处理 ── 清洗、去重、特征提取
  │
  ▼
规则引擎 ── 4 条规则初筛（分拆/快进快出/对敲/大额）
  │
  ├─ 无可疑 ──→ END
  │
  ▼
图分析 ── 构建资金流向图，社区发现检测团伙
  │
  ▼
LLM 深审 ── DeepSeek 逐笔复核，过滤误报
  │
  ├─ 全部误报 ──→ END
  │
  ▼
报告生成 ── 按主涉案账户生成 STR 报告
  │
  ▼
合规审核 ── 完整性/证据/风险等级校验，分流自动通过或人工审核
  │
  ▼
END
```

### 各 Agent 职责

| Agent | 职责 | 核心方法 |
|-------|------|---------|
| 数据预处理 | 去重、缺失值处理、金额分级、时间特征提取 | pandas 风格清洗 |
| 规则引擎 | 4 条 AML 规则初筛，滑动窗口/双指针算法 | 规则匹配 + 合并去重 |
| 图分析 | 构建资金流向有向图，Louvain 社区发现 | NetworkX 风格图算法 |
| LLM 深审 | 逐笔调用 DeepSeek 做语义分析，输出风险等级和置信度 | Prompt + JSON 解析 |
| 报告生成 | 按主涉案账户分组，生成结构化 STR 报告 | 模板渲染 |
| 合规审核 | 完整性、证据充分性、风险一致性、格式规范 4 项校验 | 加权评分 |

### 4 条反洗钱规则

| 规则 | 检测逻辑 | 典型场景 |
|------|---------|---------|
| 分拆转账 | 同一收款账户 1 小时内收到 ≥5 笔来自不同付款方、金额在 4-5 万之间的转账 | 规避 5 万大额报告线 |
| 快进快出 | 资金入账后 10 分钟内 ≥95% 金额转出 | 资金过路不留存 |
| 对敲交易 | 两个账户 7 天内互相转账，金额差异 ≤20% | 制造虚假交易量 |
| 大额交易 | 单笔交易 ≥10 万元 | 超过报告阈值 |

## 技术栈

| 类别 | 技术 | 说明 |
|------|------|------|
| 工作流编排 | LangGraph | StateGraph + add_edge + add_conditional_edges |
| LLM | DeepSeek API | 逐笔可疑交易语义分析，含降级模式 |
| 图分析 | 自研社区发现 | 简化版 Louvain，后续可接入 PyG 做 GNN |
| 数据处理 | Python 标准库 | 无外部依赖，便于部署 |
| 测试 | pytest | State 定义和规则引擎单测 |

## 项目结构

```
反洗钱/
├── main.py                     # 入口：生成数据 → 初始化LLM → 运行工作流
├── config.py                   # 配置：LLM参数、规则阈值、统一AML_CONFIG
├── .env                        # DeepSeek API Key（不入库）
│
├── agents/                     # 6 个 Agent，每个一个 create_xxx_agent(llm) 工厂函数
│   ├── data_preprocessor.py    # Agent 1：清洗 + 特征提取
│   ├── rule_engine.py          # Agent 2：4 条规则 + 合并去重
│   ├── graph_analyst.py        # Agent 3：建图 + 社区发现
│   ├── llm_reviewer.py         # Agent 4：DeepSeek 复核 + 降级模式
│   ├── report_generator.py     # Agent 5：STR 报告生成
│   └── compliance_auditor.py   # Agent 6：合规校验 + 分流
│
├── graph/                      # LangGraph 工作流
│   ├── state.py                # AMLState 定义（TypedDict，6 层字段）
│   ├── conditional_logic.py    # 2 个条件判断函数
│   ├── graph_setup.py          # GraphSetup：构建 StateGraph
│   └── workflow.py             # AMLAgentsGraph：主类，run() 执行
│
├── data/
│   └── data_generator.py       # 模拟交易生成器（5 种模式）
│
├── tools/
│   └── llm_client.py           # DeepSeek 客户端封装
│
├── tests/
│   └── test_state.py           # State 定义单测
│
└── reports/                    # 运行结果 JSON 输出
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，填入 DeepSeek API Key

# 3. 运行
python main.py
```

无 API Key 时系统会自动降级，规则引擎和图分析正常运行，LLM 深审改为基于规则评分的保守判断。

## 实际运行结果

145 笔模拟交易（120 正常 + 25 可疑）的实测数据：

| 阶段 | 结果 |
|------|------|
| 数据预处理 | 145 笔清洗完成，提取金额分级、时间特征 |
| 规则引擎 | 命中 27 笔（分拆 8 + 快进快出 8 + 对敲 6 + 大额 12，去重后 27） |
| 图分析 | 83 账户 / 141 资金路径，12 个社区，4 个可疑社区 |
| LLM 深审 | 确认 19 笔，过滤误报 8 笔（**误报过滤率 29.6%**） |
| 报告生成 | 25 份 STR 报告（9 critical + 11 high + 5 medium） |
| 合规审核 | 22 份自动通过 + 3 份需人工审核 |
| 总耗时 | 约 258 秒（含 27 次 DeepSeek API 调用） |

## 设计要点

### 为什么用工厂函数

每个 Agent 都是一个 `create_xxx_agent(llm) -> node_func` 工厂函数，直接传入 `StateGraph.add_node()`。这样 Agent 的创建和工作流的编排解耦，新增 Agent 只需写工厂函数 + 在 GraphSetup 里加节点和边。

### 为什么用条件边

规则引擎捞出的可疑交易可能为 0，LLM 复核后可能全是误报。这两种情况都没必要继续走完整流程。用 `add_conditional_edges` 在这两个点做判断，无可疑直接结束，节省计算和 API 调用。

### 为什么 LLM 深审要降级模式

DeepSeek API 可能因为网络、配额、模型升级等原因不可用。降级模式基于规则命中数和风险评分做保守判断（命中 ≥2 条规则或评分 ≥0.6 判为可疑），保证系统在无 LLM 时也能跑通全流程。

## 后续计划

- [ ] 图分析升级：接入 NetworkX 做 PageRank 和中心性分析
- [ ] GNN 扩展：用 PyTorch Geometric 做节点分类，替代社区发现
- [ ] Web 界面：Streamlit 可视化资金流向和分析结果
- [ ] 单元测试：补充规则引擎和图分析的测试覆盖
