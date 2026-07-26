"""
LangGraph State 定义 - 反洗钱多Agent系统共享状态

设计原则:
- 类型安全: 使用 TypedDict 明确定义所有字段类型
- 易于扩展: 模块化设计，新增Agent只需添加对应字段
- 最小化冗余: 各Agent产出独立存储，不重复保存原始数据
- 清晰明确: 按阶段分组，字段语义清晰
- 完整覆盖: 包含从原始输入到最终报告的全链路数据
"""
from typing import TypedDict, Annotated, List, Dict, Any, Optional, Literal
from langgraph.graph.message import add_messages


# ============================================================
# 基础数据结构
# ============================================================

class Transaction(TypedDict, total=False):
    """
    单笔交易数据结构
    覆盖原始输入 + 清洗后扩展字段
    """
    # 核心字段
    transaction_id: str          # 交易唯一ID
    from_account: str            # 付款账户
    to_account: str              # 收款账户
    amount: float                # 交易金额
    timestamp: str               # 交易时间(ISO格式)
    transaction_type: str        # 交易类型(transfer/payment/cash_out等)
    remark: str                  # 交易备注

    # 扩展字段(数据预处理后填充)
    amount_level: str            # 金额等级(low/medium/high/very_high)
    is_weekend: bool             # 是否周末交易
    is_night: bool               # 是否夜间交易(22:00-06:00)
    from_account_risk: str       # 付款方风险等级
    to_account_risk: str         # 收款方风险等级

    # 风险标记
    is_suspicious: Optional[bool]        # 是否可疑
    suspicious_reason: Optional[str]     # 可疑原因
    risk_score: Optional[float]          # 风险评分(0-1)


class SuspiciousTransaction(TypedDict, total=False):
    """
    可疑交易(规则引擎/图分析/LLM深审的产出)
    包含证据链、命中规则、风险评分
    """
    transaction: Transaction           # 关联的原始交易
    rule_hits: List[str]               # 命中的规则列表
    risk_score: float                  # 综合风险评分(0-1)
    evidence: List[str]                # 证据链(文本描述)
    graph_evidence: Optional[str]      # 图分析补充证据
    llm_analysis: Optional[str]        # LLM分析结论
    llm_confidence: Optional[float]    # LLM置信度
    is_false_positive: Optional[bool]  # 是否误报(LLM判定)
    community_id: Optional[str]        # 所属团伙ID


class STRReport(TypedDict, total=False):
    """
    可疑交易报告 (Suspicious Transaction Report)
    符合央行反洗钱报告格式
    """
    report_id: str                     # 报告编号
    report_date: str                   # 报告生成日期
    report_type: str                   # 报告类型(初始/补充/复核)

    # 主体信息
    primary_account: str               # 主涉案账户
    related_accounts: List[str]        # 关联账户列表
    customer_profile: Dict[str, Any]   # 客户画像信息

    # 可疑信息
    suspicious_transactions: List[SuspiciousTransaction]  # 可疑交易列表
    total_suspicious_amount: float     # 可疑交易总金额
    suspicious_patterns: List[str]     # 可疑模式描述
    risk_level: str                    # 整体风险等级(low/medium/high/critical)

    # 分析结论
    analysis_summary: str              # 分析摘要
    evidence_chain: List[str]          # 完整证据链
    disposal_suggestion: str           # 处置建议

    # 审核信息
    compliance_status: str             # 合规状态(pending/passed/rejected)
    compliance_notes: Optional[str]    # 合规备注
    reviewer: Optional[str]            # 审核人
    final_decision: Optional[str]      # 最终结论


class GraphData(TypedDict, total=False):
    """
    图分析中间数据
    """
    nodes: List[Dict[str, Any]]        # 节点(账户)
    edges: List[Dict[str, Any]]        # 边(交易)
    node_count: int                    # 节点数
    edge_count: int                    # 边数
    communities: List[List[str]]       # 社区划分结果
    suspicious_communities: List[Dict[str, Any]]  # 可疑社区详情
    node_risk_scores: Dict[str, float]  # 节点风险评分
    graph_stats: Dict[str, Any]        # 图统计指标


# ============================================================
# 主状态: AMLState
# ============================================================

class AMLState(TypedDict, total=False):
    """
    反洗钱多Agent系统共享状态

    数据流向: 输入 → 预处理 → 规则引擎 → 图分析 → LLM深审 → 报告生成 → 合规审核
    每个Agent只读取自己需要的字段，写入自己负责的字段
    """

    # ===== 输入层 =====
    transactions: List[Transaction]        # 原始交易流水
    analysis_date: str                     # 分析日期
    analysis_params: Dict[str, Any]        # 分析参数(自定义阈值等)

    # ===== Agent 1: 数据预处理 =====
    cleaned_transactions: List[Transaction]   # 清洗后的交易
    transaction_features: Dict[str, Any]      # 全局统计特征
    preprocessing_stats: Dict[str, int]       # 预处理统计(去重数、缺失值数等)

    # ===== Agent 2: 规则引擎 =====
    rule_hits: List[SuspiciousTransaction]    # 规则命中的可疑交易
    rule_hit_count: int                       # 命中交易数
    rule_details: Dict[str, int]              # 各规则命中数
    rule_engine_stats: Dict[str, Any]         # 规则引擎统计

    # ===== Agent 3: 图分析 =====
    graph_data: GraphData                     # 图数据与分析结果
    graph_suspicious: List[SuspiciousTransaction]   # 图分析新增可疑
    graph_hit_count: int                      # 图分析命中数

    # ===== Agent 4: LLM 深审 =====
    llm_reviewed: List[SuspiciousTransaction]     # LLM 审核后的可疑
    llm_confirmed: List[SuspiciousTransaction]    # LLM 确认可疑
    false_positives: List[SuspiciousTransaction]  # LLM 判定误报
    llm_analysis_count: int                       # LLM 分析数量
    llm_stats: Dict[str, Any]                     # LLM 统计

    # ===== Agent 5: 报告生成 =====
    str_reports: List[STRReport]             # 生成的 STR 报告
    report_count: int                        # 报告数量
    report_generation_stats: Dict[str, Any]  # 报告生成统计

    # ===== Agent 6: 合规审核 =====
    final_reports: List[STRReport]           # 合规通过的最终报告
    rejected_reports: List[STRReport]        # 被驳回的报告
    human_review_tasks: List[Dict[str, Any]]  # 需人工审核的任务
    compliance_stats: Dict[str, Any]         # 合规统计
    compliance_summary: str                  # 合规审核摘要

    # ===== 控制层 =====
    messages: Annotated[list, add_messages]  # 消息历史(Agent间通信)
    current_step: str                         # 当前步骤
    error: str                                # 错误信息(如有)
    total_processing_time: float              # 总处理时间(秒)
    step_times: Dict[str, float]              # 各步骤耗时
    execution_id: str                         # 执行ID(用于追踪)
