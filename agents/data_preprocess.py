"""
数据预处理 Agent
职责: 加载 PaySim 数据、清洗、特征提取、统计概览
支持 --demo 模式注入高风险测试样本
"""
import logging
from datetime import datetime
from graph.state import AMLState
from data_loader import load_data, stats, get_source_label

log = logging.getLogger("aml.agent.preprocess")


def run(state: AMLState) -> dict:
    """加载并预处理交易数据"""
    updates = {"current_step": "数据预处理"}

    try:
        n = state.get("n_samples", 5000)
        df = load_data(n)
        txns = df.to_dict("records")

        if state.get("demo_mode"):
            from agents.demo_injector import inject_demo_txns
            txns = inject_demo_txns(txns)
            log.info("Demo 模式: 已注入高风险样本")

        updates["transactions"] = txns
        updates["data_summary"] = stats(df)
        updates["data_source"] = get_source_label()
        updates["preprocess_ok"] = True
        updates["messages"] = [_msg("ok", f"加载 {len(txns)} 条交易")]

        s = updates["data_summary"]
        log.info(f"数据加载完成: {s['total']} 条, 欺诈 {s['fraud']} ({s['fraud_rate']}), "
                 f"来源: {s['source']}")
    except Exception as e:
        log.error(f"数据预处理失败: {e}")
        updates["preprocess_ok"] = False
        updates["errors"] = [f"数据预处理: {e}"]
        updates["messages"] = [_msg("error", str(e))]

    return updates


def _msg(status: str, summary: str) -> dict:
    return {
        "agent": "data_preprocess",
        "timestamp": datetime.now().isoformat(),
        "summary": summary,
        "status": status,
    }
