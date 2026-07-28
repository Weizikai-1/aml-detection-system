"""
分析历史记录管理

保存每次分析运行的摘要信息，支持查询、检索、对比。

戒律:
- M1: 历史记录数据来自真实运行结果，不编造
- M4: 完整记录关键指标，便于追溯和审计
- P1: 不遗漏任何一次运行（包括失败的）

存储格式:
    data/history/
        ├── <execution_id>.json   # 单次运行记录
        └── index.json            # 索引文件（轻量，便于快速列表）

单次记录字段:
    - execution_id: 执行ID
    - timestamp: 运行时间戳
    - analysis_date: 分析日期
    - transactions_count: 交易笔数
    - transactions_hash: 交易数据哈希（用于去重/对比）
    - duration_seconds: 总耗时
    - rule_hit_count: 规则命中数
    - llm_confirmed_count: LLM确认可疑数
    - report_count: 生成报告数
    - risk_distribution: 风险分布
    - interrupted: 是否中断
    - error: 错误信息
    - step_times: 各步骤耗时
    - primary_accounts: 主涉案账户列表
"""
import os
import json
import time
import hashlib
from datetime import datetime
from typing import Dict, Any, List, Optional


class HistoryManager:
    """分析历史记录管理器"""

    def __init__(self, history_dir: str = None):
        """
        Args:
            history_dir: 历史记录目录，None时使用默认路径
        """
        if history_dir is None:
            from config import HISTORY_DIR
            history_dir = HISTORY_DIR
        self.history_dir = history_dir
        os.makedirs(self.history_dir, exist_ok=True)
        self.index_path = os.path.join(self.history_dir, "index.json")

    # ============================================================
    # 保存
    # ============================================================
    def save_run(self, state: Dict[str, Any]) -> str:
        """
        保存一次分析运行记录

        Args:
            state: 工作流最终状态字典

        Returns:
            执行ID
        """
        execution_id = state.get("execution_id") or self._gen_id()

        # 提取关键字段（不保存完整数据，避免文件过大）
        record = self._build_record(execution_id, state)

        # 保存单次记录
        record_path = os.path.join(self.history_dir, f"{execution_id}.json")
        with open(record_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2, default=str)

        # 更新索引
        self._update_index(record)

        return execution_id

    def _build_record(self, execution_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
        """从state构建历史记录"""
        transactions = state.get("transactions", []) or state.get("cleaned_transactions", [])
        txns_hash = self._compute_txns_hash(transactions)

        # 风险分布
        reports = state.get("str_reports", []) or state.get("final_reports", [])
        risk_dist = {}
        for r in reports:
            level = r.get("risk_level", "unknown")
            risk_dist[level] = risk_dist.get(level, 0) + 1

        # 主涉案账户
        primary_accounts = [r.get("primary_account", "") for r in reports]

        # 规则详情
        rule_details = state.get("rule_details", {})

        # 戒律 M4: 同时保留展示用 timestamp 与高精度 created_at（纳秒级），
        # 用于排序，避免同一毫秒内多次保存时排序不稳定（Windows 平台 datetime.now 精度仅 ms）
        import time as _time
        now = datetime.now()
        seq = getattr(self, "_save_seq", 0) + 1
        self._save_seq = seq
        record = {
            "execution_id": execution_id,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "created_at": _time.time_ns() / 1e9,
            "_seq": seq,
            "analysis_date": state.get("analysis_date", ""),
            "transactions_count": len(transactions),
            "transactions_hash": txns_hash,
            "duration_seconds": state.get("total_processing_time", 0),
            "rule_hit_count": state.get("rule_hit_count", 0),
            "rule_details": rule_details,
            "llm_confirmed_count": len(state.get("llm_confirmed", [])),
            "report_count": state.get("report_count", 0),
            "risk_distribution": risk_dist,
            "primary_accounts": primary_accounts,
            "interrupted": state.get("interrupted", False),
            "error": state.get("error", ""),
            "step_times": state.get("step_times", {}),
            "node_errors": self._extract_node_errors(state),
            # 戒律 M4: 保留价值指标供趋势分析回溯
            "value_metrics": state.get("value_metrics", {}),
            # 保留完整报告数据供API查询和导出
            "str_reports": reports,
        }
        return record

    def _compute_txns_hash(self, transactions: list) -> str:
        """计算交易数据哈希
        戒律 M1: 真实数据指纹 — 包含 id/amount/from_account/to_account/timestamp
        这样不同账户或时间的交易不会被误判为同一数据集
        """
        if not transactions:
            return ""
        try:
            txn_data = sorted([
                {
                    "id": t.get("transaction_id", ""),
                    "amount": round(float(t.get("amount", 0)), 2),
                    "from": t.get("from_account", ""),
                    "to": t.get("to_account", ""),
                    "ts": t.get("timestamp", ""),
                }
                for t in transactions
            ], key=lambda x: (x["id"], x["ts"]))
            s = json.dumps(txn_data, sort_keys=True, ensure_ascii=False)
            return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
        except Exception:
            return ""

    def _extract_node_errors(self, state: Dict[str, Any]) -> List[Dict]:
        """提取节点错误信息"""
        errors = []
        node_error = state.get("_node_error")
        if node_error:
            errors.append(node_error)
        return errors

    def _gen_id(self) -> str:
        """生成执行ID"""
        import uuid
        return str(uuid.uuid4())[:8]

    def _update_index(self, record: Dict[str, Any]):
        """更新索引文件（仅保存轻量字段）"""
        index = self._load_index()
        index_entry = {
            "execution_id": record["execution_id"],
            "timestamp": record["timestamp"],
            "created_at": record.get("created_at", 0.0),
            "_seq": record.get("_seq", 0),
            "analysis_date": record["analysis_date"],
            "transactions_count": record["transactions_count"],
            "rule_hit_count": record["rule_hit_count"],
            "report_count": record["report_count"],
            "risk_distribution": record["risk_distribution"],
            "duration_seconds": record["duration_seconds"],
            # 戒律 M4: 补全索引字段，避免列表查询信息缺失
            "interrupted": record.get("interrupted", False),
            "error": record.get("error", ""),
            "transactions_hash": record.get("transactions_hash", ""),
            "llm_confirmed_count": record.get("llm_confirmed_count", 0),
        }
        # 替换或新增
        index = [e for e in index if e.get("execution_id") != record["execution_id"]]
        index.append(index_entry)
        # 戒律 M4: 多键组合排序（同纳秒内仍可区分）
        index.sort(
            key=lambda x: (
                x.get("created_at", 0.0) or 0.0,
                x.get("_seq", 0),
                x.get("timestamp", ""),
            ),
            reverse=True,
        )
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2, default=str)

    def _load_index(self) -> List[Dict]:
        """加载索引"""
        if not os.path.exists(self.index_path):
            return []
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    # ============================================================
    # 查询
    # ============================================================
    def list_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        列出最近的运行记录（轻量索引）

        Args:
            limit: 最大返回数

        Returns:
            运行记录摘要列表（按时间倒序）
        """
        index = self._load_index()
        return index[:limit]

    def get_run(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单次运行的完整记录

        Args:
            execution_id: 执行ID

        Returns:
            完整记录字典，不存在返回 None
        """
        path = os.path.join(self.history_dir, f"{execution_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def search_runs(
        self,
        start_date: str = None,
        end_date: str = None,
        min_report_count: int = None,
        account: str = None,
    ) -> List[Dict[str, Any]]:
        """
        搜索历史运行记录

        Args:
            start_date: 起始日期 (YYYY-MM-DD)，None不限
            end_date: 结束日期 (YYYY-MM-DD)，None不限
            min_report_count: 最少报告数
            account: 主涉案账户（模糊匹配）

        Returns:
            匹配的运行记录列表
        """
        index = self._load_index()
        results = []
        for entry in index:
            ts = entry.get("timestamp", "")
            # 日期范围过滤
            if start_date and ts < start_date:
                continue
            if end_date and ts > end_date + " 23:59:59":
                continue
            # 最少报告数
            if min_report_count is not None and entry.get("report_count", 0) < min_report_count:
                continue
            # 账户匹配（需要加载完整记录）
            if account:
                full = self.get_run(entry["execution_id"])
                if not full:
                    continue
                accounts = full.get("primary_accounts", [])
                if not any(account in a for a in accounts):
                    continue
            results.append(entry)
        return results

    # ============================================================
    # 删除
    # ============================================================
    def delete_run(self, execution_id: str) -> bool:
        """删除单次运行记录"""
        path = os.path.join(self.history_dir, f"{execution_id}.json")
        deleted = False
        if os.path.exists(path):
            os.remove(path)
            deleted = True
        # 从索引中移除
        index = self._load_index()
        new_index = [e for e in index if e.get("execution_id") != execution_id]
        if len(new_index) != len(index):
            with open(self.index_path, "w", encoding="utf-8") as f:
                json.dump(new_index, f, ensure_ascii=False, indent=2, default=str)
        return deleted

    def clear_all(self) -> int:
        """清空所有历史记录，返回删除数"""
        count = 0
        if not os.path.exists(self.history_dir):
            return 0
        for f in os.listdir(self.history_dir):
            if f.endswith(".json"):
                try:
                    os.remove(os.path.join(self.history_dir, f))
                    count += 1
                except Exception:
                    pass
        return count

    # ============================================================
    # 统计
    # ============================================================
    def stats(self) -> Dict[str, Any]:
        """历史记录统计"""
        index = self._load_index()
        if not index:
            return {
                "total_runs": 0,
                "total_reports": 0,
                "total_transactions": 0,
                "avg_duration": 0,
            }
        total_reports = sum(e.get("report_count", 0) for e in index)
        total_txns = sum(e.get("transactions_count", 0) for e in index)
        durations = [e.get("duration_seconds", 0) for e in index if e.get("duration_seconds", 0) > 0]
        avg_duration = sum(durations) / len(durations) if durations else 0
        return {
            "total_runs": len(index),
            "total_reports": total_reports,
            "total_transactions": total_txns,
            "avg_duration": round(avg_duration, 2),
            "first_run": index[-1].get("timestamp", "") if index else "",
            "last_run": index[0].get("timestamp", "") if index else "",
        }
