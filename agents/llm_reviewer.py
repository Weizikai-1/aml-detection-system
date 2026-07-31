"""
LLM 深审 Agent
职责: 对高风险交易做语义分析，判断洗钱嫌疑
类比 TradingAgents 的 Researcher Team 辩论机制

读取: rule_report["high_risk"] — 由 rule_engine 产出的高风险交易列表
产出: llm_reviews — LLM 分析结果列表
"""
import json
import logging
from datetime import datetime
from graph.state import AMLState
from llm.deepseek_client import DeepSeekClient
from memory.chroma_store import memory

log = logging.getLogger("aml.agent.llm")

_SYSTEM_PROMPT = """你是反洗钱(AML)高级分析师。根据交易数据判断洗钱嫌疑，输出 JSON。

返回格式: {"suspicion_level": "high|medium|low", "reasoning": "分析理由",
"typology": "洗钱类型", "recommendation": "建议措施"}"""

llm = DeepSeekClient()


def run(state: AMLState) -> dict:
    """LLM 深度审核高风险交易"""
    updates = {
        "current_step": "LLM 深审",
        "llm_enabled": llm.is_available(),
        "llm_reviews": [],
    }

    rr = state.get("rule_report", {})
    high_risk = rr.get("high_risk", [])

    if not high_risk:
        log.info("LLM 深审: 无高风险交易，跳过")
        updates["messages"] = [_msg("skipped", "无高风险交易")]
        return updates

    if not llm.is_available():
        log.info("LLM 深审: DeepSeek API Key 未设置，跳过")
        updates["messages"] = [_msg("skipped", "API Key 未设置")]
        return updates

    results = []
    for h in high_risk[:10]:
        try:
            txn = h.get("transaction", {})
            evidence = h.get("evidence", [])
            rule = h.get("rule", "unknown")
            risk = h.get("risk_score", 0)

            similar_cases = memory.find_similar(rule, risk, n=2)
            history_context = ""
            if similar_cases:
                history_context = (
                    f"\n历史相似案例: {json.dumps(similar_cases, ensure_ascii=False)}"
                )

            user_msg = (
                f"交易数据: {json.dumps(txn, ensure_ascii=False)}\n"
                f"触发规则: {rule}\n"
                f"风险评分: {risk}\n"
                f"证据: {evidence}"
                f"{history_context}"
            )
            review = llm.chat(
                _SYSTEM_PROMPT, user_msg,
                temperature=0.3,
                max_tokens=2000,
                timeout=30,          # 单次调用超时
            )
            parsed = _parse_json(review)

            results.append({
                "transaction": txn,
                "rule": rule,
                "risk_score": risk,
                "llm_analysis": parsed,
            })
            log.debug(
                f"LLM 审核完成: {rule}, 嫌疑等级={parsed.get('suspicion_level')}"
            )
        except Exception as e:
            log.warning(f"LLM 审核失败: {e}")

    updates["llm_reviews"] = results
    updates["messages"] = [_msg("ok", f"完成 {len(results)} 笔深审")]
    log.info(f"LLM 深审: {len(results)} 笔完成")
    return updates


def _parse_json(text: str) -> dict:
    """从 LLM 回复中提取 JSON"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return {
        "suspicion_level": "unknown",
        "reasoning": text[:200],
        "error": "JSON 解析失败",
    }


def _msg(status: str, summary: str) -> dict:
    return {
        "agent": "llm_reviewer",
        "timestamp": datetime.now().isoformat(),
        "summary": summary,
        "status": status,
    }
