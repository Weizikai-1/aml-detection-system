"""
反洗钱多Agent系统 - 主工作流类

参考 TradingAgents 的 TradingAgentsGraph 主类模式 + LangGraph 最佳实践
- 持有 LLM、graph、状态
- 提供 run() 方法执行完整流程
- 管理状态持久化
- 支持检查点（中断恢复）
- 支持流式输出
"""
import time
import os
import json
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
from langgraph.checkpoint.memory import MemorySaver
try:
    from langgraph.checkpoint.file import FileSaver
except ImportError:
    FileSaver = None

from graph.state import AMLState, Transaction
from graph.graph_setup import GraphSetup
from graph.conditional_logic import ConditionalLogic
from config import AML_CONFIG, DATA_DIR, LOGS_DIR


# 检查点目录
CHECKPOINT_DIR = os.path.join(DATA_DIR, "checkpoints")
os.makedirs(CHECKPOINT_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)


class AMLAgentsGraph:
    """
    反洗钱多Agent系统主类

    负责:
    - 初始化LLM和各Agent
    - 构建和编译LangGraph工作流
    - 执行分析流程（支持检查点/中断恢复）
    - 管理状态和结果
    - 提供价值证明指标
    """

    def __init__(
        self,
        llm: Any = None,
        config: Dict[str, Any] = None,
        use_checkpoint: bool = False,
    ):
        """
        初始化反洗钱多Agent系统

        Args:
            llm: LLM实例(DeepSeek等)，为None时使用降级模式
            config: 配置字典，为None时使用默认配置
            use_checkpoint: 是否启用检查点（支持中断恢复）
        """
        self.config = config or AML_CONFIG
        self.llm = llm
        self.use_checkpoint = use_checkpoint

        # 初始化组件
        self.conditional_logic = ConditionalLogic(
            min_rule_hits=self.config.get("workflow", {}).get("min_rule_hits_for_graph", 1)
        )

        self.graph_setup = GraphSetup(
            llm=self.llm,
            conditional_logic=self.conditional_logic,
            use_checkpoint=use_checkpoint,
        )

        # 构建工作流（使用新的 compile 方法）
        if use_checkpoint:
            if FileSaver is not None:
                self.checkpointer = FileSaver(CHECKPOINT_DIR)
            else:
                self.checkpointer = MemorySaver()
            self.graph = self.graph_setup.compile(checkpointer=self.checkpointer)
        else:
            self.checkpointer = None
            self.graph = self.graph_setup.compile()

        # 状态追踪
        self.curr_state: Optional[Dict] = None
        self.execution_id: Optional[str] = None

    def run(
        self,
        transactions: List[Transaction],
        analysis_date: str = None,
        analysis_params: Dict[str, Any] = None,
        debug: bool = False,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        执行完整的反洗钱分析流程

        Args:
            transactions: 待分析的交易列表
            analysis_date: 分析日期，默认今天
            analysis_params: 自定义分析参数
            debug: 是否开启调试模式(流式输出)
            thread_id: 线程ID，用于检查点恢复（为None时自动生成）

        Returns:
            最终状态字典，包含所有Agent的输出和价值证明指标
        """
        start_time = time.time()
        self.execution_id = thread_id or str(uuid.uuid4())[:8]

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
        print(f"  检查点: {'启用' if self.use_checkpoint else '禁用'}")
        print("=" * 70)

        # 执行工作流
        try:
            if debug:
                final_state = self._run_debug(init_state)
            else:
                final_state = self._run_with_progress(init_state)
        except KeyboardInterrupt:
            print("\n\n  ⚠️ 用户中断分析流程")
            print(f"  已执行到: {self.curr_state.get('current_step', '未知') if self.curr_state else '未知'}")
            final_state = self.curr_state or init_state
            final_state["interrupted"] = True

        # 保存状态
        self.curr_state = final_state

        # 计算价值证明指标
        total_time = time.time() - start_time
        final_state["total_processing_time"] = round(total_time, 3)
        final_state["value_metrics"] = self._calculate_value_metrics(final_state)

        # 打印最终摘要
        self._print_summary(final_state)

        return final_state

    def _calculate_value_metrics(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        计算价值证明指标

        Returns:
            价值指标字典，包含业务价值、效率提升、合规保障等维度

        说明:
        - false_positive_rate = FP / (TP + FP) = llm_false_positives / rule_hits
          （规则命中中误报占比，越低越好）
        - human_workload_reduction 与 false_positive_rate 数学等价，
          但语义不同：表示 LLM 替人工过滤掉的工作量比例
        """
        total_txns = len(state.get("transactions", []) or [])
        rule_hits = len(state.get("rule_hits", []) or [])
        str_reports = len(state.get("str_reports", []) or [])
        llm_confirmed = len(state.get("llm_confirmed", []) or [])
        false_positives_count = len(state.get("false_positives", []) or [])
        compliance_score = state.get("compliance_score", 0) or 0
        total_time = state.get("total_processing_time", 0) or 0

        # 戒律 M3: STRReport 无 risk_score 字段，从 suspicious_transactions 提取最大风险分(0-100)
        def _max_risk_score(rpt):
            txns = rpt.get("suspicious_transactions", []) or []
            if not txns:
                return 0
            scores = []
            for t in txns:
                s = t.get("risk_score", 0)
                if isinstance(s, (int, float)) and s >= 0:
                    scores.append(s)
            return max(scores) if scores else 0

        # 业务价值指标
        detection_rate = rule_hits / max(total_txns, 1)
        false_positive_rate = false_positives_count / max(rule_hits, 1)
        # 语义同 false_positive_rate，保留独立命名便于业务报表区分
        human_workload_reduction = false_positives_count / max(rule_hits, 1)

        # 效率指标
        if total_time > 0:
            throughput = total_txns / total_time
            avg_time_per_txn = total_time / max(total_txns, 1)
        else:
            throughput = 0
            avg_time_per_txn = 0

        return {
            # 业务价值
            "detection_rate": round(detection_rate, 4),
            "false_positive_rate": round(false_positive_rate, 4),
            "human_workload_reduction": round(human_workload_reduction, 4),
            "suspicious_transactions_found": rule_hits,
            "str_reports_generated": str_reports,
            "llm_confirmed_cases": llm_confirmed,
            "llm_filtered_false_positives": false_positives_count,

            # 效率指标
            "throughput_txns_per_sec": round(throughput, 2),
            "avg_time_per_txn_ms": round(avg_time_per_txn * 1000, 2),
            "total_processing_time_sec": round(total_time, 3),

            # 合规保障
            "compliance_score": round(compliance_score, 1),
            "analysis_coverage": 1.0 if total_txns > 0 else 0.0,

            # 戒律 M3: 风险分级按 0-100 标尺
            "high_risk_cases": sum(
                1 for rpt in (state.get("str_reports", []) or [])
                if _max_risk_score(rpt) >= 70
            ),
            "medium_risk_cases": sum(
                1 for rpt in (state.get("str_reports", []) or [])
                if 50 <= _max_risk_score(rpt) < 70
            ),
            "low_risk_cases": sum(
                1 for rpt in (state.get("str_reports", []) or [])
                if _max_risk_score(rpt) < 50
            ),

            # 数据质量
            "data_quality_score": (state.get("preprocessing_stats", {}) or {}).get("quality_score", 0),
        }

    def _run_debug(self, init_state: AMLState) -> Dict[str, Any]:
        """调试模式: 流式输出每个节点的状态变化"""
        # 戒律 M4: 初始化为 init_state 副本，保留 transactions 等基础字段
        final_state = dict(init_state)
        # 戒律 P1: 检查点 thread_id 必须传递，否则中断恢复失效
        config = {"configurable": {"thread_id": self.execution_id}}
        for chunk in self.graph.stream(init_state, config=config):
            for node_name, node_output in chunk.items():
                if isinstance(node_output, dict):
                    step = node_output.get("current_step", node_name)
                    print(f"  [stream] 节点={node_name} 步骤={step}")
                    # 戒律 M4: 合并 step_times
                    new_step_times = node_output.get("step_times", {})
                    existing = final_state.get("step_times", {})
                    if isinstance(new_step_times, dict) and isinstance(existing, dict):
                        node_output["step_times"] = {**existing, **new_step_times}
                    final_state.update(node_output)
        return final_state

    def _run_with_progress(self, init_state: AMLState) -> Dict[str, Any]:
        """
        带进度显示的工作流执行

        用 stream 模式执行，每个节点完成后打印进度
        支持 Ctrl+C 中断
        """
        # 6个Agent节点
        total_steps = 6
        step_names = {
            "数据预处理": 1,
            "规则引擎": 2,
            "图分析": 3,
            "LLM深审": 4,
            "报告生成": 5,
            "合规审核": 6,
        }

        final_state = dict(init_state)
        completed = 0

        print(f"\n  分析进度:")
        print(f"  {'─' * 50}")

        # 戒律 P1: 检查点 thread_id 必须传递，否则中断恢复失效
        config = {"configurable": {"thread_id": self.execution_id}}
        for chunk in self.graph.stream(init_state, config=config):
            for node_name, node_output in chunk.items():
                if isinstance(node_output, dict):
                    # 戒律 M4: 合并 step_times（防止后续节点覆盖前序节点耗时）
                    new_step_times = node_output.get("step_times", {})
                    existing = final_state.get("step_times", {})
                    if isinstance(new_step_times, dict) and isinstance(existing, dict):
                        node_output["step_times"] = {**existing, **new_step_times}
                    final_state.update(node_output)
                    self.curr_state = dict(final_state)
                    completed = step_names.get(node_name, completed)
                    # 进度条
                    bar_len = 30
                    filled = int(bar_len * completed / total_steps)
                    bar = "█" * filled + "░" * (bar_len - filled)
                    pct = completed * 100 // total_steps
                    print(f"  [{bar}] {pct}% ({completed}/{total_steps}) {node_name} 完成")

        print(f"  {'─' * 50}")
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


