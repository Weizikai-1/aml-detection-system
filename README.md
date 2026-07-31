# AML 反洗钱多智能体检测系统

基于 **LangGraph StateGraph** 构建的 6-Agent 并行协同反洗钱检测系统。

**定位**: 求职技术 Demo — 展示 LangGraph 多 Agent 协作、LLM 集成、GNN 图分析能力。

---

## 架构

```
                    ┌──────────────────────────────────────┐
                    │       LangGraph StateGraph            │
                    │     AMLState (Annotated TypedDict)     │
                    └──────────────────────────────────────┘
                                       │
         ┌─────────────────────────────┤
         ▼                             ▼
  ┌──────────────┐            ┌──────────────┐
  │ 规则引擎 Agent│            │ GNN图分析Agent │  ← 并行 super-step
  │ 10条YAML规则  │            │ GAT 图注意力   │
  └──────┬───────┘            └──────┬───────┘
         │                           │
         └───────────┬───────────────┘
                     ▼
            ┌────────────────┐
            │  综合分析 merge  │
            └───────┬────────┘
                    │
            ┌───────▼────────┐
            │ 高风险 ≥70?     │  ← 条件路由
            ├───────┬────────┤
            ▼       ▼        │
     ┌──────────┐ ┌──────────┐
     │LLM深审Agent│ │报告生成Agent│
     │DeepSeek API│ │STR 报告   │
     └─────┬────┘ └─────┬────┘
           │            │
           └─────┬──────┘
                 ▼
          ┌──────────────┐
          │ 合规审核 Agent │
          │ 6项格式校验    │
          └──────┬───────┘
                 ▼
                END
```

## 6 个 Agent

| # | Agent | 职责 | 核心技术 |
|---|-------|------|----------|
| 1 | 数据预处理 | 加载 PaySim 数据、特征提取 | Pandas, PaySim 636万笔真实交易 |
| 2 | 规则引擎 | 10条反洗钱规则并行检测 | YAML 配置驱动, 纯函数 |
| 3 | GNN 图分析 | 构建资金流向图谱, 节点分类 | GAT 图注意力网络, F1=0.90 |
| 4 | LLM 深审 | 高风险交易语义分析 | DeepSeek API + 反思记忆 |
| 5 | 报告生成 | 汇总产出, 生成 STR 报告 | Markdown 模板 + LLM 建议 |
| 6 | 合规审核 | 央行格式校验 | 6项规则检查 |

## 10 条检测规则

| # | 规则 | 风险分 | 说明 |
|---|------|--------|------|
| 1 | 分拆转账 | 70 | 同收款方1h内≥5笔来自不同付款方 |
| 2 | 快进快出 | 60 | 10min内≥95%入账金额转出 |
| 3 | 对敲交易 | 65 | 两账户7天内互转, 金额差异≤20% |
| 4 | 大额交易 | 40 | 单笔≥10万 |
| 5 | 基线偏离 | 60 | 账户行为偏离历史基线(Z-score) |
| 6 | 备注关键词 | 55 | 高风险/低风险词库匹配 |
| 7 | 空壳公司 | 75 | 对手分散度+资金留存率判定 |
| 8 | 制裁名单 | 95 | OFAC/央行关注名单 |
| 9 | 跨境交易 | 80 | 外币/高风险地区检测 |
| 10 | 虚拟货币 | 95 | OTC/混币器/交易所关键词 |

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 基础检测 (5000条 PaySim 真实数据)
python main.py

# Demo 模式 (注入高风险样本, 展示完整 LLM 深审链路)
python main.py --demo --n 1000

# Streamlit 可视化界面
streamlit run app.py
```

## 运行示例

```
$ python main.py --demo --n 500

============================================================
  AML 反洗钱多智能体检测系统
  架构: LangGraph 6-Agent 并行协同工作流
  模式: 🧪 Demo (注入高风险样本)
============================================================

数据加载完成: 500 条, 来源: Kaggle PaySim (ntnu-testimon/paysim1)
规则引擎: 224 笔命中 (高:4 中:1 低:219)
规则分布: large_amount:222, remark_keywords:1, crypto_pattern:1,
           sanction_list:1, shell_company:1, smurfing:1, cross_border:1
路由: 4 笔高风险 → LLM 深审
STR 报告生成完成 (587 字符)
合规审核: 合规通过

────────────────────────────────────────
  执行摘要
  ────────────────────────────────────
  数据: 500 条, 欺诈 0 (0.00%)
  规则命中: 224 笔 (高:4 中:1 低:219)
  规则分布: 7 条不同规则触发
  LLM 深审: 4 笔高风险交易
  ────────────────────────────────────
```

## 技术栈

| 层次 | 技术 |
|------|------|
| 工作流编排 | LangGraph StateGraph (并行 + 条件路由) |
| LLM | DeepSeek API (OpenAI 兼容) |
| 图神经网络 | PyTorch Geometric, GAT |
| 数据 | Kaggle PaySim (636万笔真实交易) |
| 规则配置 | YAML (aml_rules.yaml) |
| 记忆 | 文件 JSONL 记忆库 (案例检索+反思) |
| 界面 | Streamlit |
| 测试 | pytest (26 用例, 全部通过) |

## 项目结构

```
反洗钱/
├── app.py                  # Streamlit 界面
├── main.py                 # CLI 入口
├── graph/
│   ├── state.py            # AMLState (Annotated TypedDict)
│   └── workflow.py         # LangGraph 并行工作流
├── agents/
│   ├── data_preprocess.py  # Agent 1: 数据预处理
│   ├── rule_engine_agent.py# Agent 2: 规则引擎
│   ├── graph_analyst.py    # Agent 3: GNN 图分析
│   ├── llm_reviewer.py     # Agent 4: LLM 深审
│   ├── report_generator.py # Agent 5: 报告生成
│   ├── compliance.py       # Agent 6: 合规审核
│   └── demo_injector.py    # 高风险 Demo 数据注入
├── llm/deepseek_client.py  # DeepSeek API 封装
├── memory/chroma_store.py  # 反思记忆
├── rules.py                # 10条检测规则 (纯函数)
├── rule_engine.py          # 规则编排器
├── gnn_model.py            # GAT 图神经网络
├── data_loader.py          # PaySim 数据加载
├── evaluate.py             # 评估入口
├── settings.py             # 集中配置
├── config/rules/aml_rules.yaml  # 规则参数
└── tests/                  # 26 个测试用例
```

## 与 TradingAgents 架构对标

| TradingAgents | AML 系统 |
|---|---|
| Analyst Team (4路并行) | 规则引擎 + GNN 并行 super-step |
| Researcher Team (多空辩论) | LLM 深审 + 历史案例反思 |
| Risk Management | 合规审核 (6项校验) |
| Trader + PM | 报告生成 (STR) |
| ConditionalLogic | `_route_by_risk()` 风险路由 |
| ChromaDB Memory | JSONL 文件记忆库 |
| LangGraph StateGraph | 完全一致 |

## 生产级差距 (诚实标注)

| 维度 | 当前 | 生产要求 |
|------|------|----------|
| 数据 | Kaggle PaySim 636万笔 | 真实银行交易流水 |
| 规则 | 10条覆盖主要场景 | 50+规则 + 监管合规审核 |
| GNN | GAT 节点分类, F1=0.90 | 异构图 + 时序图 + 在线学习 |
| LLM | DeepSeek 单次调用 | 多轮辩论 + 工具调用 |
| 部署 | 本地 Python | K8s + 实时流处理 |
| 可解释性 | 规则证据 + LLM 分析 | SHAP + 完整审计链路 |

## License

MIT
