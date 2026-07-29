# 系统架构文档

## 整体架构

AML-Agent 采用 LangGraph 状态机编排 **7 个 Agent** 协作完成反洗钱分析，采用 **规则引擎 + 图神经网络 + LLM 语义分析** 三重检测体系。

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         AML-Agent 系统架构                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      多智能体编排层 (LangGraph)                  │   │
│  │                                                                 │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │   │
│  │  │数据预处理│→ │ 规则引擎 │→ │  图分析  │→ │ LLM 深审 │        │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │   │
│  │       │             │             │             │               │   │
│  │       │             │             │             │               │   │
│  │  ┌──────────────────────────────────────────────────────────┐   │   │
│  │  │                    条件分支逻辑                              │   │   │
│  │  │  · 规则引擎后: 无可疑 → END / 可疑 → 图分析                  │   │   │
│  │  │  · LLM深审后: 全误报 → END / 有确认 → 语义裁决               │   │   │
│  │  └──────────────────────────────────────────────────────────┘   │   │
│  │       │             │             │             │               │   │
│  │       ▼             ▼             ▼             ▼               │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐                     │   │
│  │  │ 语义裁决 │→ │ 报告生成 │→ │ 合规审核 │→ END                  │   │
│  │  └──────────┘  └──────────┘  └──────────┘                     │   │
│  │                                                                 │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                              ↓                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      三重检测体系                                │   │
│  │                                                                 │   │
│  │  Layer 1: 规则引擎 (Rule Engine)                                │   │
│  │  ├── 10 条反洗钱规则 (YAML 配置, 可热更新)                        │   │
│  │  ├── 滑动窗口检测算法                                            │   │
│  │  └── 输出: rule_hits (可疑交易列表)                             │   │
│  │                                                                 │   │
│  │  Layer 2: 图神经网络 (GNN)                                      │   │
│  │  ├── PaySim 数据集加载与图构建                                    │   │
│  │  ├── EdgeAwareGAT (边特征增强注意力机制)                         │   │
│  │  ├── 模型可解释性 (注意力权重可视化)                              │   │
│  │  └── 输出: gnn_scores (节点风险评分)                            │   │
│  │                                                                 │   │
│  │  Layer 3: LLM 语义分析 (Semantic Analysis)                       │   │
│  │  ├── 交易语义异常检测                                            │   │
│  │  ├── 混合裁决 (规则 × 0.40 + GNN × 0.35 + 语义 × 0.25)          │   │
│  │  └── 输出: adjudications (最终裁决结果)                         │   │
│  │                                                                 │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                              ↓                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      基础设施层                                  │   │
│  │                                                                 │   │
│  │  · YAML 规则配置 (RuleYAMLManager)                               │   │
│  │  · 自适应规则调优 (AdaptiveRuleTuner)                           │   │
│  │  · A/B 测试框架 (RuleABTest)                                    │   │
│  │  · 反思记忆系统 (MemoryManager + ReflectionEngine)                │   │
│  │  · 参数优化闭环 (Feedback → Evaluation → Optimization)            │   │
│  │  · 审计日志与数据血缘追踪                                        │   │
│  │                                                                 │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Agent 职责

| Agent | 输入 | 输出 | 职责 |
|-------|------|------|------|
| **数据预处理** | 原始交易 / PaySim 数据集 | 清洗后交易 + 特征 + paysim_features | 数据清洗、特征提取、PaySim 集成 |
| **规则引擎** | 清洗后交易 | 可疑交易列表 (rule_hits) | 10 条规则初筛 (YAML 配置) |
| **图分析** | 清洗后交易 + 规则命中 | 资金图谱 + 团伙 + GNN 风险分 | EdgeAwareGAT 推理、节点分类 |
| **LLM 深审** | 可疑交易 | 确认可疑 + 误报 | 语义分析 (高精度过滤) |
| **语义裁决** | LLM 审核结果 + GNN 分数 | 混合裁决 + 风险报告 | 规则+GNN+语义三重信号融合 |
| **报告生成** | 确认可疑 + 裁决结果 | STR 报告 | 生成结构化报告 |
| **合规审核** | STR 报告 | 最终报告 + 人工任务 | 合规性检查、评分 |

## State 设计

State 是所有 Agent 共享的数据结构, 定义在 `graph/state.py`。

核心字段分组:

```python
AMLState = {
    # Agent 1: 数据预处理
    "transactions": [...],           # 原始交易列表
    "cleaned_transactions": [...],   # 清洗后交易
    "paysim_features": {...},        # PaySim 数据集特征 (启用 EdgeAwareGAT)
    "preprocessing_stats": {...},    # 预处理统计

    # Agent 2: 规则引擎
    "rule_hits": [...],              # 规则命中列表
    "rule_hit_count": 0,             # 命中数

    # Agent 3: 图分析
    "graph_data": {
        "gnn_result": {...},         # GNN 推理结果 (含 scores)
        "gnn_model": "...",          # 使用的模型类型
        ...
    },

    # Agent 4: LLM 深审
    "llm_confirmed": [...],          # LLM 确认可疑交易
    "llm_false_positives": [...],    # LLM 判定误报
    "llm_reviewed": True,            # 是否完成审核

    # Agent 5: 语义裁决
    "semantic_results": [...],       # 语义异常检测结果
    "adjudications": [...],          # 混合裁决结果
    "risk_report": "...",            # 自然语言风险报告

    # Agent 6: 报告生成
    "str_reports": [...],            # STR 报告列表

    # Agent 7: 合规审核
    "final_reports": [...],          # 最终通过的报告
    "compliance_score": 0,           # 合规评分
}
```

## 工作流

```
START → 数据预处理 → 规则引擎 → [条件1] → 图分析 → LLM 深审 → [条件2] → 语义裁决 → 报告生成 → 合规审核 → END
```

### 条件分支逻辑

| 分支点 | 条件 | 下一节点 | 说明 |
|--------|------|----------|------|
| **条件1: 规则引擎后** | 有规则命中 (>0) | 图分析 | 继续深入分析 |
| | 无规则命中 | END | 无风险信号，流程结束 |
| | 规则引擎失败但有交易数据 | 图分析 | 降级处理（戒律 P1: 不遗漏） |
| **条件2: LLM深审后** | 有确认可疑交易 | 语义裁决 | 进入混合裁决 |
| | 全部误报 | END | 无可疑交易 |
| | LLM 未审核但有规则命中 | 语义裁决 | 降级处理（戒律 P1: 不遗漏） |

## 三重检测体系详解

### Layer 1: 规则引擎

10 条反洗钱规则，阈值通过 YAML 配置：

| 规则 | 检测目标 | 关键参数 |
|------|----------|----------|
| smurfing | 分拆转账 | hour_window, min_count, amount_range |
| fast_in_fast_out | 快进快出 | max_minutes, min_ratio |
| round_trip | 对敲交易 | max_days, max_amount_diff_ratio |
| large_amount | 大额交易 | threshold |
| baseline_deviation | 基线偏离 | multiplier |
| remark_keywords | 备注关键词 | keywords |
| shell_company | 空壳公司 | - |
| sanction_list | 制裁名单 | - |
| cross_border | 跨境异常 | - |
| crypto_pattern | 虚拟货币特征 | - |

### Layer 2: 图神经网络

- **同构图**: 所有账户作为节点，资金流动作为边
- **异构图**: 账户 + 交易作为两类节点，支持更丰富的关系建模
- **EdgeAwareGAT**: 边特征增强注意力机制，感知交易金额、时间等属性
- **可解释性**: 注意力权重可视化，分析哪些交易对风险评分贡献最大

### Layer 3: LLM 语义分析

混合裁决权重分配：
- 规则引擎: 0.40
- GNN 模型: 0.35
- 语义分析: 0.25

语义检测能力：
- 金额与备注不匹配检测
- 异常时间交易检测（凌晨/周末）
- 交易模式与账户画像偏离检测

## 配置管理

规则配置通过 YAML 文件管理，位于 `config/rules/aml_rules.yaml`。

### 配置层级

```
AML_CONFIG (硬编码默认值)
    ↓ 启动时加载
RuleYAMLManager (YAML 文件)
    ↓ 深合并覆盖
AML_CONFIG["rules"] (运行时生效值)
```

### 热更新机制

```python
from config.rules_yaml import RuleYAMLManager

manager = RuleYAMLManager()
manager.load()  # 首次加载

# 运行时热更新（检查文件 mtime，冷却时间 5 秒）
changed = manager.reload_if_needed()

# 动态修改
manager.update_rule("large_amount", {"threshold": 200000})
```

### 自适应调优

系统基于反馈数据自动优化规则参数：

```python
from config.rules_yaml import AdaptiveRuleTuner

tuner = AdaptiveRuleTuner(manager)
tuner.record_feedback(
    rule_name="smurfing",
    transaction_id="txn_001",
    is_correct=True,
    was_flagged=True,
    actual_fraud=True
)
suggestions = tuner.suggest_optimizations()  # 获取优化建议
tuner.apply_optimization("smurfing", suggestions[0])
```

## 错误处理与降级

系统遵循"安全降级"原则，确保任一组件失败不阻塞主流程：

| 组件 | 失败处理 |
|------|----------|
| 规则引擎 | 降级到图分析（若有交易数据） |
| GNN 模型 | 跳过，仅用规则引擎结果 |
| LLM API | 走降级路径，基于规则评分做保守判断 |
| YAML 配置 | 回退到硬编码默认值 |
| 数据库写入 | 异步重试 + 日志记录 |

## 扩展点

- **自定义规则**: 在 `agents/rule_engine.py` 添加新规则函数
- **自定义 GNN 模型**: 在 `tools/` 添加新模型，通过 `config/gnn.py` 配置
- **自定义 Agent**: 在 `agents/` 创建新 Agent，在 `graph/graph_setup.py` 注册到工作流
- **自定义 LLM**: 在 `config/llm.py` 配置不同 LLM Provider
