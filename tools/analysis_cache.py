"""
分析结果缓存

对相同输入数据跳过重复计算，提升二次分析速度。

戒律:
- M1: 缓存的是真实计算结果，不是编造
- P1: 缓存命中时直接返回结果，不遗漏
- P2: 配置变化时缓存自动失效，不用过期结果
"""
import os
import json
import hashlib
import time
import copy
from typing import Any, Dict, Optional


class AnalysisCache:
    """分析结果缓存管理器"""

    def __init__(
        self,
        cache_dir: str = None,
        enabled: bool = False,
        expire_days: int = 7,
        max_size_mb: int = 100,
    ):
        """
        Args:
            cache_dir: 缓存目录，None时使用默认路径
            enabled: 是否启用缓存
            expire_days: 缓存过期天数
            max_size_mb: 最大缓存大小(MB)
        """
        if cache_dir is None:
            from config import CACHE_DIR
            cache_dir = CACHE_DIR
        self.cache_dir = cache_dir
        self.enabled = enabled
        self.expire_seconds = expire_days * 24 * 3600
        self.max_size_bytes = max_size_mb * 1024 * 1024
        os.makedirs(self.cache_dir, exist_ok=True)

    def _compute_key(
        self,
        transactions: list,
        config_snapshot: dict,
    ) -> str:
        """
        计算缓存key: 交易数据哈希 + 配置快照哈希

        Args:
            transactions: 交易列表
            config_snapshot: 影响结果的配置项快照

        Returns:
            缓存key字符串
        """
        # 交易数据哈希（只取关键字段，避免顺序影响）
        # 戒律 M4: 必须包含所有影响分析结果的字段，否则配置变化时缓存错误命中
        txn_data = []
        for t in transactions:
            txn_data.append({
                "id": t.get("transaction_id", ""),
                "from": t.get("from_account", ""),
                "to": t.get("to_account", ""),
                "amount": round(float(t.get("amount") or 0), 2),
                "ts": t.get("timestamp", ""),
                "remark": t.get("remark", ""),
                "transaction_type": t.get("transaction_type", ""),
                "currency": t.get("currency", ""),
                "channel": t.get("channel", ""),
                "status": t.get("status", ""),
            })
        # 排序后哈希，保证顺序无关
        txn_data.sort(key=lambda x: x["id"])
        txn_str = json.dumps(txn_data, sort_keys=True, ensure_ascii=False)
        txn_hash = hashlib.sha256(txn_str.encode("utf-8")).hexdigest()[:16]

        # 配置哈希
        cfg_str = json.dumps(config_snapshot, sort_keys=True, ensure_ascii=False)
        cfg_hash = hashlib.sha256(cfg_str.encode("utf-8")).hexdigest()[:8]

        return f"{txn_hash}_{cfg_hash}"

    def get(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """
        读取缓存

        Returns:
            缓存的结果字典，未命中返回 None
        """
        if not self.enabled:
            return None

        cache_path = os.path.join(self.cache_dir, f"{cache_key}.json")
        if not os.path.exists(cache_path):
            return None

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 检查缓存是否过期
            cached_time = data.get("_cached_at", 0)
            if time.time() - cached_time > self.expire_seconds:
                # 过期则删除
                try:
                    os.remove(cache_path)
                except Exception:
                    pass
                return None
            # 标记缓存命中（供调用方统计）
            result = data.get("result")
            if isinstance(result, dict):
                # 戒律 P4: 深拷贝避免上层修改污染缓存内容
                result = copy.deepcopy(result)
                result["_cache_hit"] = True
            return result
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def set(self, cache_key: str, result: Dict[str, Any]):
        """写入缓存"""
        if not self.enabled:
            return

        # 写入前检查容量，必要时清理最旧的
        self._enforce_size_limit()

        cache_path = os.path.join(self.cache_dir, f"{cache_key}.json")
        # 移除内部标记字段，避免污染缓存
        clean_result = {k: v for k, v in result.items() if not k.startswith("_cache")}
        data = {
            "_cached_at": time.time(),
            "_cache_key": cache_key,
            "result": clean_result,
        }
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=str)
        except Exception as e:
            # 戒律 M4: 缓存写入失败需记录日志，可追溯
            print(f"  [缓存] 写入失败: {e}")

    def _enforce_size_limit(self):
        """检查缓存总大小，超过限制时删除最旧的文件"""
        try:
            files = []
            total_size = 0
            for f in os.listdir(self.cache_dir):
                if not f.endswith(".json"):
                    continue
                fp = os.path.join(self.cache_dir, f)
                size = os.path.getsize(fp)
                mtime = os.path.getmtime(fp)
                files.append((fp, size, mtime))
                total_size += size

            if total_size <= self.max_size_bytes:
                return

            # 按修改时间排序，从最旧开始删除
            files.sort(key=lambda x: x[2])
            for fp, size, _ in files:
                if total_size <= self.max_size_bytes * 0.9:  # 清到90%以下
                    break
                try:
                    os.remove(fp)
                    total_size -= size
                except Exception:
                    pass
        except Exception as e:
            # 戒律 M4: 清理失败需记录日志，可追溯
            print(f"[缓存] 清理失败: {e}")

    def clear(self):
        """清空所有缓存"""
        if not os.path.exists(self.cache_dir):
            return
        for f in os.listdir(self.cache_dir):
            if f.endswith(".json"):
                try:
                    os.remove(os.path.join(self.cache_dir, f))
                except Exception:
                    pass

    def stats(self) -> dict:
        """缓存统计信息"""
        if not os.path.exists(self.cache_dir):
            return {"count": 0, "size_mb": 0}
        files = [f for f in os.listdir(self.cache_dir) if f.endswith(".json")]
        total_size = 0
        for f in files:
            try:
                total_size += os.path.getsize(os.path.join(self.cache_dir, f))
            except Exception:
                pass
        return {
            "count": len(files),
            "size_mb": round(total_size / 1024 / 1024, 2),
            "enabled": self.enabled,
        }


def build_config_snapshot(aml_config: dict) -> dict:
    """
    从全局配置中提取影响规则引擎结果的配置快照

    只取 rules 部分（baseline_deviation / remark_keywords / shell_company /
    smurfing / fast_in_fast_out / round_trip / large_amount），因为只有这些
    会影响规则检测的输出。其他配置（如 LLM、GNN）不影响规则引擎结果。

    Args:
        aml_config: AML_CONFIG 字典

    Returns:
        配置快照字典
    """
    rules = aml_config.get("rules", {})
    # 只取关键阈值字段，避免快照过大
    snapshot = {}
    for rule_name, rule_cfg in rules.items():
        if isinstance(rule_cfg, dict):
            # 递归取基本类型值
            snapshot[rule_name] = _extract_scalars(rule_cfg)
        else:
            snapshot[rule_name] = rule_cfg
    return snapshot


def _extract_scalars(d: dict) -> dict:
    """提取字典中的基本类型值（int/float/str/bool/list），跳过嵌套dict"""
    result = {}
    for k, v in d.items():
        if isinstance(v, (int, float, str, bool, list, tuple)):
            result[k] = list(v) if isinstance(v, tuple) else v
        elif isinstance(v, dict):
            # 递归一层
            result[k] = _extract_scalars(v)
    return result
