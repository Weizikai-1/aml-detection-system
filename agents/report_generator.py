"""
报告生成 Agent
职责: 汇总所有 Agent 产出，生成 STR (可疑交易报告)

读取:
  rule_report, gnn_report, llm_reviews — 上游 Agent 产出
产出:
  str_report — 最终可疑交易报告文本
"""
import logging
from datetime import datetime
from graph.state import AMLState
from llm.deepseek_client import DeepSeekClient
from memory.chroma_store import memory

log = logging.getLogger("aml.agent.report")

llm = DeepSeekClient()

_STR_TEMPLATE = """# 可疑交易报告 (STR)

**报告时间**: {timestamp}
**数据来源**: {data_source}

---

## 1. 数据概览

{data_summary}

## 2. 规则引擎检测

- 命中总数: {total_hits}
- 高风险: {high_risk} / 中风险: {medium_risk} / 低风险: {low_risk}
- 规则分布: {by_rule}

## 3. 图分析 (GNN)

{gnn_section}

## 4. LLM 深度审核

{llm_section}

## 5. 高风险交易详情

{risk_details}

## 6. 建议措施

{suggestions}

---

*报告由 AML 反洗钱多智能体系统自动生成*
"""


def run(state: AMLState) -> dict:
    """生成 STR 报告"""
    updates = {"current_step": "报告生成"}

    try:
        rr = state.get("rule_report", {})
        summary = rr.get("summary", {})
        data_s = state.get("data_summary", {})

        # 数据概览
        data_text = (
            f"- 总交易: {data_s.get('total', 'N/A'):,}\n"
            f"- 欺诈交易: {data_s.get('fraud', 'N/A')}\n"
            f"- 欺诈率: {data_s.get('fraud_rate', 'N/A')}\n"
            f"- 交易类型: {data_s.get('types', {})}"
        )

        # GNN
        gnn = state.get("gnn_report", {})
        if state.get("gnn_enabled") and gnn:
            gnn_text = (
                f"- 节点级 F1: {gnn.get('node_f1', 0):.4f}\n"
                f"- 节点级 Precision: {gnn.get('node_precision', 0):.4f}\n"
                f"- 节点级 Recall: {gnn.get('node_recall', 0):.4f}"
            )
        else:
            gnn_text = "GNN 不可用（未安装 PyTorch Geometric）"

        # LLM 深审
        reviews = state.get("llm_reviews", [])
        llm_enabled = state.get("llm_enabled", False)
        if reviews:
            lines = []
            for i, r in enumerate(reviews, 1):
                a = r.get("llm_analysis", {})
                lines.append(
                    f"{i}. **规则**: {r['rule']} | **风险分**: {r['risk_score']}\n"
                    f"   - 嫌疑等级: {a.get('suspicion_level', 'N/A')}\n"
                    f"   - 分析: {a.get('reasoning', 'N/A')[:150]}\n"
                    f"   - 洗钱类型: {a.get('typology', 'N/A')}"
                )
            llm_text = "\n\n".join(lines)
        elif llm_enabled:
            llm_text = "LLM 审核已执行，未发现额外风险。"
        else:
            llm_text = (
                "LLM 不可用 (未设置 DEEPSEEK_API_KEY)。\n"
                "> 设置环境变量后，系统将自动对高风险交易进行语义分析。"
            )

        # 高风险交易详情
        high_risk = rr.get("high_risk", [])
        if high_risk:
            tx_details = []
            for i, h in enumerate(high_risk[:10], 1):
                txn = h.get("transaction", {})
                rules = h.get("rules", [h.get("rule", "?")])
                evidence = h.get("evidence", [])
                tx_details.append(
                    f"{i}. 风险分 **{h.get('risk_score', '?')}** | "
                    f"规则: {', '.join(rules)}\n"
                    f"   - 付款方: {txn.get('nameOrig', '?')[:30]}\n"
                    f"   - 收款方: {txn.get('nameDest', '?')[:30]}\n"
                    f"   - 金额: {txn.get('amount', 0):,.0f}\n"
                    f"   - 证据: {'; '.join(str(e)[:80] for e in evidence[:2])}"
                )
            risk_text = "\n\n".join(tx_details)
        else:
            risk_text = "无高风险交易。"

        # 建议措施
        suggestions = _generate_suggestions(state, summary)

        report = _STR_TEMPLATE.format(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            data_source=state.get("data_source", "未知"),
            data_summary=data_text,
            total_hits=summary.get("total_hits", 0),
            high_risk=summary.get("high_risk", 0),
            medium_risk=summary.get("medium_risk", 0),
            low_risk=summary.get("low_risk", 0),
            by_rule=summary.get("by_rule", {}),
            gnn_section=gnn_text,
            llm_section=llm_text,
            risk_details=risk_text,
            suggestions=suggestions,
        )

        updates["str_report"] = report
        updates["messages"] = [_msg("ok", f"报告生成 {len(report)} 字符")]
        log.info(f"STR 报告生成完成 ({len(report)} 字符)")

        memory.save_case({
            "case_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "total_hits": summary.get("total_hits", 0),
            "high_risk": summary.get("high_risk", 0),
            "rules": list(summary.get("by_rule", {}).keys()),
            "report_length": len(report),
        })

    except Exception as e:
        log.error(f"报告生成失败: {e}")
        updates["errors"] = [f"报告生成: {e}"]

    return updates


def _generate_suggestions(state: AMLState, summary: dict) -> str:
    """生成建议措施"""
    high_risk = summary.get("high_risk", 0)

    if high_risk >= 5:
        base = "⚠ 高风险警报：多条规则命中，建议立即上报监管部门并冻结相关账户。"
    elif high_risk >= 1:
        base = "⚡ 中危预警：存在高风险交易，建议人工复审后上报。"
    else:
        base = "📊 低风险：当前样本未发现高危模式，建议持续监控。"

    if llm.is_available():
        try:
            enhanced = llm.chat(
                "你是反洗钱合规顾问，根据检测结果给出具体的建议措施（2-3句话）。",
                f"高风险命中数: {high_risk}, 总命中: {summary.get('total_hits', 0)}, "
                f"规则分布: {summary.get('by_rule', {})}",
                temperature=0.3,
                max_tokens=500,
                timeout=20,          # 建议生成可容忍稍长延迟
            )
            return f"{base}\n\n> LLM 建议: {enhanced}"
        except Exception:
            pass
    return base


def _msg(status: str, summary: str) -> dict:
    return {
        "agent": "report_generator",
        "timestamp": datetime.now().isoformat(),
        "summary": summary,
        "status": status,
    }
