"""
图分析 Agent (GNN)
职责: 构建交易图谱，用 GAT 模型检测可疑节点

产出:
  gnn_report: {node_f1, node_precision, node_recall}
  gnn_enabled: bool
"""
import logging
from datetime import datetime
import pandas as pd
from graph.state import AMLState

log = logging.getLogger("aml.agent.graph")


def run(state: AMLState) -> dict:
    """运行 GNN 图分析"""
    updates = {
        "current_step": "图分析 (GNN)",
        "gnn_enabled": False,
        "gnn_report": {},
    }

    transactions = state.get("transactions", [])
    if not transactions:
        return updates

    try:
        from gnn_model import build_graph, train_and_eval, is_available
        if not is_available():
            log.info("GNN: PyTorch Geometric 未安装，跳过")
            updates["messages"] = [_msg("skipped", "PyTorch Geometric 未安装")]
            return updates

        df = pd.DataFrame(transactions)
        data = build_graph(df)
        result = train_and_eval(data, epochs=60)

        updates["gnn_enabled"] = True
        updates["gnn_report"] = {
            "node_f1": result["node_f1"],
            "node_precision": result["node_precision"],
            "node_recall": result["node_recall"],
        }
        updates["messages"] = [_msg(
            "ok",
            f"F1={result['node_f1']:.4f} P={result['node_precision']:.4f} R={result['node_recall']:.4f}"
        )]
        log.info(
            f"GNN 完成: F1={result['node_f1']:.4f}, "
            f"P={result['node_precision']:.4f}, R={result['node_recall']:.4f}"
        )
    except ImportError:
        log.info("GNN: 依赖未安装，跳过")
    except Exception as e:
        log.warning(f"GNN 失败: {e}")
        updates["errors"] = [f"GNN: {e}"]

    return updates


def _msg(status: str, summary: str) -> dict:
    return {
        "agent": "graph_analyst",
        "timestamp": datetime.now().isoformat(),
        "summary": summary,
        "status": status,
    }
