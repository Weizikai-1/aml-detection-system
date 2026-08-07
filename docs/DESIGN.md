# 设计决策文档

> 本文档记录 AML 反洗钱多智能体检测系统的关键架构决策，便于代码审查和面试讨论。

---

## 1. 为什么选择 LangGraph 而非手写编排？

### 决策

使用 LangGraph `StateGraph` 的 `add_edge` + `add_conditional_edges` 声明式定义 6-Agent 拓扑，而非手写 Python 控制流。

### 理由

| 考量 | LangGraph | 手写控制流 |
|------|:---:|:---:|
| 并行执行 | `add_edge(a, b); add_edge(a, c)` 自动 fan-out → fan-in | 需手动 `ThreadPool` / `asyncio.gather` |
| 条件路由 | `add_conditional_edges` 声明式分支 | `if/else` 散落在调用链中 |
| 状态管理 | `Annotated[List, add]` reducer 自动处理并行写入冲突 | 需手动加锁或队列 |
| 可观测性 | 内置 `stream()` + checkpointer | 需自建日志/追踪 |
| 生产就绪 | LangChain 生态 + 社区维护 | 自研成本高 |

### 为什么不选 Airflow / Celery？

- **Airflow**: 面向 DAG 批处理调度，分钟级延迟，不适合检测流水线的秒级响应
- **Celery**: 面向任务队列，不内置状态共享和并行 super-step 语义

---

## 2. 为什么 6 个 Agent 而非更少或更多？

### Agent 职责拆分原则

1. **单一职责** — 每个 Agent 只做一件事，产出收敛到 State 中的一个字段
2. **并行安全** — 互不依赖的 Agent 放入同一 super-step，最大化并行度
3. **可替换性** — 每个 Agent 可独立替换实现（如规则引擎换成 XGBoost）

### 当前 6-Agent 拓扑

```
[数据预处理] → [规则引擎 ∥ GNN 图分析] → [汇聚] → [LLM 深审 → 报告] → [合规]
```

- **为什么规则引擎和 GNN 并行？** — 两者读取同一个 `transactions` 但写入不同字段（`rule_report` vs `gnn_report`），零数据竞争
- **为什么 LLM 深审在条件分支？** — 仅当 `high_risk` 非空时才触发，避免不必要的 API 调用和延迟
- **为什么合规在最后？** — 合规审核必须看到完整的 STR 报告才能做格式/内容校验

### 如果扩展到更多 Agent

可以考虑的拆分方向：
- **feature_engineer Agent** — 将当前 data_preprocess 中的特征工程独立出来
- **alert_router Agent** — 在合规之后，按风险等级路由到不同处理流程
- **feedback_collector Agent** — 收集人工标注反馈，更新规则权重

---

## 3. State 设计原则

### 核心约束

```
1. 单一写入者 — 每个 State 字段只由一个 Agent 写入
2. 类型收敛 — 每个字段是一个结构化容器（dict/list），不是扁平键
3. 并行安全 — 多 Agent 并发写的字段必须标注 Annotated reducer
```

### 关键设计决策

| 决策 | 理由 |
|------|------|
| `total=False` 的 `TypedDict` | 工作流渐进填充，初始 State 只需 `n_samples` + `demo_mode` |
| `rule_report` 三合一容器 `{hits, summary, high_risk}` | 避免三个独立字段的写入竞态，虽由同一 Agent 写入但语义内聚 |
| `messages` 使用 `operator.add` reducer | 并行 super-step 中多个 Agent 同时追加消息，必须保证不丢失 |
| `current_step` 使用 `_last_write` | 仅用于 UI 展示，取最后写入值即可 |

---

## 4. 容错策略

### LLM 调用（DeepSeekClient）

```
1. 指数退避重试: 1.5s → 3s → 6s (最多 3 次)
2. 超时保护: 单次调用 30s 上限
3. 速率限制: 连续调用间隔 ≥ 0.5s
4. Fallback 降级: 所有重试耗尽后返回 "[LLM Fallback: ...]" 文本，不抛异常
```

### GNN 延迟导入

```
- 模块导入不依赖 torch/torch-geometric
- gnn_model.py 仅在实例化 FraudGNN 时才 import torch_geometric
- is_available() 用于上游 Agent 预检，不可用时优雅跳过
```

### 串行回退（run_sequential）

```
LangGraph 未安装时，自动降级为纯 Python 串行执行
确保项目在最小依赖（numpy + pandas + pyyaml）下仍可运行
```

---

## 5. 规则引擎设计

### 为什么 YAML 驱动 + 纯函数？

| 考量 | 决策 |
|------|------|
| 可扩展性 | 新增规则只需 `aml_rules.yaml` + 一个纯函数，零侵入现有代码 |
| 可测试性 | 每条规则是独立纯函数 `List[dict] → List[dict]`，可单独测试 |
| 可配置性 | 阈值/关键词/风险分全部在 YAML，无需改代码 |
| 性能 | 20 条规则在 5000 条交易上执行时间 < 1s |

### 合并去重策略（_merge_and_rank）

```
同一笔交易可能被多条规则命中 → 按 _idx 去重
- 证据合并: 多条规则的 evidence 拼接
- 风险分取最大值: max(score1, score2, ...)
- 规则名去重: rules 列表去重
- 最终按 risk_score 降序输出
```

---

## 6. 合规审核设计

### 审核维度

1. **结构完整性** (9 项) — 参考央行《大额交易和可疑交易报告管理办法》必要章节
2. **内容实质性** — 报告长度、N/A 字段数量
3. **证据链完整性** — 付款方/收款方/金额/证据等关键信息是否填充
4. **风险评分合理性** — 命中数 vs 高风险数比例、命中率是否异常

### 评分机制

```
满分 100 分
- 每缺一个结构项: -8 分
- 内容实质性每项问题: -10 分
- 证据链每项缺失: -6 分
- 无内容: -30 分
```

---

## 7. 数据策略

### 诚实文档原则

- **真实数据优先**: `load_data()` 优先加载 `data/data_table.csv` (PaySim)
- **模拟数据降级**: 无真实数据时生成 PaySim 格式模拟数据，带 Ground Truth 标签
- **透明标注**: `get_source_label()` 返回当前数据来源，README 已知限制部分清晰声明

### 为什么不内置 PaySim 数据集？

- PaySim CSV 约 476MB，不适合随代码仓库分发
- 用户可通过 `scripts/sample.py` 一键采样为 50K 轻量样本
- 模拟数据已包含 `isFraud` 标签，可验证规则引擎和 GNN 逻辑

---

## 8. 技术选型总结

| 层次 | 选择 | 备选 | 选择理由 |
|------|------|------|----------|
| 编排 | LangGraph | Prefect/Dagster | 多 Agent 并行语义最自然 |
| LLM | DeepSeek | GPT-4/Claude | 中文金融场景理解好，API 兼容 OpenAI |
| GNN | PyTorch Geometric | DGL | 社区最大，GAT/SAGE/GCN 开箱即用 |
| API | FastAPI | Flask | 自动 OpenAPI 文档 + Pydantic 验证 |
| UI | Streamlit | Gradio | 更适合仪表盘和报告展示 |
| 记忆 | JSONL 文件 | ChromaDB/Redis | 零依赖，适合项目初期快速迭代 |
