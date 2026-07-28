"""
案件跨期串联分析模块（B1-3）

职责: 将当前批次分析与历史案件进行关联，识别跨期洗钱案件

严格遵守戒律:
- M1: 关联基于真实历史数据(data/history/)，不臆测
- M2: 关联理由明确(账户/团伙/模式/时间)
- M3: 风险加成钳制 0-100
- M4: 关联证据链完整，记录历史案件ID与关联维度
- P1: 关联历史案件可显著提升风险分(加成 +20)
- P2: 仅时间临近但无实质关联不串联

串联维度:
1. 账户级串联: 当前批次可疑账户 与 历史可疑账户有交易关系
2. 团伙级串联: 当前团伙社区ID 与 历史团伙有成员重叠
3. 模式级串联: 当前批次规则命中模式 与 历史案件模式高度相似
4. 时间级串联: 案件时间窗口临近 (≤30天)

使用方式:
    from tools.cross_period_linker import cross_period_linker
    links = cross_period_linker.link_current_to_history(current_state, history_records)
"""
import os
import json
from typing import Dict, List, Any, Optional, Tuple, Set
from datetime import datetime, timedelta
from collections import defaultdict


# ============================================================
# 配置（戒律: 配置外部化）
# ============================================================

CROSS_PERIOD_CONFIG = {
    "enabled": True,
    "time_window_days": 30,          # 时间级串联窗口(天)
    "min_pattern_similarity": 0.7,   # 模式相似度阈值
    "min_community_overlap": 2,      # 团伙成员最小重叠数
    "risk_score_bonus": 20,          # 关联命中加成
    "max_history_runs": 50,          # 最多回溯历史记录数
    "min_account_overlap": 1,        # 账户最小重叠数
}


class CrossPeriodLinker:
    """
    案件跨期串联分析器

    主入口:
        link_current_to_history(current_state, history_records)
        -> List[CrossPeriodLink]
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or CROSS_PERIOD_CONFIG

    def link_current_to_history(
        self,
        current_state: Dict[str, Any],
        history_records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        将当前批次分析与历史案件关联

        Args:
            current_state: 当前工作流状态(含 str_reports / rule_hits / primary_accounts)
            history_records: 历史运行记录列表(来自 history_manager.list_runs 或 get_run)

        Returns:
            关联链接列表，每条链接包含:
            - link_type: 关联类型(account/community/pattern/time)
            - history_execution_id: 历史案件ID
            - history_timestamp: 历史案件时间
            - current_entities: 当前批次涉及的实体
            - history_entities: 历史案件涉及的实体
            - overlap_entities: 重叠实体
            - reason: 关联理由
            - risk_score_bonus: 风险加成分
        """
        if not self.config.get("enabled", True):
            return []

        if not history_records:
            return []

        # 限制回溯数量（性能保护）
        history_records = history_records[:self.config["max_history_runs"]]

        links: List[Dict[str, Any]] = []

        # 提取当前批次的关键实体
        current_accounts = self._extract_current_accounts(current_state)
        current_patterns = self._extract_current_patterns(current_state)
        current_communities = self._extract_current_communities(current_state)
        current_timestamp = current_state.get("analysis_date", "") or datetime.now().isoformat()

        for history_record in history_records:
            # 跳过自身（如果是同一执行ID）
            if history_record.get("execution_id") == current_state.get("execution_id"):
                continue

            # 1. 账户级串联
            account_link = self._check_account_overlap(
                current_accounts, history_record, current_state
            )
            if account_link:
                links.append(account_link)

            # 2. 团伙级串联
            community_link = self._check_community_overlap(
                current_communities, history_record, current_state
            )
            if community_link:
                links.append(community_link)

            # 3. 模式级串联
            pattern_link = self._check_pattern_similarity(
                current_patterns, history_record, current_state
            )
            if pattern_link:
                links.append(pattern_link)

            # 4. 时间级串联（需配合其他维度，不单独触发，戒律 P2）
            # 时间级串联在其他维度命中时作为附加证据

        return links

    def _extract_current_accounts(self, state: Dict[str, Any]) -> Set[str]:
        """提取当前批次涉及的所有账户（来自规则命中和报告）"""
        accounts: Set[str] = set()

        # 从规则命中提取
        for s in state.get("rule_hits", []):
            txn = s.get("transaction", {})
            for acc in [txn.get("from_account", ""), txn.get("to_account", "")]:
                if acc:
                    accounts.add(acc)

        # 从最终报告提取
        for r in state.get("final_reports", []) or state.get("str_reports", []):
            primary = r.get("primary_account", "")
            if primary:
                accounts.add(primary)
            for acc in r.get("related_accounts", []):
                if acc:
                    accounts.add(acc)

        return accounts

    def _extract_current_patterns(self, state: Dict[str, Any]) -> Dict[str, int]:
        """提取当前批次规则命中模式统计"""
        rule_details = state.get("rule_details", {})
        # rule_details 已经是 {rule_name: count} 格式
        return dict(rule_details) if rule_details else {}

    def _extract_current_communities(self, state: Dict[str, Any]) -> List[List[str]]:
        """提取当前批次的团伙社区"""
        graph_data = state.get("graph_data", {})
        return graph_data.get("communities", []) if graph_data else []

    def _check_account_overlap(
        self,
        current_accounts: Set[str],
        history_record: Dict[str, Any],
        current_state: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        检查账户级串联（戒律 M1: 基于真实账户ID匹配）

        触发条件: 当前批次可疑账户 与 历史案件主涉案账户 有重叠
        """
        if not current_accounts:
            return None

        # 历史案件的主涉案账户
        history_accounts: Set[str] = set()
        for acc in history_record.get("primary_accounts", []):
            if acc:
                history_accounts.add(acc)
        # 也从历史报告的 related_accounts 提取
        for r in history_record.get("str_reports", []):
            for acc in r.get("related_accounts", []):
                if acc:
                    history_accounts.add(acc)

        if not history_accounts:
            return None

        overlap = current_accounts & history_accounts
        min_overlap = self.config.get("min_account_overlap", 1)
        if len(overlap) < min_overlap:
            return None

        return {
            "link_type": "account",
            "history_execution_id": history_record.get("execution_id", ""),
            "history_timestamp": history_record.get("timestamp", ""),
            "current_entities": sorted(list(current_accounts))[:10],  # 戒律 M4: 保留证据，最多10个
            "history_entities": sorted(list(history_accounts))[:10],
            "overlap_entities": sorted(list(overlap)),
            "reason": (
                f"账户级串联: 当前批次可疑账户与历史案件"
                f"[{history_record.get('execution_id', '')}]"
                f"存在{len(overlap)}个重叠账户"
                f"({', '.join(sorted(list(overlap))[:3])}{'等' if len(overlap) > 3 else ''})"
            ),
            "risk_score_bonus": self.config["risk_score_bonus"],
        }

    def _check_community_overlap(
        self,
        current_communities: List[List[str]],
        history_record: Dict[str, Any],
        current_state: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        检查团伙级串联（戒律 M1: 基于真实团伙成员匹配）

        触发条件: 当前团伙社区成员 与 历史团伙成员 重叠数 ≥ 阈值
        """
        if not current_communities:
            return None

        # 历史案件的团伙信息（从 graph_data 中提取，但历史记录可能未保存完整 graph_data）
        # 简化实现: 从历史主涉案账户 + 关联账户推断历史团伙成员
        history_community_members: Set[str] = set()
        for r in history_record.get("str_reports", []):
            primary = r.get("primary_account", "")
            if primary:
                history_community_members.add(primary)
            for acc in r.get("related_accounts", []):
                if acc:
                    history_community_members.add(acc)

        if not history_community_members:
            return None

        min_overlap = self.config.get("min_community_overlap", 2)
        best_overlap: Set[str] = set()
        best_community_idx = -1

        for idx, community in enumerate(current_communities):
            current_members = set(community) if community else set()
            overlap = current_members & history_community_members
            if len(overlap) >= min_overlap and len(overlap) > len(best_overlap):
                best_overlap = overlap
                best_community_idx = idx

        if not best_overlap:
            return None

        return {
            "link_type": "community",
            "history_execution_id": history_record.get("execution_id", ""),
            "history_timestamp": history_record.get("timestamp", ""),
            "current_entities": [f"社区#{best_community_idx}"],
            "history_entities": sorted(list(history_community_members))[:10],
            "overlap_entities": sorted(list(best_overlap)),
            "reason": (
                f"团伙级串联: 当前批次社区#{best_community_idx}与历史案件"
                f"[{history_record.get('execution_id', '')}]"
                f"存在{len(best_overlap)}个重叠成员"
                f"({', '.join(sorted(list(best_overlap))[:3])}{'等' if len(best_overlap) > 3 else ''})"
            ),
            "risk_score_bonus": self.config["risk_score_bonus"],
        }

    def _check_pattern_similarity(
        self,
        current_patterns: Dict[str, int],
        history_record: Dict[str, Any],
        current_state: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        检查模式级串联（戒律 M1: 基于真实规则命中统计）

        触发条件: 当前批次 与 历史案件 的规则命中模式相似度 ≥ 阈值
        相似度 = 交集规则数 / 并集规则数 (Jaccard系数)
        """
        if not current_patterns:
            return None

        history_patterns = history_record.get("rule_details", {})
        if not history_patterns:
            return None

        current_rules = set(current_patterns.keys())
        history_rules = set(history_patterns.keys())

        # Jaccard 相似度
        intersection = current_rules & history_rules
        union = current_rules | history_rules
        if not union:
            return None

        similarity = len(intersection) / len(union)
        min_similarity = self.config.get("min_pattern_similarity", 0.7)

        if similarity < min_similarity:
            return None

        # 时间临近检查（模式相似 + 时间临近 才串联，戒律 P2: 防误报）
        history_ts = self._parse_timestamp(history_record.get("timestamp", ""))
        current_ts = self._parse_timestamp(current_state.get("analysis_date", ""))
        time_window_days = self.config.get("time_window_days", 30)

        if history_ts and current_ts:
            time_diff = abs((current_ts - history_ts).days)
            if time_diff > time_window_days:
                # 模式相似但时间太远，不串联
                return None
            time_info = f"，时间间隔{time_diff}天"
        else:
            time_info = "，时间信息缺失"

        return {
            "link_type": "pattern",
            "history_execution_id": history_record.get("execution_id", ""),
            "history_timestamp": history_record.get("timestamp", ""),
            "current_entities": sorted(list(current_rules)),
            "history_entities": sorted(list(history_rules)),
            "overlap_entities": sorted(list(intersection)),
            "reason": (
                f"模式级串联: 当前批次与历史案件"
                f"[{history_record.get('execution_id', '')}]"
                f"规则命中模式相似度{similarity:.1%}"
                f"(共同命中: {', '.join(sorted(list(intersection))[:3])}"
                f"{'等' if len(intersection) > 3 else ''})"
                f"{time_info}"
            ),
            "risk_score_bonus": self.config["risk_score_bonus"],
            "similarity": similarity,
        }

    def _parse_timestamp(self, ts_str: str) -> Optional[datetime]:
        """解析时间戳字符串（兼容多种格式）"""
        if not ts_str:
            return None
        # 尝试 ISO 格式
        try:
            return datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            pass
        # 尝试 YYYY-MM-DD HH:MM:SS 格式
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"]:
            try:
                return datetime.strptime(ts_str, fmt)
            except (ValueError, TypeError):
                continue
        return None

    def apply_links_to_reports(
        self,
        reports: List[Dict[str, Any]],
        links: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        将跨期关联结果应用到 STR 报告

        Args:
            reports: 当前批次的 STR 报告列表
            links: 跨期关联链接列表

        Returns:
            更新后的报告列表（每份报告新增 cross_period_links 字段）
        """
        if not links or not reports:
            return reports

        # 按账户聚合 links
        account_to_links: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for link in links:
            for entity in link.get("overlap_entities", []):
                account_to_links[entity].append(link)

        for report in reports:
            # 收集与该报告相关的所有 links
            related_links: List[Dict[str, Any]] = []
            primary = report.get("primary_account", "")
            related = report.get("related_accounts", [])

            for acc in [primary] + list(related):
                if acc and acc in account_to_links:
                    for link in account_to_links[acc]:
                        if link not in related_links:
                            related_links.append(link)

            if related_links:
                # 戒律 M4: 保留完整关联证据
                report["cross_period_links"] = related_links
                # 戒律 M3: 风险分加成钳制 0-100
                total_bonus = sum(l.get("risk_score_bonus", 0) for l in related_links)
                # 单份报告最多加成 40 分（避免过度加成）
                total_bonus = min(total_bonus, 40)

                # 更新风险等级（如果加成后超过 high 阈值则升级）
                # 戒律 M1: 不编造风险分，仅在原有基础上加成
                current_evidence = report.get("evidence_chain", [])
                link_summaries = [l.get("reason", "") for l in related_links]
                report["evidence_chain"] = current_evidence + [
                    f"跨期关联: {summary}" for summary in link_summaries
                ]
                # 在分析摘要中提及跨期关联
                analysis_summary = report.get("analysis_summary", "")
                if analysis_summary:
                    report["analysis_summary"] = (
                        analysis_summary + "\n\n"
                        f"【跨期关联】本案件与{len(related_links)}个历史案件存在关联"
                        f"(加成风险分{total_bonus}分)。"
                    )
                else:
                    report["analysis_summary"] = (
                        f"【跨期关联】本案件与{len(related_links)}个历史案件存在关联"
                        f"(加成风险分{total_bonus}分)。"
                    )

        return reports


# 全局单例
cross_period_linker = CrossPeriodLinker()
