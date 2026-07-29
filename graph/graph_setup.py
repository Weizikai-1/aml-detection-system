"""
工作流图构建 - GraphSetup

参考 TradingAgents 的 GraphSetup 类模式 + LangGraph 官方最佳实践
负责: 创建所有Agent节点 → 添加边 → 添加条件边 → 返回 workflow

核心改进:
1. 标准节点模板 - 统一的节点执行模式，包含计时、日志、错误处理
2. 工具调用集成 - 通过 ToolExecutor 支持节点调用外部工具
3. 检查点支持 - 基于文件系统的检查点，支持中断恢复
4. 状态持久化 - 节点执行后自动保存中间状态
5. 流式执行 - 支持 stream 模式实时输出
"""
import time
import traceback
import json
import os
from typing import Any, Dict, Callable, List, Optional
from langgraph.graph import END, START, StateGraph
try:
    from langgraph.checkpoint.file import FileSaver
except ImportError:
    FileSaver = None
from langgraph.checkpoint.memory import MemorySaver

from agents import (
    create_data_preprocessor_agent,
    create_rule_engine_agent,
    create_graph_analyst_agent,
    create_llm_reviewer_agent,
    create_llm_semantic_agent,
    create_report_generator_agent,
    create_compliance_auditor_agent,
)
from graph.state import AMLState
from graph.conditional_logic import ConditionalLogic
from config import DATA_DIR, LOGS_DIR


# 检查点目录
CHECKPOINT_DIR = os.path.join(DATA_DIR, "checkpoints")
os.makedirs(CHECKPOINT_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)


class StandardNodeTemplate:
    """
    标准节点模板 - 统一的节点执行模式

    每个节点执行流程:
    1. 记录开始时间和节点名称
    2. 调用实际业务逻辑
    3. 记录结束时间和耗时
    4. 记录执行状态（成功/失败）
    5. 保存中间状态到日志
    """

    def __init__(self, node_name: str, node_func: Callable, tools: List[Any] = None):
        """
        Args:
            node_name: 节点名称
            node_func: 实际业务逻辑函数
            tools: 该节点可用的工具列表（预留接口）
        """
        self.node_name = node_name
        self.node_func = node_func
        self.tools = tools or []

    def execute(self, state: AMLState) -> Dict[str, Any]:
        """
        执行节点逻辑（主入口）

        Returns:
            节点输出字典，包含执行结果和元数据
        """
        start_time = time.time()
        node_output: Dict[str, Any] = {}

        print(f"\n{'─'*60}")
        print(f"  🚀 开始执行: {self.node_name}")
        # 戒律 P4: 工具对象属性访问需防御
        if self.tools:
            tool_names = [getattr(t, "name", str(t)) for t in self.tools]
            print(f"  可用工具: {tool_names}")
        else:
            print(f"  可用工具: 无")

        try:
            # 执行业务逻辑
            result = self.node_func(state)

            # 构建输出
            node_output = self._build_success_output(result)
            print(f"  ✅ {self.node_name} 执行成功")

        except Exception as e:
            error_info = self._capture_error(e)
            node_output = self._build_error_output(error_info, state)
            print(f"  ❌ {self.node_name} 执行失败")
            print(f"     错误: {error_info['error_msg']}")

        # 记录耗时（合并而非覆盖：保留业务结果中已有的 step_times）
        elapsed = time.time() - start_time
        existing_step_times = node_output.get("step_times", {})
        if not isinstance(existing_step_times, dict):
            existing_step_times = {}
        node_output["step_times"] = {**existing_step_times, self.node_name: round(elapsed, 3)}
        node_output["current_step"] = self.node_name

        print(f"  ⏱️ {self.node_name} 耗时: {elapsed:.3f} 秒")
        print(f"{'─'*60}")

        # 保存中间状态
        self._log_intermediate_state(state, node_output)

        return node_output

    def _build_success_output(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """构建成功输出"""
        output = {
            "_node_meta": {
                "node": self.node_name,
                "status": "success",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        }

        # 合并业务结果（过滤内部元数据）
        if isinstance(result, dict):
            for key, value in result.items():
                if not key.startswith("_"):
                    output[key] = value

        return output

    def _build_error_output(self, error_info: Dict[str, Any], state: AMLState = None) -> Dict[str, Any]:
        """
        构建错误输出（降级处理，不中断流程）

        戒律 P1: 节点失败时注入降级业务数据，避免后续节点因字段缺失而静默跳过
        - 数据预处理失败：保留原始 transactions 作为 cleaned_transactions
        - 规则引擎失败：保留 rule_hits（若部分已写入），不强制清空
        - 图分析失败：返回空 graph_data
        - LLM深审失败：llm_reviewed=None 触发条件边降级
        - 报告生成失败：返回空 str_reports
        - 合规审核失败：返回空 final_reports
        """
        output = {
            "_node_meta": {
                "node": self.node_name,
                "status": "error",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            "error": error_info["error_msg"],
            "_node_error": error_info,
        }

        # 戒律 P1: 按节点名注入降级业务数据
        if state is not None:
            if self.node_name == "数据预处理":
                # 预处理失败：用原始交易继续，标记降级
                output["cleaned_transactions"] = state.get("transactions", []) or []
                output["preprocessing_stats"] = {"total": len(output["cleaned_transactions"]), "degraded": True}
                output["account_baselines"] = {}
            elif self.node_name == "规则引擎":
                # 规则引擎失败：保留可能已部分写入的 rule_hits，不强制清空
                if "rule_hits" not in output:
                    output["rule_hits"] = state.get("rule_hits", []) or []
                output["rule_hit_count"] = len(output["rule_hits"])
                output["rule_details"] = state.get("rule_details", {}) or {}
                output["rule_engine_stats"] = {"degraded": True}
            elif self.node_name == "图分析":
                # 图分析失败：返回空 graph_data，不阻断后续
                output["graph_data"] = {"nodes": [], "edges": [], "graph_stats": {}}
                output["graph_suspicious"] = []
                output["graph_hit_count"] = 0
            elif self.node_name == "LLM深审":
                # LLM 失败：llm_reviewed=None 触发条件边降级到 rule_hits
                output["llm_reviewed"] = None
                output["llm_confirmed"] = []
                output["false_positives"] = []
                output["llm_stats"] = {"degraded": True}
            elif self.node_name == "语义裁决":
                # 语义裁决失败：返回空结果，报告生成仍可基于 llm_confirmed
                output["semantic_results"] = []
                output["adjudications"] = []
                output["risk_report"] = ""
            elif self.node_name == "报告生成":
                # 报告生成失败：返回空列表
                output["str_reports"] = []
                output["report_count"] = 0
            elif self.node_name == "合规审核":
                # 合规审核失败：final_reports 为空，所有报告转人工审核
                output["final_reports"] = []
                output["rejected_reports"] = state.get("str_reports", []) or []
                output["human_review_tasks"] = [
                    {"report_id": r.get("report_id", ""), "reason": "合规审核节点失败，转人工审核"}
                    for r in (state.get("str_reports", []) or [])
                ]
                output["compliance_stats"] = {"degraded": True}

        return output

    def _capture_error(self, e: Exception) -> Dict[str, Any]:
        """捕获异常信息"""
        return {
            "node": self.node_name,
            "error_type": type(e).__name__,
            "error_msg": str(e),
            "stack_trace": traceback.format_exc()[:2000],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _log_intermediate_state(self, state: AMLState, output: Dict[str, Any]):
        """
        保存中间状态到日志文件

        戒律 M4: 证据链完整可追溯
        """
        execution_id = state.get("execution_id", "unknown")
        log_path = os.path.join(LOGS_DIR, f"{execution_id}_{self.node_name}.json")

        # 戒律 M4: 排除所有大列表字段，避免日志膨胀
        _EXCLUDE_KEYS = {
            "transactions", "cleaned_transactions", "rule_hits",
            "str_reports", "final_reports", "rejected_reports",
            "graph_data", "llm_reviewed", "llm_confirmed",
            "false_positives", "human_review_tasks",
            "semantic_results", "adjudications", "risk_report",
        }
        log_data = {
            "execution_id": execution_id,
            "node_name": self.node_name,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "input_keys": list(state.keys()),
            "output_keys": list(output.keys()),
            "output_summary": {
                k: v for k, v in output.items()
                if k not in _EXCLUDE_KEYS
            },
        }

        try:
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            # 戒律 M4: 日志写入失败需记录，可追溯
            print(f"  [日志] 写入失败 ({self.node_name}): {e}")


def create_standard_node(node_name: str, node_func: Callable, tools: List[Any] = None) -> Callable:
    """
    创建标准节点的便捷函数

    Args:
        node_name: 节点名称
        node_func: 实际业务逻辑函数
        tools: 该节点可用的工具列表

    Returns:
        可直接用于 langgraph 的节点函数
    """
    template = StandardNodeTemplate(node_name, node_func, tools)

    def node_wrapper(state: AMLState) -> Dict[str, Any]:
        return template.execute(state)

    node_wrapper.__name__ = node_name
    return node_wrapper


class GraphSetup:
    """
    构建反洗钱多Agent工作流图

    工作流:
    START → 数据预处理 → 规则引擎 → [条件1]
                                      ↓
                          有可疑 → 图分析 → LLM深审 → [条件2]
                            ↑                              ↓
                          END                       有确认 → 报告生成 → 合规审核 → END

    核心特性:
    1. 标准节点模板 - 统一的执行、计时、日志、错误处理
    2. 工具调用集成 - 支持节点调用外部工具（如数据导入、报告导出）
    3. 检查点支持 - 基于文件系统的检查点，支持中断恢复
    4. 状态持久化 - 自动保存中间状态
    """

    def __init__(
        self,
        llm: Any = None,
        conditional_logic: ConditionalLogic = None,
        use_checkpoint: bool = False,
    ):
        """
        Args:
            llm: LLM实例，传入所有需要LLM的Agent
            conditional_logic: 条件逻辑实例
            use_checkpoint: 是否启用检查点（中断恢复）
        """
        self.llm = llm
        self.conditional_logic = conditional_logic or ConditionalLogic()
        self.use_checkpoint = use_checkpoint

    def setup_graph(self) -> StateGraph:
        """
        构建完整的反洗钱工作流图

        工作流:
        START → 数据预处理 → 规则引擎 → [条件1]
                                         ↓
                            有可疑 → 图分析 → LLM深审 → [条件2]
                              ↑                              ↓
                            END                       有确认 → 语义裁决 → 报告生成 → 合规审核 → END

        Returns:
            已配置好节点和边的 StateGraph 实例(未编译)
        """
        # 创建各Agent节点（使用标准模板）
        data_preprocessor = create_data_preprocessor_agent(self.llm)
        rule_engine = create_rule_engine_agent(self.llm)
        graph_analyst = create_graph_analyst_agent(self.llm)
        llm_reviewer = create_llm_reviewer_agent(self.llm)
        semantic_agent = create_llm_semantic_agent(self.llm)
        report_generator = create_report_generator_agent(self.llm)
        compliance_auditor = create_compliance_auditor_agent(self.llm)

        # 创建工作流
        workflow = StateGraph(AMLState)

        # 添加节点（使用标准模板，实现统一的错误隔离和日志）
        workflow.add_node("数据预处理", create_standard_node("数据预处理", data_preprocessor))
        workflow.add_node("规则引擎", create_standard_node("规则引擎", rule_engine))
        workflow.add_node("图分析", create_standard_node("图分析", graph_analyst))
        workflow.add_node("LLM深审", create_standard_node("LLM深审", llm_reviewer))
        workflow.add_node("语义裁决", create_standard_node("语义裁决", semantic_agent))
        workflow.add_node("报告生成", create_standard_node("报告生成", report_generator))
        workflow.add_node("合规审核", create_standard_node("合规审核", compliance_auditor))

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

        # 条件边2: LLM深审 → 语义裁决 / END
        workflow.add_conditional_edges(
            "LLM深审",
            self.conditional_logic.should_continue_after_llm_review,
            {
                "语义裁决": "语义裁决",
                "END": END,
            },
        )

        # 固定边: 语义裁决 → 报告生成 → 合规审核 → END
        workflow.add_edge("语义裁决", "报告生成")
        workflow.add_edge("报告生成", "合规审核")
        workflow.add_edge("合规审核", END)

        return workflow

    def compile(self, checkpointer: Optional[Any] = None) -> Any:
        """
        编译工作流图，返回可执行的图对象

        Args:
            checkpointer: 自定义检查点，为 None 时使用默认内存检查点（当 FileSaver 不可用时）

        Returns:
            编译后的图对象
        """
        workflow = self.setup_graph()

        if self.use_checkpoint or checkpointer:
            cp = checkpointer
            if cp is None:
                if FileSaver is not None:
                    cp = FileSaver(CHECKPOINT_DIR)
                else:
                    cp = MemorySaver()
            return workflow.compile(checkpointer=cp)
        else:
            return workflow.compile()
