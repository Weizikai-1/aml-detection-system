"""
反洗钱多Agent系统 - 主工作流类

参考 TradingAgents 的 TradingAgentsGraph 主类模式
- 持有 LLM、graph、状态
- 提供 run() 方法执行完整流程
- 管理状态持久化
"""
import time
import os
import json
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional

from graph.state import AMLState, Transaction
from graph.graph_setup import GraphSetup
from graph.conditional_logic import ConditionalLogic
from config import AML_CONFIG


class AMLAgentsGraph:
    """
    反洗钱多Agent系统主类

    负责:
    - 初始化LLM和各Agent
    - 构建和编译LangGraph工作流
    - 执行分析流程
    - 管理状态和结果
    """

    def __init__(
        self,
        llm: Any = None,
        config: Dict[str, Any] = None,
    ):
        """
        初始化反洗钱多Agent系统

        Args:
            llm: LLM实例(DeepSeek等)，为None时使用降级模式
            config: 配置字典，为None时使用默认配置
        """
        self.config = config or AML_CONFIG
        self.llm = llm

        # 初始化组件
        self.conditional_logic = ConditionalLogic(
            min_rule_hits=self.config.get("workflow", {}).get("min_rule_hits_for_graph", 1)
        )

        self.graph_setup = GraphSetup(
            llm=self.llm,
            conditional_logic=self.conditional_logic,
        )

        # 构建工作流
        self.workflow = self.graph_setup.setup_graph()
        self.graph = self.workflow.compile()

        # 状态追踪
        self.curr_state: Optional[Dict] = None
        self.execution_id: Optional[str] = None

    def run(
        self,
        transactions: List[Transaction],
        analysis_date: str = None,
        analysis_params: Dict[str, Any] = None,
        debug: bool = False,
    ) -> Dict[str, Any]:
        """
        执行完整的反洗钱分析流程

        Args:
            transactions: 待分析的交易列表
            analysis_date: 分析日期，默认今天
            analysis_params: 自定义分析参数
            debug: 是否开启调试模式(流式输出)

        Returns:
            最终状态字典，包含所有Agent的输出
        """
        start_time = time.time()
        self.execution_id = str(uuid.uuid4())[:8]

        if analysis_date is None:
            analysis_date = datetime.now().strftime("%Y-%m-%d")

        # 初始化状态
        init_state: AMLState = {
            "transactions": transactions,
            "analysis_date": analysis_date,
            "analysis_params": analysis_params or {},
            "messages": [],
            "current_step": "start",
            "error": "",
            "execution_id": self.execution_id,
        }

        print("\n" + "=" * 70)
        print(f"  反洗钱多Agent分析系统启动")
        print(f"  执行ID: {self.execution_id}")
        print(f"  分析日期: {analysis_date}")
        print(f"  交易数量: {len(transactions)}")
        print("=" * 70)

        # 执行工作流
        if debug:
            final_state = self._run_debug(init_state)
        else:
            final_state = self.graph.invoke(init_state)

        # 保存状态
        self.curr_state = final_state

        # 计算总耗时
        total_time = time.time() - start_time
        final_state["total_processing_time"] = round(total_time, 3)

        # 打印最终摘要
        self._print_summary(final_state)

        return final_state

    def _run_debug(self, init_state: AMLState) -> Dict[str, Any]:
        """调试模式: 流式输出每个节点的状态变化"""
        final_state = {}
        for chunk in self.graph.stream(init_state):
            for node_name, node_output in chunk.items():
                if isinstance(node_output, dict):
                    step = node_output.get("current_step", node_name)
                    # 只在有新步骤时打印
                    if step and step != final_state.get("current_step"):
                        final_state.update(node_output)
            final_state.update(chunk)
        return final_state

    def _print_summary(self, state: Dict[str, Any]):
        """打印分析结果摘要"""
        total_time = state.get("total_processing_time", 0)
        step_times = state.get("step_times", {})

        print("\n" + "=" * 70)
        print("  分析完成 - 结果摘要")
        print("=" * 70)

        # 预处理统计
        pre_stats = state.get("preprocessing_stats", {})
        if pre_stats:
            print(f"\n  【数据预处理】")
            print(f"    原始交易: {pre_stats.get('total', 0)} 笔")
            print(f"    清洗后: {pre_stats.get('cleaned', 0)} 笔")
            print(f"    去重数: {pre_stats.get('duplicates_removed', 0)} 笔")

        # 规则引擎统计
        rule_details = state.get("rule_details", {})
        if rule_details:
            print(f"\n  【规则引擎】")
            for rule, count in rule_details.items():
                print(f"    {rule}: {count} 笔")
            print(f"    去重后可疑: {state.get('rule_hit_count', 0)} 笔")

        # 图分析统计
        graph_data = state.get("graph_data", {})
        if graph_data and graph_data.get("graph_stats"):
            gs = graph_data["graph_stats"]
            print(f"\n  【图分析】")
            print(f"    账户节点: {gs.get('node_count', 0)} 个")
            print(f"    资金路径: {gs.get('edge_count', 0)} 条")
            print(f"    发现社区: {gs.get('community_count', 0)} 个")
            print(f"    可疑社区: {gs.get('suspicious_community_count', 0)} 个")

        # LLM深审统计
        llm_stats = state.get("llm_stats", {})
        if llm_stats and llm_stats.get("total", 0) > 0:
            print(f"\n  【LLM深审】")
            print(f"    审核总数: {llm_stats.get('total', 0)} 笔")
            print(f"    确认可疑: {llm_stats.get('confirmed', 0)} 笔")
            print(f"    误报过滤: {llm_stats.get('false_positive', 0)} 笔")
            print(f"    误报率: {llm_stats.get('false_positive_rate', 0) * 100:.1f}%")

        # 报告统计
        if state.get("report_count", 0) > 0:
            print(f"\n  【报告生成】")
            print(f"    生成报告: {state['report_count']} 份")

        # 合规审核统计
        compliance_stats = state.get("compliance_stats", {})
        if compliance_stats and compliance_stats.get("total", 0) > 0:
            print(f"\n  【合规审核】")
            print(f"    自动通过: {compliance_stats.get('passed', 0)} 份")
            print(f"    人工审核: {compliance_stats.get('human_review', 0)} 份")
            print(f"    驳回: {compliance_stats.get('rejected', 0)} 份")

        # 耗时统计
        print(f"\n  【性能统计】")
        print(f"    总耗时: {total_time:.2f} 秒")
        for step, t in step_times.items():
            print(f"    {step}: {t:.2f} 秒")

        print("\n" + "=" * 70)

    def get_graph_image(self, output_path: str = None):
        """
        获取工作流图的可视化图片(Mermaid格式)

        Args:
            output_path: 输出路径，None时返回Mermaid字符串

        Returns:
            Mermaid格式的图描述字符串，或写入文件
        """
        try:
            mermaid = self.graph.get_graph().draw_mermaid()
            if output_path:
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(mermaid)
                print(f"工作流图已保存到: {output_path}")
            return mermaid
        except Exception as e:
            print(f"生成工作流图失败: {e}")
            return None
