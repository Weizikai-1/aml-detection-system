# AML 反洗钱多智能体检测系统

基于 **LangGraph** 构建的 6-Agent 并行协同反洗钱检测引擎，集成 **GNN 图神经网络**与 **DeepSeek LLM** 语义分析，覆盖 20 条检测规则，输出 STR 可疑交易报告。

> 数据来源: [Kaggle PaySim](https://www.kaggle.com/datasets/ntnu-testimon/paysim1) 636 万笔真实交易记录

---

## 架构

```
  START
    │
    ▼
  数据预处理 (PaySim 加载 + 特征提取 + Demo 注入)
    │
    ├──────────────────────────┐
    ▼                          ▼
  规则引擎 (20 条 YAML 规则)    GNN 图分析 (GAT / SAGE / GCN)
    │                          │
    └──────────┬───────────────┘
               ▼
        综合汇聚 (messages 通信总线)
               │
        ┌──────▼──────┐
        │ 高风险 ≥ 70? │  ← 条件路由
        ├──────┬──────┤
        ▼      ▼      │
   LLM 深审  报告生成  │
  (DeepSeek) (STR)    │
        │      │      │
        └──┬───┘◄─────┘
           ▼
       合规审核 (9 项央行格式 + 证据链 + 百分制评分)
           ▼
         END
```

**LangGraph State 设计**:

```python
class AMLState(TypedDict, total=False):
    transactions: List[dict]              # 交易数据
    rule_report: dict                     # {hits, summary, high_risk}
    gnn_report: dict                      # {node_f1, node_precision, node_recall}
    llm_reviews: List[dict]               # LLM 深审结果
    str_report: str                       # STR 报告
    compliance: dict                      # 合规结果
    # ── 并行安全 ──
    messages: Annotated[List[dict], add]  # Agent 通信总线
    current_step: Annotated[str, ...]     # 流程追踪
    errors: Annotated[List[str], add]     # 错误收集
```

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 DeepSeek API Key (可选，不影响基础检测)
echo "DEEPSEEK_API_KEY=sk-xxx" > .env

# 3. 运行检测
python main.py                        # 基础模式: 5000 条
python main.py --demo --n 500         # Demo 模式: 注入高风险样本
python main.py --demo --n 200         # 快速 Demo

# 4. 可视化界面
streamlit run app.py

# 5. API 服务
uvicorn api:app --port 8000
```

---

## 检测规则 (20 条, YAML 驱动)

| 规则 | 风险分 | 检测模式 |
|------|:---:|---|
| 分拆转账 | 70 | 同收款方短时间内多笔小额来自不同付款方 |
| 快进快出 | 60 | 资金快速转入后几乎全部转出 |
| 对敲交易 | 65 | 两账户短期内等额互转 |
| 大额交易 | 40 | 单笔超过阈值 |
| 基线偏离 | ≤60 | 交易金额偏离账户历史行为基线 |
| 备注关键词 | 55 | 高风险词库匹配（跑分/洗钱/套现等） |
| 空壳公司 | 75 | 对手高度分散 + 资金留存率极低 |
| 制裁名单 | 95 | OFAC / 央行关注名单命中 |
| 跨境交易 | 80 | 外币交易 + 高风险地区 |
| 虚拟货币 | 95 | OTC / 混币器 / 交易所关键词 |
| 循环转账 | 80 | A→B→C→A 多层资金循环 |
| 整数金额 | 45 | 频繁整齐金额交易 |
| 高频交易 | 60 | 短时间内大量交易 |
| 余额清空 | 65 | 交易后账户余额接近归零 |
| 类型跳跃 | 55 | 低风险类型突变为高风险类型 |
| 夜间异常 | 50 | 非营业时间高频交易 |
| 中介账户 | 70 | 频繁 A→X→B 资金过桥 |
| 金额聚类 | 55 | 多笔金额高度相似的结构化交易 |
| 交易量突变 | 60 | 交易量突然暴增 |
| 结构化交易 | 75 | 小额汇总规避大额报告阈值 |

新增规则只需修改 `config/rules/aml_rules.yaml` + 一个纯函数。

---

## 运行示例

```
$ python main.py --demo --n 200

============================================================
  AML 反洗钱多智能体检测系统
  架构: LangGraph 6-Agent 并行协同工作流
  模式: [Demo] 注入高风险样本
============================================================

[并行 (LangGraph)] 启动 6-Agent 工作流...

───────────────────────────────────────────────────────
  执行摘要
  ──────────────────────────────────────────────────
  数据: 200 条, 欺诈 0 (0.00%)
  来源: Kaggle PaySim (ntnu-testimon/paysim1)
  规则命中: 96 笔 (高:4 中:35 低:57)
  规则分布: large_amount:75, balance_drain:34, sanction_list:1, ...
  GNN: F1=1.0000 P=1.0000 R=1.0000
  LLM 深审: 4 笔
  Agent 链路: data_preprocess → rule_engine → graph_analyst
              → merge_analysis → llm_reviewer → report_generator → compliance
  ──────────────────────────────────────────────────

合规审核: 合规通过 (评分: 92/100)
报告已保存: reports\aml_report.md
```

---

## LLM 深审示例

> **制裁名单命中** (风险分 95) → 嫌疑等级: **high** → 恐怖融资
> *"付款方名称明确包含 SDN001_TERROR_FINANCE，直接命中制裁名单，具有明确的恐怖融资风险。交易备注要求'紧急快速处理'，符合洗钱或恐怖融资中规避审查的典型特征。"*

> **虚拟货币跑分** (风险分 95) → 嫌疑等级: **high** → 虚拟货币洗钱
> *"备注明确提及'USDT换汇-洗钱-跑分平台结算'，直接指向洗钱活动。资金从C8765432转至C2345678，符合跑分平台资金归集特征。"*

---

## 工程特性

- **并行安全**: `Annotated[List, add]` reducer 确保并行节点状态不丢失
- **LLM 容错**: 指数退避重试 (1.5s→3s→6s) + 30s 超时 + 速率限制 + Fallback 降级
- **延迟导入**: GNN 依赖在函数入口处延迟导入，无安装不阻塞模块加载
- **多架构 GNN**: GAT / GraphSAGE / GCN 三种架构可通过参数切换
- **合规评分**: 9 项央行格式检查 + 内容实质性 + 证据链完整性 + 百分制评分
- **Messages 总线**: 每个 Agent 追加结构化消息 (`{agent, timestamp, status, summary}`)，可追溯全链路

---

## 技术栈

| 层 | 技术 |
|---|---|
| 编排 | LangGraph StateGraph (并行 Super-step + 条件路由) |
| LLM | DeepSeek API (OpenAI 兼容, retry/timeout/fallback) |
| 图神经网络 | PyTorch Geometric (GAT / GraphSAGE / GCN) |
| 规则引擎 | YAML 配置驱动, 纯函数, 20 条规则 |
| 记忆 | JSONL 文件记忆库 (案例检索 + 反思注入) |
| API | FastAPI (`POST /detect` `GET /health` `GET /report/{id}`) |
| 界面 | Streamlit |
| 数据 | Kaggle PaySim (636 万笔) |
| 测试 | pytest, 32 用例 |

---

## 项目结构

```
├── main.py                  # CLI 入口
├── app.py                   # Streamlit 界面
├── api.py                   # FastAPI 接口
├── settings.py              # 配置 (自动加载 .env)
├── graph/
│   ├── state.py             # AMLState (TypedDict + Annotated reducer)
│   └── workflow.py          # 6-Agent 并行拓扑
├── agents/
│   ├── data_preprocess.py   # Agent 1: 数据预处理
│   ├── rule_engine_agent.py # Agent 2: 规则引擎
│   ├── graph_analyst.py     # Agent 3: GNN 图分析
│   ├── llm_reviewer.py      # Agent 4: LLM 深审
│   ├── report_generator.py  # Agent 5: STR 报告生成
│   ├── compliance.py        # Agent 6: 合规审核
│   └── demo_injector.py     # Demo 样本注入
├── rules.py                 # 20 条规则 (纯函数)
├── rule_engine.py           # 规则编排器
├── gnn_model.py             # GAT / GraphSAGE / GCN
├── data_loader.py           # PaySim 数据加载
├── evaluate.py              # 评估: 基线 vs 规则 vs GNN
├── llm/deepseek_client.py   # DeepSeek API (重试/超时/降级)
├── memory/chroma_store.py   # 反思记忆
├── config/rules/aml_rules.yaml  # 规则参数
└── tests/                   # 32 个测试
```

---

## 已知限制

- **数据**: PaySim 为学术数据集，缺少真实 KYC 信息、IP、地理位置等字段。GNN F1 在模拟数据上有效，真实场景需银行数据验证。
- **规则**: 20 条覆盖主要洗钱模式，银行生产环境通常 100+ 条。
- **实时性**: 当前为批量检测模式，未接入流处理与告警推送。
- **GNN 模型**: 当前聚焦节点分类，未引入时序图模型与异构图。

---

## License

MIT
