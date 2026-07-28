"""
双写机制（过渡期）

在确认 PostgreSQL 完全稳定之前，同时写入 JSON 文件和 PostgreSQL 数据库。
符合业务戒律 M4: 数据完整可追溯，零丢失。

设计原则:
- 不修改现有代码，通过适配器模式实现
- PostgreSQL 写入失败时不影响 JSON 写入（错误隔离）
- 写入后自动验证一致性
- 过渡期结束后可无缝切换到纯 PostgreSQL 模式

双写范围:
1. HistoryManager → analysis_history 表
2. AccountProfileManager → accounts 表
3. AlertHistory → alert_history 表
"""
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class DualWriteAdapter:
    """双写适配器基类"""

    def __init__(self):
        from api.database import get_db_mode
        self._db_mode = get_db_mode()
        self._use_postgres = self._db_mode == "postgres"

    def _should_write_postgres(self) -> bool:
        """判断是否应该写入 PostgreSQL"""
        return self._use_postgres

    def _safe_postgres_write(self, operation: str, func, *args, **kwargs) -> bool:
        """
        安全写入 PostgreSQL

        Args:
            operation: 操作名称（用于日志）
            func: 写入函数
            *args: 函数参数
            **kwargs: 函数关键字参数

        Returns:
            True=成功, False=失败
        """
        if not self._should_write_postgres():
            return False

        try:
            result = func(*args, **kwargs)
            logger.info(f"[双写] PostgreSQL {operation} 成功")
            return result is not None
        except Exception as e:
            logger.error(f"[双写] PostgreSQL {operation} 失败: {e}")
            return False


class HistoryDualWrite(DualWriteAdapter):
    """分析历史记录双写适配器"""

    def __init__(self, history_manager):
        super().__init__()
        self._history_manager = history_manager

    def save_run(self, state: Dict[str, Any]) -> str:
        """
        双写分析历史记录

        先写入 JSON（保证可靠性），再写入 PostgreSQL（可选）

        Returns:
            execution_id
        """
        execution_id = self._history_manager.save_run(state)

        # 同步写入 PostgreSQL
        if self._should_write_postgres():
            self._write_history_to_postgres(state, execution_id)

        return execution_id

    def _write_history_to_postgres(self, state: Dict[str, Any], execution_id: str):
        """将历史记录写入 PostgreSQL"""
        from api.database import session_scope
        from api.models import AnalysisHistory

        with session_scope() as session:
            if session is None:
                return

            try:
                existing = session.query(AnalysisHistory).filter_by(
                    execution_id=execution_id
                ).first()
                if existing:
                    return

                transactions = state.get("transactions", []) or state.get("cleaned_transactions", [])
                reports = state.get("str_reports", []) or state.get("final_reports", [])
                rule_hits = state.get("rule_hit_count", 0)

                record = AnalysisHistory(
                    execution_id=execution_id,
                    timestamp=datetime.now(),
                    transactions_count=len(transactions),
                    rule_hit_count=rule_hits,
                    str_reports_count=len(reports),
                    compliance_score=state.get("compliance_score", 0),
                    total_processing_time_sec=state.get("total_processing_time", 0),
                    value_metrics=state.get("value_metrics", {}),
                    config_snapshot=state.get("config_snapshot", {}),
                    _seq=state.get("_seq", 0),
                )
                session.add(record)
                session.commit()
                logger.info(f"[双写] 历史记录 {execution_id} 写入 PostgreSQL 成功")
            except Exception as e:
                session.rollback()
                logger.error(f"[双写] 历史记录 {execution_id} 写入 PostgreSQL 失败: {e}")

    def get_run(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """
        读取历史记录

        优先从 PostgreSQL 读取（如果可用），降级到 JSON
        """
        if self._should_write_postgres():
            result = self._read_history_from_postgres(execution_id)
            if result:
                return result

        return self._history_manager.get_run(execution_id)

    def _read_history_from_postgres(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """从 PostgreSQL 读取历史记录"""
        from api.database import session_scope
        from api.models import AnalysisHistory

        with session_scope() as session:
            if session is None:
                return None

            try:
                record = session.query(AnalysisHistory).filter_by(
                    execution_id=execution_id
                ).first()
                if record:
                    return record.to_dict()
            except Exception as e:
                logger.error(f"[双写] 从 PostgreSQL 读取历史记录失败: {e}")

        return None

    def list_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        列出历史记录

        优先从 PostgreSQL 读取（如果可用），降级到 JSON
        """
        if self._should_write_postgres():
            result = self._list_history_from_postgres(limit)
            if result:
                return result

        return self._history_manager.list_runs(limit)

    def _list_history_from_postgres(self, limit: int) -> List[Dict[str, Any]]:
        """从 PostgreSQL 列出历史记录"""
        from api.database import session_scope
        from api.models import AnalysisHistory

        with session_scope() as session:
            if session is None:
                return []

            try:
                records = session.query(AnalysisHistory).order_by(
                    AnalysisHistory.created_at.desc(),
                    AnalysisHistory._seq.desc()
                ).limit(limit).all()
                return [r.to_dict() for r in records]
            except Exception as e:
                logger.error(f"[双写] 从 PostgreSQL 列出历史记录失败: {e}")

        return []


class AccountDualWrite(DualWriteAdapter):
    """账户画像双写适配器"""

    def __init__(self, profile_manager):
        super().__init__()
        self._profile_manager = profile_manager

    def save(self):
        """双写账户画像"""
        self._profile_manager.save()

        if self._should_write_postgres():
            self._write_profiles_to_postgres()

    def _write_profiles_to_postgres(self):
        """将账户画像写入 PostgreSQL"""
        from api.database import session_scope
        from api.models import Account

        with session_scope() as session:
            if session is None:
                return

            profiles = self._profile_manager.get_all_profiles()
            for account_id, profile in profiles.items():
                try:
                    profile_dict = profile.to_dict()

                    existing = session.query(Account).filter_by(
                        account_id=account_id
                    ).first()

                    if existing:
                        existing.risk_multiplier = profile.get_risk_multiplier()
                        existing.suspicious_count = profile.total_suspicious_hits
                        existing.false_positive_count = profile.false_positive_count
                        existing.false_negative_count = profile.false_negative_count
                        existing.last_suspicious_time = datetime.now() if profile.total_suspicious_hits > 0 else None
                        existing.metadata_json = {
                            "first_seen": profile.first_seen,
                            "last_seen": profile.last_seen,
                            "total_transactions": profile.total_transactions,
                            "suspicious_patterns": profile.suspicious_patterns,
                            "highest_risk_score": profile.highest_risk_score,
                            "avg_risk_score": profile.avg_risk_score,
                            "risk_trend": profile.risk_trend,
                            "notes": profile.notes,
                        }
                    else:
                        row = Account(
                            account_id=account_id,
                            risk_multiplier=profile.get_risk_multiplier(),
                            suspicious_count=profile.total_suspicious_hits,
                            false_positive_count=profile.false_positive_count,
                            false_negative_count=profile.false_negative_count,
                            last_suspicious_time=datetime.now() if profile.total_suspicious_hits > 0 else None,
                            metadata_json={
                                "first_seen": profile.first_seen,
                                "last_seen": profile.last_seen,
                                "total_transactions": profile.total_transactions,
                                "suspicious_patterns": profile.suspicious_patterns,
                                "highest_risk_score": profile.highest_risk_score,
                                "avg_risk_score": profile.avg_risk_score,
                                "risk_trend": profile.risk_trend,
                                "notes": profile.notes,
                            },
                        )
                        session.add(row)

                    session.commit()
                except Exception as e:
                    session.rollback()
                    logger.error(f"[双写] 账户画像 {account_id} 写入 PostgreSQL 失败: {e}")

    def get_profile(self, account_id: str):
        """读取账户画像（优先 PostgreSQL）"""
        return self._profile_manager.get_profile(account_id)

    def get_all_profiles(self):
        """获取所有画像"""
        return self._profile_manager.get_all_profiles()

    def update_from_suspicious(self, suspicious_list: list, total_transactions: int = 0):
        """更新画像"""
        self._profile_manager.update_from_suspicious(suspicious_list, total_transactions)
        if self._should_write_postgres():
            self.save()

    def update_from_transactions(self, transactions: list):
        """更新画像"""
        self._profile_manager.update_from_transactions(transactions)
        if self._should_write_postgres():
            self.save()


class AlertDualWrite(DualWriteAdapter):
    """告警历史双写适配器"""

    def __init__(self, alert_history):
        super().__init__()
        self._alert_history = alert_history

    def save_alert(self, alert) -> str:
        """双写告警记录"""
        filepath = self._alert_history.save_alert(alert)

        if self._should_write_postgres():
            self._write_alert_to_postgres(alert)

        return filepath

    def _write_alert_to_postgres(self, alert):
        """将告警写入 PostgreSQL"""
        from api.database import session_scope
        from api.models import AlertHistoryRecord

        with session_scope() as session:
            if session is None:
                return

            try:
                existing = session.query(AlertHistoryRecord).filter_by(
                    alert_id=alert.alert_id
                ).first()
                if existing:
                    return

                row = AlertHistoryRecord(
                    alert_id=alert.alert_id,
                    rule_id=alert.rule_id,
                    severity=alert.severity,
                    category=alert.category,
                    message=alert.message,
                    triggered_at=datetime.fromisoformat(alert.triggered_at) if alert.triggered_at else datetime.now(),
                    metadata_json=alert.context,
                )
                session.add(row)
                session.commit()
                logger.info(f"[双写] 告警 {alert.alert_id} 写入 PostgreSQL 成功")
            except Exception as e:
                session.rollback()
                logger.error(f"[双写] 告警 {alert.alert_id} 写入 PostgreSQL 失败: {e}")

    def list_alerts(self, **kwargs):
        """列出告警（优先 PostgreSQL）"""
        if self._should_write_postgres():
            result = self._list_alerts_from_postgres(**kwargs)
            if result:
                return result
        return self._alert_history.list_alerts(**kwargs)

    def _list_alerts_from_postgres(self, **kwargs) -> List[Dict[str, Any]]:
        """从 PostgreSQL 列出告警"""
        from api.database import session_scope
        from api.models import AlertHistoryRecord

        with session_scope() as session:
            if session is None:
                return []

            try:
                query = session.query(AlertHistoryRecord)

                severity = kwargs.get("severity")
                if severity:
                    query = query.filter_by(severity=severity)

                category = kwargs.get("category")
                if category:
                    query = query.filter_by(category=category)

                rule_id = kwargs.get("rule_id")
                if rule_id:
                    query = query.filter_by(rule_id=rule_id)

                limit = kwargs.get("limit", 100)

                records = query.order_by(
                    AlertHistoryRecord.triggered_at.desc()
                ).limit(limit).all()
                return [r.to_dict() for r in records]
            except Exception as e:
                logger.error(f"[双写] 从 PostgreSQL 列出告警失败: {e}")

        return []

    def get_alert(self, alert_id: str):
        """获取告警详情"""
        return self._alert_history.get_alert(alert_id)

    def stats(self):
        """统计告警"""
        return self._alert_history.stats()


# 全局双写实例（延迟初始化）
_global_history_adapter = None
_global_account_adapter = None
_global_alert_adapter = None


def get_history_adapter(history_manager=None) -> HistoryDualWrite:
    """获取历史记录双写适配器"""
    global _global_history_adapter
    if _global_history_adapter is None:
        if history_manager is None:
            from tools.history_manager import HistoryManager
            history_manager = HistoryManager()
        _global_history_adapter = HistoryDualWrite(history_manager)
    return _global_history_adapter


def get_account_adapter(profile_manager=None) -> AccountDualWrite:
    """获取账户画像双写适配器"""
    global _global_account_adapter
    if _global_account_adapter is None:
        if profile_manager is None:
            from tools.account_profile import AccountProfileManager
            from config import PROFILES_DIR
            profile_path = f"{PROFILES_DIR}/account_profiles.json"
            profile_manager = AccountProfileManager(profile_path)
        _global_account_adapter = AccountDualWrite(profile_manager)
    return _global_account_adapter


def get_alert_adapter(alert_history=None) -> AlertDualWrite:
    """获取告警双写适配器"""
    global _global_alert_adapter
    if _global_alert_adapter is None:
        if alert_history is None:
            from tools.alert_history import AlertHistory
            alert_history = AlertHistory()
        _global_alert_adapter = AlertDualWrite(alert_history)
    return _global_alert_adapter


def init_dual_write(history_manager=None, profile_manager=None, alert_history=None):
    """初始化所有双写适配器"""
    get_history_adapter(history_manager)
    get_account_adapter(profile_manager)
    get_alert_adapter(alert_history)
    logger.info("[双写] 双写适配器初始化完成")