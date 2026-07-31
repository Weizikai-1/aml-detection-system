"""
AML 反洗钱多智能体检测系统 — 主入口
基于 LangGraph 的 6 Agent 并行协同工作流

用法:
    python main.py                     # 基础模式 (5000条)
    python main.py --demo              # Demo 模式 (注入高风险样本)
    python main.py --n 2000 --demo     # 指定样本数 + Demo
"""
import os
import sys
import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("aml")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(description="AML 反洗钱多智能体检测系统")
    parser.add_argument("--n", type=int, default=5000, help="数据量")
    parser.add_argument("--demo", action="store_true", help="注入高风险 Demo 样本")
    parser.add_argument("--output", type=str, default="", help="报告输出路径")
    args = parser.parse_args()

    print("=" * 60)
    print("  AML 反洗钱多智能体检测系统")
    print("  架构: LangGraph 6-Agent 并行协同工作流")
    if args.demo:
        print("  模式: [Demo] 注入高风险样本")
    print("=" * 60)

    from graph.workflow import build_workflow, run_sequential

    wf = build_workflow()
    initial_state = {
        "n_samples": args.n,
        "demo_mode": args.demo,
        "errors": [],
    }

    if wf is not None:
        mode = "并行 (LangGraph)" if wf else "串行 (回退)"
        print(f"\n[{mode}] 启动 6-Agent 工作流...\n")
        final_state = wf.invoke(initial_state)
    else:
        print("\n[串行] LangGraph 未安装，使用回退模式...\n")
        final_state = run_sequential(initial_state)

    _print_summary(final_state)

    report = final_state.get("str_report", "")
    if report:
        out_path = args.output or os.path.join(
            os.path.dirname(__file__), "reports", "aml_report.md"
        )
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n报告已保存: {out_path}")

    comp = final_state.get("compliance", {})
    print(f"\n合规审核: {comp.get('status', 'N/A')}")
    if comp.get("issues"):
        for issue in comp["issues"]:
            print(f"  - {issue}")

    errors = final_state.get("errors", [])
    if errors:
        print(f"\n⚠ 错误 ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")

    print("\n完成。")


def _print_summary(state: dict):
    ds = state.get("data_summary", {})
    rr = state.get("rule_report", {})
    rs = rr.get("summary", {})
    gn = state.get("gnn_report", {})
    llm_count = len(state.get("llm_reviews", []))

    print(f"\n{'─' * 55}")
    print("  执行摘要")
    print(f"  {'─' * 50}")
    print(f"  数据: {ds.get('total', 'N/A'):,} 条, "
          f"欺诈 {ds.get('fraud', 'N/A')} ({ds.get('fraud_rate', 'N/A')})")
    print(f"  来源: {state.get('data_source', 'N/A')}")
    print(f"  规则命中: {rs.get('total_hits', 0)} 笔 "
          f"(高:{rs.get('high_risk', 0)} 中:{rs.get('medium_risk', 0)} 低:{rs.get('low_risk', 0)})")
    if rs.get("by_rule"):
        rules_str = ", ".join(f"{k}:{v}" for k, v in rs["by_rule"].items())
        print(f"  规则分布: {rules_str}")
    if gn:
        print(f"  GNN: F1={gn.get('node_f1', 0):.4f} "
              f"P={gn.get('node_precision', 0):.4f} R={gn.get('node_recall', 0):.4f}")
    if llm_count:
        print(f"  LLM 深审: {llm_count} 笔")
    # 展示 messages 总线中的关键节点
    messages = state.get("messages", [])
    if messages:
        agents = list(set(m.get("agent", "?") for m in messages))
        print(f"  Agent 链路: {' → '.join(agents)}")
    print(f"  {'─' * 50}")


if __name__ == "__main__":
    main()
