"""
数据血缘追踪 (Data Lineage Tracker)

职责:
- 端到端血缘记录: 报告 → 证据 → 规则 → 原始交易 → 导入批次
- 多维追溯: 按 execution_id / report_id / transaction_id 查询
- 完整性校验: 阶段链路完整性验证
- 索引维护: 报告/交易反向索引，支持快速追溯

设计原则:
- M1: 血缘数据来自真实工作流状态，不编造
- M2: 每个阶段记录明确输入/输出引用与统计
- M3: 不涉及风险评分（血缘仅记录，不评分）
- M4: 完整记录每个阶段的版本信息（rule_config_version / llm_model_version 等）
- P4: 记录失败 try/except 不阻塞主流程
"""
import json
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# 模块版本（戒律 M4: 可追溯）
__LINEAGE_TRACKER_VERSION__ = "1.0.0"

# 默认保留天数
_DEFAULT_RETAIN_DAYS = 90


def _now_iso() -> str:
    """当前时间 ISO 格式"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_len(seq) -> int:
    """安全获取序列长度"""
    try:
        return len(seq) if seq else 0
    except Exception:
        return 0


class DataLineageTracker:
    """
    数据血缘追踪器

    主入口:
        record_lineage(execution_id, state) -> lineage_id
        query_lineage(execution_id) -> Optional[Dict]
        trace_report(report_id) -> Optional[Dict]
        trace_transaction(transaction_id) -> List[Dict]
        list_lineages(limit) -> List[Dict]
        verify_integrity(lineage_id) -> Dict

    戒律遵守:
    - M1: 血缘数据来自真实工作流状态字段，不编造
    - M4: 完整记录每个阶段的版本信息
    - P4: 所有操作 try/except，失败不抛异常
    """

    # 7 个阶段定义（按工作流执行顺序）
    STAGES = [
        "data_preprocess",
        "rule_engine",
        "graph_analyst",
        "llm_reviewer",
        "report_generator",
        "compliance_auditor",
        "cross_period_linker",
    ]

    def __init__(self, lineage_dir: str = None, config: Dict[str, Any] = None):
        """
        Args:
            lineage_dir: 血缘存储目录，默认 config.LINEAGE_DIR
            config: 配置字典（retain_days / index_by_report / index_by_transaction）
        """
        if lineage_dir is None:
            try:
                from config import LINEAGE_DIR
                lineage_dir = LINEAGE_DIR
            except Exception:
                lineage_dir = os.path.join("data", "lineage")

        self.lineage_dir = lineage_dir
        self.records_dir = os.path.join(lineage_dir, "records")
        self.by_report_dir = os.path.join(lineage_dir, "by_report")
        self.by_transaction_dir = os.path.join(lineage_dir, "by_transaction")
        self.index_path = os.path.join(lineage_dir, "index.json")

        # 配置
        cfg = config or {}
        try:
            from config import LINEAGE_CONFIG as _LC
            if not config:
                cfg = _LC
        except Exception:
            pass
        self.retain_days = int(cfg.get("retain_days", _DEFAULT_RETAIN_DAYS))
        self.index_by_report = bool(cfg.get("index_by_report", True))
        self.index_by_transaction = bool(cfg.get("index_by_transaction", True))
        self.enabled = bool(cfg.get("enabled", True))

        # 确保目录存在
        for d in (self.lineage_dir, self.records_dir, self.by_report_dir, self.by_transaction_dir):
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                pass

    # ============================================================
    # 主入口：记录血缘
    # ============================================================
    def record_lineage(self, execution_id: str, state: Dict[str, Any]) -> Optional[str]:
        """
        从工作流最终状态提取血缘信息并持久化

        Args:
            execution_id: 执行ID
            state: 工作流最终状态（AMLState）

        Returns:
            lineage_id，失败返回 None（戒律 P4: 不抛异常）

        戒律:
        - M1: 血缘数据来自真实 state 字段，不编造
        - M4: 完整记录每个阶段版本信息
        - P4: 任何失败返回 None，不阻塞主流程
        """
        if not self.enabled:
            return None
        if not execution_id or not state:
            return None

        try:
            lineage_id = f"LN-{uuid.uuid4().hex[:12]}"
            timestamp = _now_iso()

            # 提取各阶段信息
            stages = self._extract_all_stages(state)

            # 提取最终输出
            final_outputs = self._extract_final_outputs(state)

            # 提取导入批次ID（若 state 中有）
            import_batch_id = state.get("import_batch_id") or state.get("batch_id") or ""

            # 提取报告ID列表（用于索引）
            report_ids = self._extract_report_ids(state)

            # 提取交易ID列表（用于索引）
            transaction_ids = self._extract_transaction_ids(state)

            record = {
                "lineage_id": lineage_id,
                "execution_id": execution_id,
                "timestamp": timestamp,
                "created_at_ts": time.time(),
                "tracker_version": __LINEAGE_TRACKER_VERSION__,
                "import_batch_id": import_batch_id,
                "stages": stages,
                "final_outputs": final_outputs,
                "report_ids": report_ids,
                "transaction_ids": transaction_ids,
                "total_processing_time": state.get("total_processing_time", 0),
            }

            # 持久化主记录
            record_path = os.path.join(self.records_dir, f"{lineage_id}.json")
            with open(record_path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)

            # 更新索引
            self._update_index(record)

            # 建立报告反向索引
            if self.index_by_report:
                for rid in report_ids:
                    self._write_index_entry(
                        os.path.join(self.by_report_dir, f"{rid}.json"),
                        lineage_id, execution_id, timestamp,
                    )

            # 建立交易反向索引
            if self.index_by_transaction:
                for tid in transaction_ids:
                    self._append_transaction_index(
                        tid, lineage_id, execution_id, timestamp,
                    )

            return lineage_id

        except Exception as e:
            # 戒律 P4: 不抛异常
            print(f"  [血缘追踪] 记录失败: {e}")
            return None

    # ============================================================
    # 查询接口
    # ============================================================
    def query_lineage(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """
        查询指定执行的血缘记录

        Args:
            execution_id: 执行ID

        Returns:
            血缘记录字典，不存在返回 None
        """
        if not execution_id:
            return None
        try:
            index = self._load_index()
            for entry in index.get("entries", []):
                if entry.get("execution_id") == execution_id:
                    lid = entry.get("lineage_id")
                    if lid:
                        return self._load_record(lid)
            return None
        except Exception as e:
            print(f"  [血缘追踪] 查询失败: {e}")
            return None

    def query_by_lineage_id(self, lineage_id: str) -> Optional[Dict[str, Any]]:
        """按 lineage_id 直接查询"""
        if not lineage_id:
            return None
        return self._load_record(lineage_id)

    def trace_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        """
        从 STR 报告 ID 逆向追溯到血缘记录

        Args:
            report_id: 报告ID

        Returns:
            血缘记录字典，不存在返回 None
        """
        if not report_id:
            return None
        try:
            idx_path = os.path.join(self.by_report_dir, f"{report_id}.json")
            if not os.path.exists(idx_path):
                return None
            with open(idx_path, "r", encoding="utf-8") as f:
                idx_data = json.load(f)
            lid = idx_data.get("lineage_id")
            if lid:
                return self._load_record(lid)
            return None
        except Exception as e:
            print(f"  [血缘追踪] 报告追溯失败: {e}")
            return None

    def trace_transaction(self, transaction_id: str) -> List[Dict[str, Any]]:
        """
        查询某笔交易参与过的所有分析批次

        Args:
            transaction_id: 交易ID

        Returns:
            血缘记录列表（可能多条），失败返回空列表
        """
        if not transaction_id:
            return []
        try:
            idx_path = os.path.join(self.by_transaction_dir, f"{transaction_id}.json")
            if not os.path.exists(idx_path):
                return []
            with open(idx_path, "r", encoding="utf-8") as f:
                idx_data = json.load(f)
            lineage_ids = idx_data.get("lineage_ids", [])
            results = []
            for lid in lineage_ids:
                rec = self._load_record(lid)
                if rec:
                    results.append(rec)
            return results
        except Exception as e:
            print(f"  [血缘追踪] 交易追溯失败: {e}")
            return []

    def list_lineages(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        列出最近的血缘记录（摘要）

        Args:
            limit: 最多返回条数

        Returns:
            摘要列表，每条含 lineage_id / execution_id / timestamp / report_ids
        """
        try:
            index = self._load_index()
            entries = index.get("entries", [])
            # 按 created_at_ts 倒序
            sorted_entries = sorted(
                entries,
                key=lambda x: x.get("created_at_ts", 0),
                reverse=True,
            )
            return sorted_entries[:limit]
        except Exception as e:
            print(f"  [血缘追踪] 列表查询失败: {e}")
            return []

    # ============================================================
    # 完整性校验
    # ============================================================
    def verify_integrity(self, lineage_id: str) -> Dict[str, Any]:
        """
        校验血缘记录完整性

        Args:
            lineage_id: 血缘ID

        Returns:
            {
                "valid": bool,
                "lineage_id": str,
                "stages_present": List[str],
                "stages_missing": List[str],
                "issues": List[str],
            }
        """
        result = {
            "valid": False,
            "lineage_id": lineage_id,
            "stages_present": [],
            "stages_missing": [],
            "issues": [],
        }
        try:
            record = self._load_record(lineage_id)
            if record is None:
                result["issues"].append("血缘记录不存在")
                return result

            stages = record.get("stages", [])
            stage_names = {s.get("stage") for s in stages if isinstance(s, dict)}
            result["stages_present"] = sorted(stage_names)

            # 检查必需阶段（前 5 个为必需，后 2 个可选）
            required_stages = self.STAGES[:5]
            missing = [s for s in required_stages if s not in stage_names]
            result["stages_missing"] = missing

            if missing:
                result["issues"].append(f"缺少必需阶段: {missing}")

            # 检查 final_outputs
            final_outputs = record.get("final_outputs", {})
            if not final_outputs:
                result["issues"].append("final_outputs 为空")

            # 检查 execution_id
            if not record.get("execution_id"):
                result["issues"].append("execution_id 缺失")

            # 检查索引文件一致性（如启用）
            if self.index_by_report:
                for rid in record.get("report_ids", []):
                    idx_path = os.path.join(self.by_report_dir, f"{rid}.json")
                    if not os.path.exists(idx_path):
                        result["issues"].append(f"报告索引缺失: {rid}")
                        break

            result["valid"] = len(result["issues"]) == 0
            return result

        except Exception as e:
            result["issues"].append(f"校验异常: {e}")
            return result

    # ============================================================
    # 维护：清理过期记录
    # ============================================================
    def cleanup_expired(self) -> int:
        """
        清理过期血缘记录

        Returns:
            清理的记录数
        """
        if self.retain_days <= 0:
            return 0
        try:
            cutoff_ts = time.time() - self.retain_days * 86400
            index = self._load_index()
            entries = index.get("entries", [])
            kept_entries = []
            removed_count = 0

            for entry in entries:
                if entry.get("created_at_ts", 0) < cutoff_ts:
                    # 删除主记录
                    lid = entry.get("lineage_id")
                    if lid:
                        rec_path = os.path.join(self.records_dir, f"{lid}.json")
                        if os.path.exists(rec_path):
                            try:
                                os.remove(rec_path)
                            except Exception:
                                pass
                    removed_count += 1
                else:
                    kept_entries.append(entry)

            # 重写索引
            index["entries"] = kept_entries
            index["updated_at"] = _now_iso()
            self._save_index(index)

            return removed_count
        except Exception as e:
            print(f"  [血缘追踪] 清理失败: {e}")
            return 0

    # ============================================================
    # 内部：阶段信息提取
    # ============================================================
    def _extract_all_stages(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 state 提取所有阶段信息（戒律 M1: 真实字段）"""
        stages = []
        stages.append(self._extract_preprocess_stage(state))
        stages.append(self._extract_rule_engine_stage(state))
        stages.append(self._extract_graph_stage(state))
        stages.append(self._extract_llm_stage(state))
        stages.append(self._extract_report_stage(state))
        stages.append(self._extract_compliance_stage(state))
        cross = self._extract_cross_period_stage(state)
        if cross:
            stages.append(cross)
        # 过滤 None
        return [s for s in stages if s]

    def _extract_preprocess_stage(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """阶段1: 数据预处理"""
        try:
            raw_count = _safe_len(state.get("transactions"))
            cleaned_count = _safe_len(state.get("cleaned_transactions"))
            stats = state.get("preprocessing_stats", {}) or {}
            return {
                "stage": "data_preprocess",
                "input_refs": [f"raw_transactions:{raw_count}"],
                "output_refs": [f"cleaned_transactions:{cleaned_count}"],
                "version_info": {
                    "preprocessor_version": "1.0.0",
                },
                "stats": {
                    "raw_count": raw_count,
                    "cleaned_count": cleaned_count,
                    "quality_score": stats.get("quality_score"),
                    "deduplicated": stats.get("deduplicated", 0),
                    "missing_filled": stats.get("missing_filled", 0),
                    "account_baselines_count": _safe_len(state.get("account_baselines")),
                },
            }
        except Exception:
            return None

    def _extract_rule_engine_stage(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """阶段2: 规则引擎"""
        try:
            rule_hits = state.get("rule_hits", []) or []
            rule_details = state.get("rule_details", {}) or {}
            rule_engine_stats = state.get("rule_engine_stats", {}) or {}

            # 规则配置版本（从 analysis_params 或 rule_tuner 获取）
            analysis_params = state.get("analysis_params", {}) or {}
            rule_config_version = (
                analysis_params.get("rule_config_version")
                or analysis_params.get("config_version")
                or "default"
            )

            return {
                "stage": "rule_engine",
                "input_refs": [f"cleaned_transactions:{_safe_len(state.get('cleaned_transactions'))}"],
                "output_refs": [f"rule_hits:{_safe_len(rule_hits)}"],
                "version_info": {
                    "rule_config_version": rule_config_version,
                    "rule_engine_version": "1.0.0",
                },
                "stats": {
                    "rule_hit_count": state.get("rule_hit_count", _safe_len(rule_hits)),
                    "by_rule": rule_details,
                    "engine_stats": rule_engine_stats,
                },
            }
        except Exception:
            return None

    def _extract_graph_stage(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """阶段3: 图分析"""
        try:
            graph_data = state.get("graph_data") or {}
            graph_suspicious = state.get("graph_suspicious", []) or []

            # 提取社区/节点/边统计
            communities = graph_data.get("communities", []) if isinstance(graph_data, dict) else []
            nodes = graph_data.get("nodes", []) if isinstance(graph_data, dict) else []
            edges = graph_data.get("edges", []) if isinstance(graph_data, dict) else []

            # GNN 模型版本
            gnn_version = "1.0.0"
            try:
                from tools.gnn_model import MoneyLaunderingGCN
                gnn_version = getattr(MoneyLaunderingGCN, "__version__", "1.0.0")
            except Exception:
                pass

            return {
                "stage": "graph_analyst",
                "input_refs": [f"rule_hits:{_safe_len(state.get('rule_hits'))}"],
                "output_refs": [
                    f"graph_suspicious:{_safe_len(graph_suspicious)}",
                    f"communities:{_safe_len(communities)}",
                ],
                "version_info": {
                    "gnn_model_version": gnn_version,
                    "graph_analyst_version": "1.0.0",
                },
                "stats": {
                    "graph_hit_count": state.get("graph_hit_count", _safe_len(graph_suspicious)),
                    "nodes_count": _safe_len(nodes),
                    "edges_count": _safe_len(edges),
                    "communities_count": _safe_len(communities),
                },
            }
        except Exception:
            return None

    def _extract_llm_stage(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """阶段4: LLM 审核"""
        try:
            llm_reviewed = state.get("llm_reviewed", []) or []
            llm_confirmed = state.get("llm_confirmed", []) or []
            false_positives = state.get("false_positives", []) or []
            llm_stats = state.get("llm_stats", {}) or {}

            # LLM 模型版本
            try:
                from config import DEEPSEEK_MODEL
                llm_model = DEEPSEEK_MODEL
            except Exception:
                llm_model = "unknown"

            return {
                "stage": "llm_reviewer",
                "input_refs": [f"graph_suspicious:{_safe_len(state.get('graph_suspicious'))}"],
                "output_refs": [
                    f"llm_confirmed:{_safe_len(llm_confirmed)}",
                    f"false_positives:{_safe_len(false_positives)}",
                ],
                "version_info": {
                    "llm_model": llm_model,
                    "llm_reviewer_version": "1.0.0",
                },
                "stats": {
                    "llm_analysis_count": state.get("llm_analysis_count", _safe_len(llm_reviewed)),
                    "llm_confirmed_count": _safe_len(llm_confirmed),
                    "false_positive_count": _safe_len(false_positives),
                    "llm_stats": llm_stats,
                },
            }
        except Exception:
            return None

    def _extract_report_stage(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """阶段5: 报告生成"""
        try:
            str_reports = state.get("str_reports", []) or []
            report_stats = state.get("report_generation_stats", {}) or {}

            return {
                "stage": "report_generator",
                "input_refs": [f"llm_confirmed:{_safe_len(state.get('llm_confirmed'))}"],
                "output_refs": [f"str_reports:{_safe_len(str_reports)}"],
                "version_info": {
                    "report_template_version": "1.0.0",
                    "report_generator_version": "1.0.0",
                },
                "stats": {
                    "report_count": state.get("report_count", _safe_len(str_reports)),
                    "report_stats": report_stats,
                },
            }
        except Exception:
            return None

    def _extract_compliance_stage(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """阶段6: 合规审核"""
        try:
            final_reports = state.get("final_reports", []) or []
            rejected = state.get("rejected_reports", []) or []
            compliance_stats = state.get("compliance_stats", {}) or {}

            return {
                "stage": "compliance_auditor",
                "input_refs": [f"str_reports:{_safe_len(state.get('str_reports'))}"],
                "output_refs": [
                    f"final_reports:{_safe_len(final_reports)}",
                    f"rejected_reports:{_safe_len(rejected)}",
                ],
                "version_info": {
                    "compliance_auditor_version": "1.0.0",
                    "aml_rules_version": "1.0.0",
                },
                "stats": {
                    "final_report_count": _safe_len(final_reports),
                    "rejected_count": _safe_len(rejected),
                    "compliance_score": state.get("compliance_score", 0),
                    "compliance_stats": compliance_stats,
                },
            }
        except Exception:
            return None

    def _extract_cross_period_stage(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """阶段7: 跨期串联（可选，仅当存在时记录）"""
        try:
            links = state.get("cross_period_links", []) or []
            if not links:
                return None
            return {
                "stage": "cross_period_linker",
                "input_refs": [f"final_reports:{_safe_len(state.get('final_reports'))}"],
                "output_refs": [f"cross_period_links:{_safe_len(links)}"],
                "version_info": {
                    "cross_period_linker_version": "1.0.0",
                },
                "stats": {
                    "links_count": _safe_len(links),
                },
            }
        except Exception:
            return None

    # ============================================================
    # 内部：最终输出与ID提取
    # ============================================================
    def _extract_final_outputs(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """提取最终输出引用"""
        try:
            final_reports = state.get("final_reports", []) or state.get("str_reports", []) or []
            report_ids = [r.get("report_id", "") for r in final_reports
                          if isinstance(r, dict) and r.get("report_id")]
            return {
                "str_reports": report_ids,
                "report_count": _safe_len(final_reports),
                "exported_files": [],  # 导出文件由 batch_exporter 单独记录
                "compliance_summary": state.get("compliance_summary", ""),
            }
        except Exception:
            return {}

    def _extract_report_ids(self, state: Dict[str, Any]) -> List[str]:
        """提取所有报告ID（用于索引）"""
        try:
            ids = []
            for key in ("final_reports", "str_reports", "rejected_reports"):
                reports = state.get(key, []) or []
                for r in reports:
                    if isinstance(r, dict):
                        rid = r.get("report_id")
                        if rid and rid not in ids:
                            ids.append(rid)
            return ids
        except Exception:
            return []

    def _extract_transaction_ids(self, state: Dict[str, Any]) -> List[str]:
        """提取所有交易ID（用于索引）"""
        try:
            ids = []
            # 从原始交易提取
            for t in (state.get("transactions") or []):
                if isinstance(t, dict):
                    tid = t.get("transaction_id")
                    if tid and tid not in ids:
                        ids.append(tid)
            # 从可疑交易提取（含原始交易）
            for key in ("rule_hits", "llm_confirmed", "false_positives"):
                for s in (state.get(key) or []):
                    if isinstance(s, dict):
                        t = s.get("transaction") or {}
                        if isinstance(t, dict):
                            tid = t.get("transaction_id")
                            if tid and tid not in ids:
                                ids.append(tid)
            return ids
        except Exception:
            return []

    # ============================================================
    # 内部：索引维护
    # ============================================================
    def _load_index(self) -> Dict[str, Any]:
        """加载主索引"""
        try:
            if not os.path.exists(self.index_path):
                return {"entries": [], "updated_at": _now_iso()}
            with open(self.index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"entries": [], "updated_at": _now_iso()}

    def _save_index(self, index: Dict[str, Any]) -> None:
        """保存主索引"""
        try:
            with open(self.index_path, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [血缘追踪] 索引保存失败: {e}")

    def _update_index(self, record: Dict[str, Any]) -> None:
        """更新主索引"""
        try:
            index = self._load_index()
            entry = {
                "lineage_id": record["lineage_id"],
                "execution_id": record["execution_id"],
                "timestamp": record["timestamp"],
                "created_at_ts": record.get("created_at_ts", time.time()),
                "import_batch_id": record.get("import_batch_id", ""),
                "report_ids": record.get("report_ids", []),
                "transaction_count": _safe_len(record.get("transaction_ids", [])),
            }
            index.setdefault("entries", []).append(entry)
            index["updated_at"] = _now_iso()
            self._save_index(index)
        except Exception as e:
            print(f"  [血缘追踪] 索引更新失败: {e}")

    def _write_index_entry(self, path: str, lineage_id: str,
                           execution_id: str, timestamp: str) -> None:
        """写入报告索引文件（一个报告对应一个血缘）"""
        try:
            entry = {
                "lineage_id": lineage_id,
                "execution_id": execution_id,
                "timestamp": timestamp,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [血缘追踪] 报告索引写入失败: {e}")

    def _append_transaction_index(self, transaction_id: str,
                                   lineage_id: str, execution_id: str,
                                   timestamp: str) -> None:
        """追加交易索引（一个交易可对应多个血缘）"""
        try:
            idx_path = os.path.join(self.by_transaction_dir, f"{transaction_id}.json")
            # 读取现有
            existing = {"lineage_ids": [], "entries": []}
            if os.path.exists(idx_path):
                try:
                    with open(idx_path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    existing = {"lineage_ids": [], "entries": []}

            if lineage_id not in existing.get("lineage_ids", []):
                existing.setdefault("lineage_ids", []).append(lineage_id)
                existing.setdefault("entries", []).append({
                    "lineage_id": lineage_id,
                    "execution_id": execution_id,
                    "timestamp": timestamp,
                })
                with open(idx_path, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [血缘追踪] 交易索引追加失败: {e}")

    def _load_record(self, lineage_id: str) -> Optional[Dict[str, Any]]:
        """加载单条血缘记录"""
        try:
            if not lineage_id:
                return None
            path = os.path.join(self.records_dir, f"{lineage_id}.json")
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None


# ============================================================
# 模块级单例（便于 workflow 集成）
# ============================================================
try:
    _lineage_tracker_instance = DataLineageTracker()
except Exception:
    _lineage_tracker_instance = None


def get_lineage_tracker() -> Optional[DataLineageTracker]:
    """获取血缘追踪器单例（戒律 P4: 失败返回 None 不抛异常）"""
    return _lineage_tracker_instance
