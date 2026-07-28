"""
SQLAlchemy ORM 模型

对应 init-db.sql 中的表结构，用于 Python 代码操作数据库。
符合业务戒律 M4: 所有模型字段与数据库列一一对应，可追溯。
"""
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, DateTime, JSON, Numeric, Sequence
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Account(Base):
    """账户风险画像表"""
    __tablename__ = "accounts"

    account_id = Column(String(50), primary_key=True, comment="账户ID")
    risk_multiplier = Column(Numeric(10, 4), default=1.0, comment="风险乘数")
    suspicious_count = Column(Integer, default=0, comment="可疑命中次数")
    false_positive_count = Column(Integer, default=0, comment="误报次数")
    false_negative_count = Column(Integer, default=0, comment="漏报次数")
    last_suspicious_time = Column(DateTime, nullable=True, comment="最近可疑时间")
    last_feedback_time = Column(DateTime, nullable=True, comment="最近反馈时间")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    metadata_json = Column("metadata", JSON, nullable=True, comment="扩展元数据")

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "risk_multiplier": float(self.risk_multiplier) if self.risk_multiplier else 1.0,
            "suspicious_count": self.suspicious_count or 0,
            "false_positive_count": self.false_positive_count or 0,
            "false_negative_count": self.false_negative_count or 0,
            "last_suspicious_time": self.last_suspicious_time.isoformat() if self.last_suspicious_time else "",
            "last_feedback_time": self.last_feedback_time.isoformat() if self.last_feedback_time else "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "updated_at": self.updated_at.isoformat() if self.updated_at else "",
            "metadata": self.metadata_json or {},
        }


class AnalysisHistory(Base):
    """分析历史表"""
    __tablename__ = "analysis_history"

    execution_id = Column(String(20), primary_key=True, comment="执行ID")
    timestamp = Column(DateTime, default=datetime.now, comment="运行时间")
    transactions_count = Column(Integer, default=0, comment="交易笔数")
    rule_hit_count = Column(Integer, default=0, comment="规则命中数")
    str_reports_count = Column(Integer, default=0, comment="报告数")
    compliance_score = Column(Numeric(5, 2), default=0, comment="合规评分")
    total_processing_time_sec = Column(Numeric(10, 3), default=0, comment="总耗时(秒)")
    value_metrics = Column(JSON, nullable=True, comment="价值指标")
    config_snapshot = Column(JSON, nullable=True, comment="配置快照")
    _seq = Column(Integer, default=0, comment="排序序列号")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else "",
            "transactions_count": self.transactions_count or 0,
            "rule_hit_count": self.rule_hit_count or 0,
            "report_count": self.str_reports_count or 0,
            "compliance_score": float(self.compliance_score) if self.compliance_score else 0,
            "total_processing_time_sec": float(self.total_processing_time_sec) if self.total_processing_time_sec else 0,
            "value_metrics": self.value_metrics or {},
            "config_snapshot": self.config_snapshot or {},
            "_seq": self._seq or 0,
            "created_at": self.created_at.isoformat() if self.created_at else "",
        }


class EvaluationResult(Base):
    """评估结果表"""
    __tablename__ = "evaluation_results"

    eval_id = Column(String(20), primary_key=True, comment="评估ID")
    execution_id = Column(String(20), nullable=True, comment="关联执行ID")
    ground_truth_name = Column(String(100), nullable=True, comment="真值集名称")
    precision_score = Column(Numeric(5, 4), default=0, comment="精确率")
    recall_score = Column(Numeric(5, 4), default=0, comment="召回率")
    f1_score = Column(Numeric(5, 4), default=0, comment="F1分数")
    tp = Column(Integer, default=0, comment="真正例")
    fp = Column(Integer, default=0, comment="假正例")
    tn = Column(Integer, default=0, comment="真负例")
    fn = Column(Integer, default=0, comment="假负例")
    scan_results = Column(JSON, nullable=True, comment="扫描结果")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

    def to_dict(self) -> dict:
        return {
            "eval_id": self.eval_id,
            "execution_id": self.execution_id,
            "ground_truth_name": self.ground_truth_name,
            "precision": float(self.precision_score) if self.precision_score else 0,
            "recall": float(self.recall_score) if self.recall_score else 0,
            "f1": float(self.f1_score) if self.f1_score else 0,
            "tp": self.tp or 0,
            "fp": self.fp or 0,
            "tn": self.tn or 0,
            "fn": self.fn or 0,
            "scan_results": self.scan_results or {},
            "created_at": self.created_at.isoformat() if self.created_at else "",
        }


class AlertHistoryRecord(Base):
    """告警历史表"""
    __tablename__ = "alert_history"

    alert_id = Column(String(36), primary_key=True, comment="告警ID")
    rule_id = Column(String(50), nullable=False, comment="规则ID")
    severity = Column(String(20), nullable=False, comment="严重级别")
    category = Column(String(50), nullable=True, comment="告警类别")
    message = Column(Text, nullable=True, comment="告警消息")
    triggered_at = Column(DateTime, default=datetime.now, comment="触发时间")
    acknowledged_at = Column(DateTime, nullable=True, comment="确认时间")
    acknowledged_by = Column(String(50), nullable=True, comment="确认人")
    metadata_json = Column("metadata", JSON, nullable=True, comment="扩展元数据")
    _seq = Column(Integer, nullable=True, comment="排序序列号")

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "triggered_at": self.triggered_at.isoformat() if self.triggered_at else "",
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else "",
            "acknowledged_by": self.acknowledged_by or "",
            "metadata": self.metadata_json or {},
        }


class User(Base):
    """用户认证表"""
    __tablename__ = "users"

    user_id = Column(String(36), primary_key=True, comment="用户ID")
    username = Column(String(50), unique=True, nullable=False, comment="用户名")
    hashed_password = Column(String(255), nullable=False, comment="密码哈希")
    email = Column(String(100), unique=True, nullable=True, comment="邮箱")
    role = Column(String(20), default="analyst", comment="角色")
    is_active = Column(Boolean, default=True, comment="是否激活")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    last_login = Column(DateTime, nullable=True, comment="最近登录")

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email or "",
            "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "last_login": self.last_login.isoformat() if self.last_login else "",
        }


class AuditLog(Base):
    """审计日志表（含哈希链完整性保护，M11修复）"""
    __tablename__ = "audit_logs"

    log_id = Column(Integer, primary_key=True, autoincrement=True, comment="日志ID")
    user_id = Column(String(36), nullable=True, comment="操作用户ID")
    action = Column(String(100), nullable=False, comment="操作类型")
    resource_type = Column(String(50), nullable=True, comment="资源类型")
    resource_id = Column(String(100), nullable=True, comment="资源ID")
    ip_address = Column(String(50), nullable=True, comment="IP地址")
    timestamp = Column(DateTime, default=datetime.now, comment="操作时间")
    details = Column(JSON, nullable=True, comment="操作详情")
    prev_hash = Column(String(64), nullable=False, default="GENESIS", comment="上一条日志哈希")
    current_hash = Column(String(64), nullable=False, default="", comment="当前日志哈希")

    def to_dict(self) -> dict:
        return {
            "log_id": self.log_id,
            "user_id": self.user_id or "",
            "action": self.action,
            "resource_type": self.resource_type or "",
            "resource_id": self.resource_id or "",
            "ip_address": self.ip_address or "",
            "timestamp": self.timestamp.isoformat() if self.timestamp else "",
            "details": self.details or {},
            "prev_hash": self.prev_hash or "",
            "current_hash": self.current_hash or "",
        }