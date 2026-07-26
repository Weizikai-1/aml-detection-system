"""
工作流图构建 - GraphSetup

参考 TradingAgents 的 GraphSetup 类模式
负责: 创建所有Agent节点 → 添加边 → 添加条件边 → 返回 workflow
"""
from typing import Any, Dict
from langgraph.graph import END, START, StateGraph

from agents import (
    create_data_preprocessor_agent,
    create_rule_engine_agent,
    create_graph_analyst_agent,
    create_llm_reviewer_agent,
    create_report_generator_agent,
    create_compliance_auditor_agent,
)
from graph.state import AMLState
from graph.conditional_logic import ConditionalLogic


class GraphSetup:
    """
    构建反洗钱多Agent工作流图

    工作流:
    START → 数据预处理 → 规则引擎 → [条件1]
                                      ↓
                          有可疑 → 图分析 → LLM深审 → [条件2]
                            ↑                              ↓
                          END                       有确认 → 报告生成 → 合规审核 → END
    """

    def __init__(
        self,
        llm: Any = None,
        conditional_logic: ConditionalLogic = None,
    ):
        """
        Args:
            llm: LLM实例，传入所有需要LLM的Agent
            conditional_logic: 条件逻辑实例
        """
        self.llm = llm
        self.conditional_logic = conditional_logic or ConditionalLogic()

    def setup_graph(self) -> StateGraph:
        """
        构建完整的反洗钱工作流图

        Returns:
            已配置好节点和边的 StateGraph 实例(未编译)
        """
        # 创建各Agent节点
        data_preprocessor = create_data_preprocessor_agent(self.llm)
        rule_engine = create_rule_engine_agent(self.llm)
        graph_analyst = create_graph_analyst_agent(self.llm)
        llm_reviewer = create_llm_reviewer_agent(self.llm)
        report_generator = create_report_generator_agent(self.llm)
        compliance_auditor = create_compliance_auditor_agent(self.llm)

        # 创建工作流
        workflow = StateGraph(AMLState)

        # 添加节点
        workflow.add_node("数据预处理", data_preprocessor)
        workflow.add_node("规则引擎", rule_engine)
        workflow.add_node("图分析", graph_analyst)
        workflow.add_node("LLM深审", llm_reviewer)
        workflow.add_node("报告生成", report_generator)
        workflow.add_node("合规审核", compliance_auditor)

        # 添加边: 固定顺序
        # START → 数据预处理 → 规则引擎
        workflow.add_edge(START, "数据预处理")
        workflow.add_edge("数据预处理", "规则引擎")

        # 条件边1: 规则引擎 → 图分析 / END
        workflow.add_conditional_edges(
            "规则引擎",
            self.conditional_logic.should_continue_after_rule_engine,
            {
                "图分析": "图分析",
                "END": END,
            },
        )

        # 固定边: 图分析 → LLM深审
        workflow.add_edge("图分析", "LLM深审")

        # 条件边2: LLM深审 → 报告生成 / END
        workflow.add_conditional_edges(
            "LLM深审",
            self.conditional_logic.should_continue_after_llm_review,
            {
                "报告生成": "报告生成",
                "END": END,
            },
        )

        # 固定边: 报告生成 → 合规审核 → END
        workflow.add_edge("报告生成", "合规审核")
        workflow.add_edge("合规审核", END)

        return workflow
