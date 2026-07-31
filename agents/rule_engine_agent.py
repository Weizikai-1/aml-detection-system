"""
规则引擎 Agent
职责: 运行 10 条反洗钱规则，筛选高风险交易

产出:
  rule_report: {
    "hits": [...],           # 所有规则命中
    "summary": {             # 汇总统计
      "total_hits", "by_rule", "high_risk", "medium_risk", "low_risk"
    },
    "high_risk": [...]       # 高风险交易 (≥70分)，供后续 LLM 深审 + 路由
  }
"""
import logging
from datetime import datetime
from graph.state import AMLState
from rules import ALL_RULES, CORE_RULES
from rule_engine import run_engine, summary as rule_summary

log = logging.getLogger("aml.agent.rules")


def run(state: AMLState) -> dict:
    """运行规则检测，输出 rule_report 容器"""
    updates = {"current_step": "规则引擎检测"}

    txns = state.get("transactions", [])
    if not txns:
        updates["rule_report"] = _empty_report()
        return updates

    try:
        hits = run_engine(txns)
        info = rule_summary(hits)
        high_risk = [h for h in hits if h["risk_score"] >= 70]

        updates["rule_report"] = {
            "hits": hits,
            "summary": info,
            "high_risk": high_risk,
        }
        updates["messages"] = [_msg(
            "ok",
            f"命中 {info['total_hits']} 笔 "
            f"(高:{info['high_risk']} 中:{info['medium_risk']} 低:{info['low_risk']})"
        )]
        log.info(
            f"规则引擎: {info['total_hits']} 笔命中, "
            f"高风险 {info['high_risk']}, 中风险 {info['medium_risk']}, "
            f"规则分布: {info['by_rule']}"
        )
    except Exception as e:
        log.error(f"规则引擎失败: {e}")
        updates["errors"] = [f"规则引擎: {e}"]
        updates["rule_report"] = _empty_report()

    return updates


def _empty_report() -> dict:
    return {
        "hits": [],
        "summary": {"total_hits": 0, "by_rule": {}, "high_risk": 0,
                     "medium_risk": 0, "low_risk": 0},
        "high_risk": [],
    }


def _msg(status: str, summary: str) -> dict:
    return {
        "agent": "rule_engine",
        "timestamp": datetime.now().isoformat(),
        "summary": summary,
        "status": status,
    }
