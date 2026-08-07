"""
数据模型 — Pydantic 类型定义

为 Agent 间共享的数据结构提供类型约束和文档。
LangGraph State 仍为 dict 以兼容框架，这些模型用于:
  - IDE 智能提示 (类型标注)
  - API 响应验证 (FastAPI)
  - 文档自动化 (JSON Schema 导出)
"""
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from datetime import datetime


# ============================================================
# 数据层
# ============================================================

class DataSummary(BaseModel):
    """数据统计概览 — data_preprocess Agent 产出"""
    total: int = Field(..., description="总交易数")
    fraud: int = Field(..., description="欺诈交易数")
    fraud_rate: str = Field("", description="欺诈率 (百分比字符串)")
    fraud_avg_amount: str = Field("", description="欺诈交易均值")
    normal_avg_amount: str = Field("", description="正常交易均值")
    types: Dict[str, int] = Field(default_factory=dict, description="交易类型分布")
    source: str = Field("", description="数据来源")


# ============================================================
# 规则引擎层
# ============================================================

class RuleHit(BaseModel):
    """单条规则命中记录"""
    transaction: dict = Field(..., description="被命中的交易记录")
    evidence: List[str] = Field(default_factory=list, description="证据描述列表")
    risk_score: int = Field(..., ge=0, le=100, description="风险评分 0-100")
    rules: List[str] = Field(default_factory=list, description="命中规则列表")

    class Config:
        extra = "allow"


class RuleSummary(BaseModel):
    """规则命中统计"""
    total_hits: int = Field(0, description="总命中数")
    by_rule: Dict[str, int] = Field(default_factory=dict, description="各规则命中数")
    high_risk: int = Field(0, description="高风险数 (≥70)")
    medium_risk: int = Field(0, description="中风险数 (50-69)")
    low_risk: int = Field(0, description="低风险数 (<50)")


class RuleReport(BaseModel):
    """规则引擎完整产出 — rule_engine Agent 独占写入"""
    hits: List[dict] = Field(default_factory=list, description="所有规则命中记录")
    summary: dict = Field(default_factory=dict, description="命中统计摘要")
    high_risk: List[dict] = Field(default_factory=list, description="高风险交易列表 (≥70分)")


# ============================================================
# GNN 层
# ============================================================

class GNNReport(BaseModel):
    """GNN 图分析产出 — graph_analyst Agent 独占写入"""
    node_f1: float = Field(0.0, ge=0.0, le=1.0, description="节点级 F1")
    node_precision: float = Field(0.0, ge=0.0, le=1.0, description="节点级 Precision")
    node_recall: float = Field(0.0, ge=0.0, le=1.0, description="节点级 Recall")


# ============================================================
# LLM 层
# ============================================================

class LLMAnalysis(BaseModel):
    """LLM 单笔审核分析结果"""
    suspicion_level: str = Field("unknown", description="嫌疑等级: high|medium|low")
    reasoning: str = Field("", description="分析理由")
    typology: str = Field("", description="洗钱类型")
    recommendation: str = Field("", description="建议措施")


class LLMReview(BaseModel):
    """LLM 审核结果 — llm_reviewer Agent 产出条目"""
    transaction: dict = Field(..., description="被审核的交易")
    rule: str = Field("", description="触发规则名")
    risk_score: int = Field(0, ge=0, le=100)
    llm_analysis: dict = Field(default_factory=dict, description="LLM 分析结果")


# ============================================================
# 合规层
# ============================================================

class ComplianceResult(BaseModel):
    """合规审核结果 — compliance Agent 独占写入"""
    passed: bool = Field(False, description="是否通过")
    issues: List[str] = Field(default_factory=list, description="合规问题列表")
    warnings: List[str] = Field(default_factory=list, description="风险警告")
    score: int = Field(0, ge=0, le=100, description="合规评分 0-100")
    format_check: Dict[str, bool] = Field(default_factory=dict, description="格式检查明细")
    content_check: Dict = Field(default_factory=dict, description="内容检查结果")
    status: str = Field("", description="状态描述")


# ============================================================
# Agent 通信
# ============================================================

class AgentMessage(BaseModel):
    """Agent 通信总线消息 — 写入 messages 字段"""
    agent: str = Field(..., description="Agent 名称")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="ISO 时间戳")
    summary: str = Field("", description="执行摘要")
    status: str = Field("ok", description="执行状态: ok|skipped|error")


# ============================================================
# 评估指标
# ============================================================

class ClassificationMetrics(BaseModel):
    """二分类评估指标"""
    precision: float = Field(0.0, ge=0.0, le=1.0)
    recall: float = Field(0.0, ge=0.0, le=1.0)
    f1: float = Field(0.0, ge=0.0, le=1.0)
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
