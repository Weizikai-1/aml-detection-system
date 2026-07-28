# 系统架构文档

## 整体架构

AML-Agent 采用 LangGraph 状态机编排 6 个 Agent 协作完成反洗钱分析。

## Agent 职责

| Agent | 输入 | 输出 | 职责 |
|-------|------|------|------|
| 数据预处理 | 原始交易 | 清洗后交易+特征 | 数据清洗、特征提取 |
| 规则引擎 | 清洗后交易 | 可疑交易列表 | 规则初筛(高召回) |
| 图分析 | 清洗后交易 + 规则命中 | 资金图谱 + 团伙 + GNN 风险分 | NetworkX 图算法 + GCN 节点分类 |
| LLM 深审 | 可疑交易 | 确认可疑+误报 | 语义分析(高精度) |
| 报告生成 | 确认可疑 | STR 报告 | 生成央行格式报告 |
| 合规审核 | STR 报告 | 最终报告+人工任务 | 合规性检查 |

## State 设计

State 是所有 Agent 共享的数据结构,定义在 graph/state.py。

核心字段:
- transactions: 原始输入
- cleaned_transactions: 预处理后
- rule_hits: 规则命中
- graph_data: 图数据（含 PageRank / 介数中心性 / 社区发现 / GNN 节点分类）
- llm_confirmed: LLM 确认可疑
- str_reports: 生成的报告
- final_reports: 合规通过的报告

## 工作流

START -> 数据预处理 -> 规则引擎 -> [条件] -> 图分析 -> LLM 深审 -> 报告生成 -> 合规审核 -> END

条件分支:
- 规则引擎后:无可疑交易则直接结束
- LLM 深审后:全部误报则直接结束
