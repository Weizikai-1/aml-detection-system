"""
条件判断逻辑 - 控制工作流的条件分支

参考 TradingAgents 的 ConditionalLogic 类模式
每个条件函数接收 state，返回下一个节点的名称字符串
"""
from graph.state import AMLState


class ConditionalLogic:
    """
    反洗钱工作流的条件判断逻辑

    包含两个关键分支点:
    1. 规则引擎后: 有可疑 → 图分析 / 无可疑 → END
    2. LLM深审后: 有确认可疑 → 报告生成 / 全是误报 → END
    """

    def __init__(self, min_rule_hits: int = 1):
        """
        Args:
            min_rule_hits: 触发图分析的最小规则命中数
        """
        self.min_rule_hits = min_rule_hits

    def should_continue_after_rule_engine(self, state: AMLState) -> str:
        """
        规则引擎执行后的条件判断

        规则命中数 > 0 → 进入图分析
        规则命中数 = 0 → 结束

        戒律 P1: 规则引擎节点失败时（_node_meta.status == "error"），降级处理：
        - 若有交易数据，仍进入图分析（让图分析尝试发现风险）
        - 避免因规则引擎异常而静默跳过整个后续流程

        Returns:
            "图分析" 或 "END"
        """
        # 检测规则引擎节点是否失败
        node_meta = state.get("_node_meta", {}) or {}
        node_failed = (
            node_meta.get("node") == "规则引擎"
            and node_meta.get("status") == "error"
        )

        hit_count = state.get("rule_hit_count", 0) or 0
        # 降级1：rule_hit_count 缺失或为0时，尝试用 rule_hits 列表长度
        if hit_count == 0:
            rule_hits = state.get("rule_hits", []) or []
            if rule_hits:
                hit_count = len(rule_hits)

        if hit_count >= self.min_rule_hits:
            return "图分析"

        # 降级2：规则引擎节点失败但有交易数据时，仍进入图分析（戒律 P1: 不遗漏）
        if node_failed:
            transactions = state.get("transactions", []) or []
            cleaned = state.get("cleaned_transactions", []) or []
            if len(transactions) > 0 or len(cleaned) > 0:
                return "图分析"

        return "END"

    def should_continue_after_llm_review(self, state: AMLState) -> str:
        """
        LLM深审后的条件判断

        有确认可疑 → 进入报告生成
        全部误报 → 结束

        戒律 P1: LLM 深审节点失败时（llm_reviewed is None 或 _node_meta.status == "error"），
        降级到规则命中结果，避免因 LLM 不可用而遗漏高风险交易报告。
        降级生成的报告会在 report_generator 中标记 degraded=True，强制人工审核。

        Returns:
            "报告生成" 或 "END"
        """
        confirmed = state.get("llm_confirmed", []) or []
        if len(confirmed) > 0:
            return "报告生成"
        # 降级：LLM 未审核（llm_reviewed is None）但有规则命中时，仍生成报告
        # 避免LLM不可用时遗漏高风险交易（戒律 P1）
        llm_reviewed = state.get("llm_reviewed")
        if llm_reviewed is None:
            rule_hits = state.get("rule_hits", []) or []
            if len(rule_hits) > 0:
                return "报告生成"
        return "END"
