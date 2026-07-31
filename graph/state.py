"""
LangGraph State — 多 Agent 共享状态定义
参照 TradingAgents 标准设计，遵循 LangGraph 最佳实践

设计原则:
  1. messages 总线 —— 所有 Agent 产出的标准化载体，Annotated + add reducer
  2. 类型化容器 —— 每个 Agent 的产出收敛到单个结构化字段
  3. 并行安全 —— 仅控制字段使用 Annotated reducer，数据字段由单一 Agent 写入
  4. 最小化冗余 —— 不在节点间重复复制 State 中已有的字段

State 生命周期:
  ┌─ 输入层 (main.py 初始化) ──────────────────────┐
  │  n_samples, demo_mode                           │
  └─────────────────────────────────────────────────┘
         │
  ┌─ Agent 产出层 (各 Agent 增量写入) ───────────────┐
  │  data_preprocess  → transactions, data_summary   │
  │  rule_engine      → rule_report                 │
  │  graph_analyst    → gnn_report, gnn_enabled     │
  │  llm_reviewer     → llm_reviews, llm_enabled    │
  │  report_generator → str_report                  │
  │  compliance       → compliance                  │
  └─────────────────────────────────────────────────┘
         │
  ┌─ 通信控制层 (并行安全: Annotated + reducer) ─────┐
  │  messages     → AgentMessage 总线 (add reducer)  │
  │  current_step → 流程追踪 (last-write-wins)       │
  │  errors       → 错误收集 (add reducer)           │
  └─────────────────────────────────────────────────┘
"""
from typing import TypedDict, List, Annotated, Optional, Any
from operator import add


# ============================ 类型别名（提升可读性） ============================

Transaction = dict            # 单条交易记录
Hits = List[dict]             # 规则命中列表
ReviewResults = List[dict]    # LLM 审核结果列表


# ============================ Reducer 函数 ============================

def _last_write(_current: Any, new: Any) -> Any:
    """并行节点写同一标量字段时，取最后写入的值（默认行为）"""
    return new


# ============================ State 定义 ============================

class AMLState(TypedDict, total=False):
    """
    反洗钱多智能体系统全局状态

    约定:
      - 所有字段 total=False（可选），工作流渐进填充
      - 字段命名: snake_case，Agent 产出统一用 *_report / *_reviews 后缀
      - 每个 Agent 只写自己名下的字段（单一写入者原则）
      - 多 Agent 并发写的字段必须标注 Annotated reducer
    """

    # ── 输入配置（外部传入，工作流初始化时设置） ──
    n_samples: int                    # 分析数据量
    demo_mode: bool                   # 是否注入高风险 Demo 样本

    # ── 数据层（data_preprocess Agent 独占写入） ──
    transactions: List[Transaction]   # 交易数据载荷
    data_summary: dict                # 统计概览 {total, fraud, fraud_rate, types}
    data_source: str                  # 数据来源标注
    preprocess_ok: bool               # 预处理是否成功

    # ── 规则检测产出（rule_engine Agent 独占写入） ──
    rule_report: dict                 # {hits, summary, high_risk} 三合一容器

    # ── 图分析产出（graph_analyst Agent 独占写入） ──
    gnn_report: dict                  # {node_f1, node_precision, node_recall}
    gnn_enabled: bool                 # GNN 是否可用

    # ── LLM 深审产出（llm_reviewer Agent 独占写入） ──
    llm_reviews: ReviewResults        # LLM 审核结果列表
    llm_enabled: bool                 # LLM 是否可用

    # ── 报告 + 合规产出（report_generator + compliance 独占写入） ──
    str_report: str                   # 最终 STR 报告文本
    compliance: dict                  # 合规审核结果 {passed, issues, status}

    # ── 通信控制层（并行安全: Annotated + 明确 reducer） ──
    messages: Annotated[List[dict], add]
    """Agent 间通信总线。每个 Agent 完成后追加一条结构化消息:
       {"agent": "rule_engine", "timestamp": "2024-...", "summary": "...", "status": "ok"}
       使用 operator.add 确保并行节点的消息全部保留，不会互相覆盖。
    """

    current_step: Annotated[str, _last_write]
    """当前执行阶段名。并行节点取最后写入值，仅用于 UI 展示和日志。"""

    errors: Annotated[List[str], add]
    """错误收集列表。并行节点的错误通过 add reducer 拼接，防止丢失。"""
