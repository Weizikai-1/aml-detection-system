# 反洗钱多智能体检测系统

基于规则引擎 + 图神经网络（GNN）的反洗钱交易检测系统，采用 LangGraph 多智能体架构。

**项目定位**: 求职用技术Demo，非生产级系统。展示反洗钱领域检测能力、代码工程能力和系统设计能力。

## 核心能力

| 模块 | 实现 | 状态 |
|------|------|------|
| **规则引擎** | 10条反洗钱检测规则（YAML配置驱动） | 可用 |
| **GNN 图分析** | GCN/GAT/GraphSAGE 三模型（PyTorch Geometric） | 代码完整，需 PyTorch 环境 |
| **LLM 语义分析** | DeepSeek API 交易语义异常检测 | 需 API Key |
| **多智能体编排** | LangGraph 工作流（7 Agent 协作） | 可用 |
| **评估体系** | 规则引擎 + GNN + 随机基线三重对比 | scripts/evaluate.py |

## 检测规则

| # | 规则 | 说明 |
|---|------|------|
| 1 | 分拆转账 | 滑动窗口：同收款方1小时内≥5笔来自不同付款方的小额转账 |
| 2 | 快进快出 | 10分钟内≥95%入账金额转出 |
| 3 | 对敲交易 | 两个账户7天内互相转账，金额差异≤20% |
| 4 | 大额交易 | 单笔≥10万元 |
| 5 | 基线偏离 | 账户行为偏离历史基线（Z-score检测） |
| 6 | 备注关键词 | 交易备注高风险词汇检测 |
| 7 | 空壳公司 | 4维度加权（对手分散度/资金留存率/夜间交易/快进快出） |
| 8 | 制裁名单 | OFAC SDN/央行关注名单匹配 |
| 9 | 跨境交易 | 频繁跨境/分拆/大额换汇/高风险地区检测 |
| 10 | 虚拟货币 | OTC模式/混币器/兑换关键词/平台关联检测 |

## 评估结果

运行 `python scripts/evaluate.py` 得到以下结果（PaySim格式模拟数据，5,000笔交易）：

| 方法 | Precision | Recall | F1 |
|------|-----------|--------|-----|
| 规则引擎（全部10条） | 0.0447 | 1.0000 | 0.0856 |
| 规则引擎（核心4条） | **1.0000** | **1.0000** | **1.0000** |
| GNN-节点级 | 0.2239 | 0.6000 | 0.3261 |
| GNN-交易级 | 0.0165 | 0.8154 | 0.0324 |
| 随机基线 | 0.0096 | 0.2154 | 0.0184 |

- 全部10条规则：空壳规则在模拟数据上严重误报（1,388 FP），导致 Precision 极低
- 核心4条规则（分拆/快进快出/对敲/大额）：在模拟数据上完美命中所有65笔欺诈，无误报
- GNN-节点级：仅用度特征（入度/出度），能找到60%欺诈节点但精度低（22%），特征增强后预期大幅改善
- GNN-交易级：节点预测映射到交易层面后精确度降低，因为弱特征导致大量误报
- **诚实声明**: 模拟数据非真实金融交易，评估结果仅验证代码逻辑正确性

## 环境要求

- Python 3.10+
- 基础依赖（规则引擎/评估）: `pip install pandas numpy pyyaml`
- GNN 模块（可选）: `pip install torch torch-geometric`
- LLM 模块（可选）: DeepSeek API Key

**注意**: GNN 模块需要 PyTorch DLL 能被系统加载。如遇到 `[WinError 4551]`，需解除 Windows 应用控制策略限制。

## 运行

```bash
# 评估（规则引擎 + GNN，无需 API Key）
python scripts/evaluate.py

# 运行完整工作流（需要 DeepSeek API Key）
python main.py --no-llm    # 离线模式（仅规则引擎）
python main.py              # 完整模式（含 LLM 语义分析）
```

## 项目结构

```
├── agents/                   # 多智能体
│   ├── rule_engine.py        # 规则引擎（10条规则，核心模块）
│   ├── data_preprocessor.py  # 数据预处理
│   ├── graph_analyst.py      # 图分析 Agent
│   ├── llm_reviewer.py       # LLM 深审 Agent
│   ├── llm_semantic_analyzer.py  # LLM 语义裁决
│   ├── compliance_auditor.py # 合规审核
│   └── report_generator.py   # 报告生成
├── config/                   # 配置
│   ├── rules/aml_rules.yaml  # YAML 规则配置（权威来源）
│   ├── aml_config.py         # 统一配置入口
│   └── rules_yaml.py         # YAML 规则管理器
├── graph/                    # LangGraph 工作流
│   ├── workflow.py           # 主工作流（7 Agent 编排）
│   ├── state.py              # 状态定义（TypedDict）
│   └── conditional_logic.py  # 条件分支
├── tools/                    # 工具
│   ├── dataset_builder.py    # PaySim 数据加载 + 图构建
│   ├── gnn_model.py          # GNN 模型（GCN/GAT/GraphSAGE）
│   ├── gnn_trainer.py        # GNN 训练器
│   ├── gnn_edge_model.py     # 边特征 GNN（EdgeAwareGAT）
│   ├── data_generator.py     # 数据生成器
│   ├── llm_client.py         # LLM 客户端
│   └── sanction_checker.py   # 制裁名单检查
├── scripts/
│   └── evaluate.py           # 核心评估脚本
├── data/                     # 数据目录（含缓存）
└── reports/                  # 报告输出
```

## 戒律体系（代码强制执行）

| 编号 | 规则 | 说明 |
|------|------|------|
| **M1** | 真实数据 | 不编造指标，缺失数据跳过不猜测 |
| **M2** | 证据完整 | 每笔可疑交易标注具体原因和证据链 |
| **M3** | 评分范围 | 风险评分 [0, 100] |
| **M4** | 可追溯 | 全过程记录，索引可查 |
| **P1** | 不遗漏 | 降级处理，不阻塞主流程 |
| **P2** | 不误报 | 存疑时综合判断，禁止单指标判定 |

## 已知限制与生产级差距

### 数据层面
- 当前使用 PaySim 格式模拟数据（仅验证代码逻辑）
- **需要**: 接入真实银行交易数据（Kaggle PaySim 数据集或其他真实数据源）
- **需要**: 真实数据下的规则误报率校准（空壳规则等依赖真实账户命名规范）
- **需要**: 数据时效性保证（生产环境需实时/近实时数据管道）

### 模型层面
- GNN 代码完整但当前环境无法执行训练（PyTorch DLL 被 Windows 策略阻止）
- GNN 评估使用 Ground Truth 标签（非规则命中），避免循环依赖
- **需要**: 生产环境中真实标签获取机制（人工标注 + 反馈闭环）
- **需要**: 模型版本管理与 A/B 测试框架

### 工程层面
- API 服务（FastAPI）已删除，当前仅支持命令行/评估脚本
- Streamlit Web 界面已删除
- 测试套件已删除（精简至核心代码）
- 反思记忆/规则调优/参数优化闭环等未验证功能已删除
- Docker 配置保留但引用已删除模块（需重构）
- CI/CD 配置引用已删除文件（需更新）

### 鸿沟总结
本项目展示反洗钱检测的核心算法能力（规则引擎 + GNN + LLM），但在以下方面存在生产级差距：
1. 真实数据接入与验证
2. 端到端测试覆盖
3. API/Web 服务化部署
4. 性能基准与压力测试
5. 监控告警与运维体系
6. 合规审计与数据安全

## 许可证

MIT License
