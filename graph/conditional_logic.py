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

        Returns:
            "图分析" 或 "END"
        """
        hit_count = state.get("rule_hit_count", 0)
        if hit_count >= self.min_rule_hits:
            return "图分析"
        return "END"

    def should_continue_after_llm_review(self, state: AMLState) -> str:
        """
        LLM深审后的条件判断

        有确认可疑 → 进入报告生成
        全部误报 → 结束

        Returns:
            "报告生成" 或 "END"
        """
        confirmed = state.get("llm_confirmed", [])
        if len(confirmed) > 0:
            return "报告生成"
        return "END"
