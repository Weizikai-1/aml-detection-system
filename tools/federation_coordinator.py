"""
多机构联邦分析协调器 (Federation Coordinator)

职责:
- 跨机构数据脱敏共享（差分隐私 + 哈希脱敏）
- 联邦 GNN 参数聚合（FedAvg）
- 统一案件 ID 映射
- 跨机构审计日志同步

设计原则:
- M1: 仅共享脱敏后的统计特征/模型参数，原始数据不出域
- M2: 共享信号附明确来源机构与置信度
- M4: 联邦聚合过程完整记录（参与机构/参数版本/聚合策略）
- P1: 单机构参数异常不影响全局模型
- P4: 单机构失败不影响其他机构

说明:
- 初版实现核心接口 + 本地模拟，实际部署需配套联邦学习基础设施（如 Flower / PySyft）
- 差分隐私采用拉普拉斯机制（Laplace mechanism）
- 案件 ID 映射采用 SHA256 哈希（不可逆）
"""
import hashlib
import json
import math
import os
import random
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# 模块版本（戒律 M4: 可追溯）
__FEDERATION_COORDINATOR_VERSION__ = "1.0.0"


def _now_iso() -> str:
    """当前时间 ISO 格式"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _laplace_noise(sensitivity: float, epsilon: float) -> float:
    """
    拉普拉斯噪声（差分隐私）

    Args:
        sensitivity: 查询敏感度（数据变化对结果的最大影响）
        epsilon: 隐私预算（越小隐私保护越强）

    Returns:
        噪声值
    """
    if epsilon <= 0:
        return 0.0
    scale = sensitivity / epsilon
    # 拉普拉斯分布采样: -scale * sign(U-0.5) * log(1 - 2|U-0.5|)
    u = random.random() - 0.5
    return -scale * math.copysign(math.log(1 - 2 * abs(u)), u)


def _hash_identifier(identifier: str, salt: str = "") -> str:
    """
    哈希脱敏标识符（SHA256 + salt）

    用于案件 ID / 账户 ID 跨机构映射，不可逆

    Args:
        identifier: 原始标识符（账户号/案件ID）
        salt: 盐值（机构特定，防止彩虹表攻击）

    Returns:
        16 字符哈希摘要
    """
    if not identifier:
        return ""
    raw = f"{salt}:{identifier}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


class FederationCoordinator:
    """
    多机构联邦分析协调器

    主入口:
        aggregate_gnn_params(local_params_list) -> Dict (FedAvg)
        share_sanction_signals(local_signals) -> Dict (脱敏共享)
        map_cross_institution_case(case_id, institution_id) -> str (统一案件映射)
        sync_audit_logs(local_logs) -> Dict (跨机构审计同步)

    戒律遵守:
    - M1: 仅共享脱敏后的统计特征/模型参数
    - M4: 聚合过程完整记录
    - P1: 单机构参数异常不影响全局模型
    - P4: 单机构失败不影响其他
    """

    def __init__(
        self,
        institution_id: str = "LOCAL",
        federation_config: Dict[str, Any] = None,
    ):
        """
        Args:
            institution_id: 本机构ID
            federation_config: 联邦配置
                - epsilon: 差分隐私预算（默认 1.0）
                - min_participants: 最少参与机构数（默认 2）
                - aggregation_strategy: 聚合策略（fedavg / weighted）
        """
        self.institution_id = institution_id
        cfg = federation_config or {}
        self.epsilon = float(cfg.get("epsilon", 1.0))
        self.min_participants = int(cfg.get("min_participants", 2))
        self.aggregation_strategy = cfg.get("aggregation_strategy", "fedavg")
        # 案件 ID 映射表（institution_case_id → global_case_id）
        self._case_mapping: Dict[str, str] = {}
        # 参与机构记录（戒律 M4）
        self._participants: List[Dict[str, Any]] = []

    # ============================================================
    # FedAvg: 联邦 GNN 参数聚合
    # ============================================================
    def aggregate_gnn_params(
        self,
        local_params_list: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        FedAvg 聚合多机构的 GNN 参数

        Args:
            local_params_list: 各机构的本地参数列表，每项含:
                - institution_id: 机构ID
                - params: 参数字典（参数名 → 数值/数组）
                - sample_count: 训练样本数（用于加权）
                - model_version: 模型版本

        Returns:
            {
                "global_params": Dict,  # 聚合后全局参数
                "aggregation_strategy": str,
                "participant_count": int,
                "total_samples": int,
                "aggregated_at": str,
                "coordinator_version": str,
                "rejected": List[str],  # 被拒绝的机构（参数异常）
            }

        戒律:
        - M1: 仅聚合模型参数，不接触原始数据
        - M4: 完整记录参与机构与聚合策略
        - P1: 异常参数机构被拒绝，不影响全局模型
        - P4: 单机构参数解析失败被跳过
        """
        result = {
            "global_params": {},
            "aggregation_strategy": self.aggregation_strategy,
            "participant_count": 0,
            "total_samples": 0,
            "aggregated_at": _now_iso(),
            "coordinator_version": __FEDERATION_COORDINATOR_VERSION__,
            "rejected": [],
        }

        try:
            if not local_params_list:
                return result

            if len(local_params_list) < self.min_participants:
                result["rejected"].append(
                    f"参与机构数 {len(local_params_list)} 少于最小要求 {self.min_participants}"
                )
                return result

            # 过滤异常机构（戒律 P1: 异常参数不影响全局）
            valid_entries = []
            for entry in local_params_list:
                if not isinstance(entry, dict):
                    continue
                params = entry.get("params")
                if not isinstance(params, dict) or not params:
                    result["rejected"].append(
                        entry.get("institution_id", "unknown")
                    )
                    continue
                valid_entries.append(entry)

            if not valid_entries:
                return result

            # 计算总样本数（用于加权）
            total_samples = sum(
                int(e.get("sample_count", 0)) for e in valid_entries
            )

            # 收集所有参数名
            param_names = set()
            for e in valid_entries:
                param_names.update(e.get("params", {}).keys())

            # FedAvg: 按样本数加权平均
            global_params = {}
            for pname in param_names:
                weighted_sum = 0.0
                total_weight = 0.0
                for e in valid_entries:
                    p = e.get("params", {}).get(pname)
                    if p is None:
                        continue
                    sample_count = max(int(e.get("sample_count", 0)), 1)
                    try:
                        # 数值型参数加权平均
                        if isinstance(p, (int, float)):
                            weighted_sum += float(p) * sample_count
                            total_weight += sample_count
                        elif isinstance(p, list):
                            # 列表型参数逐元素加权
                            if pname not in global_params:
                                global_params[pname] = [0.0] * len(p)
                            if len(global_params[pname]) != len(p):
                                continue
                            for i, v in enumerate(p):
                                if isinstance(v, (int, float)):
                                    global_params[pname][i] += float(v) * sample_count
                            total_weight_list = global_params.get(f"_weight_{pname}", 0)
                            global_params[f"_weight_{pname}"] = total_weight_list + sample_count
                    except (TypeError, ValueError):
                        continue

                if total_weight > 0 and isinstance(weighted_sum, (int, float)):
                    global_params[pname] = weighted_sum / total_weight

            # 处理列表型参数的归一化
            keys_to_remove = []
            for key in list(global_params.keys()):
                if key.startswith("_weight_"):
                    real_key = key[len("_weight_"):]
                    if real_key in global_params and isinstance(global_params[real_key], list):
                        w = global_params[key]
                        if w > 0:
                            global_params[real_key] = [v / w for v in global_params[real_key]]
                    keys_to_remove.append(key)
            for k in keys_to_remove:
                global_params.pop(k, None)

            result["global_params"] = global_params
            result["participant_count"] = len(valid_entries)
            result["total_samples"] = total_samples

            # 记录参与机构（戒律 M4）
            self._participants = [
                {
                    "institution_id": e.get("institution_id", "unknown"),
                    "sample_count": int(e.get("sample_count", 0)),
                    "model_version": e.get("model_version", "unknown"),
                }
                for e in valid_entries
            ]
            result["participants"] = self._participants

            return result
        except Exception as e:
            result["rejected"].append(f"聚合异常: {e}")
            return result

    # ============================================================
    # 脱敏共享: 制裁信号
    # ============================================================
    def share_sanction_signals(
        self,
        local_signals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        共享制裁名单信号（脱敏后）

        Args:
            local_signals: 本地检测到的制裁信号列表，每项含:
                - account: 账户号（将被哈希脱敏）
                - country: 涉及国家
                - risk_score: 风险分
                - detected_at: 检测时间

        Returns:
            {
                "shared_signals": List[Dict],  # 脱敏后的信号
                "institution_id": str,
                "signal_count": int,
                "privacy_budget_used": float,
                "shared_at": str,
            }

        戒律:
        - M1: 仅共享脱敏后的统计特征，账户号哈希化
        - M2: 每条信号附来源机构与置信度
        - P4: 单条信号处理失败跳过
        """
        result = {
            "shared_signals": [],
            "institution_id": self.institution_id,
            "signal_count": 0,
            "privacy_budget_used": 0.0,
            "shared_at": _now_iso(),
        }

        try:
            if not local_signals:
                return result

            shared = []
            budget_used = 0.0
            for sig in local_signals:
                if not isinstance(sig, dict):
                    continue
                try:
                    # 账户号哈希脱敏（戒律 M1）
                    account = sig.get("account", "")
                    hashed_account = _hash_identifier(
                        str(account),
                        salt=self.institution_id,
                    )

                    # 风险分加差分隐私噪声（戒律 M1）
                    risk_score = float(sig.get("risk_score", 0))
                    noisy_risk = risk_score + _laplace_noise(
                        sensitivity=1.0, epsilon=self.epsilon
                    )
                    # 限制在 0-100 范围
                    noisy_risk = max(0, min(100, noisy_risk))
                    budget_used += self.epsilon

                    shared.append({
                        "hashed_account": hashed_account,
                        "country": sig.get("country", ""),
                        "risk_score": round(noisy_risk, 2),
                        "detected_at": sig.get("detected_at", ""),
                        "source_institution": self.institution_id,
                        # 戒律 M2: 附来源
                        "confidence": "high" if noisy_risk >= 80 else "medium",
                    })
                except Exception:
                    continue

            result["shared_signals"] = shared
            result["signal_count"] = len(shared)
            result["privacy_budget_used"] = round(budget_used, 4)
            return result
        except Exception as e:
            result["error"] = str(e)
            return result

    # ============================================================
    # 跨机构案件 ID 映射
    # ============================================================
    def map_cross_institution_case(
        self,
        case_id: str,
        institution_id: str = None,
    ) -> str:
        """
        跨机构案件 ID 映射（统一全局 ID）

        将各机构的本地案件 ID 映射为统一的全局 ID，
        便于跨机构协同分析，同时不暴露本地案件编号

        Args:
            case_id: 本地案件ID
            institution_id: 机构ID（默认使用本机构）

        Returns:
            全局案件ID（16 字符哈希）

        戒律:
        - M1: 不共享原始案件ID，使用哈希映射
        - M4: 映射关系本地记录可追溯
        """
        if not case_id:
            return ""
        inst = institution_id or self.institution_id
        global_id = _hash_identifier(str(case_id), salt=f"case:{inst}")

        # 本地记录映射关系（戒律 M4: 可追溯）
        local_key = f"{inst}:{case_id}"
        self._case_mapping[local_key] = global_id

        return global_id

    def lookup_case_mapping(self, local_case_id: str, institution_id: str = None) -> str:
        """查询已建立的案件映射"""
        inst = institution_id or self.institution_id
        local_key = f"{inst}:{local_case_id}"
        return self._case_mapping.get(local_key, "")

    # ============================================================
    # 跨机构审计日志同步
    # ============================================================
    def sync_audit_logs(
        self,
        local_logs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        同步审计日志到联邦（脱敏后）

        Args:
            local_logs: 本地审计日志列表

        Returns:
            {
                "synced_logs": List[Dict],  # 脱敏后可共享的日志
                "synced_count": int,
                "skipped_count": int,
                "synced_at": str,
            }

        戒律:
        - M1: 日志中的账户/用户信息脱敏
        - M4: 同步过程记录
        - P4: 单条日志处理失败跳过
        """
        result = {
            "synced_logs": [],
            "synced_count": 0,
            "skipped_count": 0,
            "synced_at": _now_iso(),
        }

        try:
            if not local_logs:
                return result

            synced = []
            skipped = 0
            for log in local_logs:
                if not isinstance(log, dict):
                    skipped += 1
                    continue
                try:
                    # 脱敏处理
                    synced_log = {
                        "timestamp": log.get("timestamp", ""),
                        "action": log.get("action", ""),
                        "source_institution": self.institution_id,
                    }

                    # 账户信息哈希
                    account = log.get("account") or log.get("user")
                    if account:
                        synced_log["hashed_account"] = _hash_identifier(
                            str(account), salt=self.institution_id
                        )

                    # 保留非敏感统计信息
                    for key in ("rule_name", "risk_score", "severity", "execution_id"):
                        if key in log:
                            synced_log[key] = log[key]

                    synced.append(synced_log)
                except Exception:
                    skipped += 1
                    continue

            result["synced_logs"] = synced
            result["synced_count"] = len(synced)
            result["skipped_count"] = skipped
            return result
        except Exception as e:
            result["error"] = str(e)
            return result

    # ============================================================
    # 联邦状态查询
    # ============================================================
    def get_federation_status(self) -> Dict[str, Any]:
        """获取联邦协调器状态（戒律 M4: 可追溯）"""
        return {
            "institution_id": self.institution_id,
            "coordinator_version": __FEDERATION_COORDINATOR_VERSION__,
            "aggregation_strategy": self.aggregation_strategy,
            "epsilon": self.epsilon,
            "min_participants": self.min_participants,
            "case_mapping_count": len(self._case_mapping),
            "participants_count": len(self._participants),
            "status_at": _now_iso(),
        }


# ============================================================
# 模块级便捷函数
# ============================================================
def create_federation_coordinator(
    institution_id: str = "LOCAL",
) -> FederationCoordinator:
    """创建联邦协调器实例"""
    return FederationCoordinator(institution_id=institution_id)
