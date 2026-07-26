"""
Agent 4: LLM 深审 Agent

职责: 用DeepSeek LLM对规则引擎+图分析筛选出的可疑交易做深度语义分析，
      过滤误报，输出高置信度的可疑交易
模式: create_llm_reviewer_agent(llm) -> node_function

工作方式:
- 构造反洗钱专用Prompt
- 批量或单条调用LLM分析
- 输出: 是否可疑、风险等级、分析理由、置信度
- 区分: 确认可疑 / 误报
"""
import time
import os
from typing import List
from graph.state import AMLState, SuspiciousTransaction


SYSTEM_PROMPT = """你是一名资深的反洗钱(AML)分析师，有10年银行反洗钱团队工作经验。
你的任务是对初筛出来的可疑交易进行深度复核，判断是否真正可疑，还是正常交易的误报。

分析时请综合考虑以下因素：
1. 交易金额与模式是否合理
2. 交易时间是否符合正常经营规律
3. 账户之间的关系和资金流向是否正常
4. 交易备注或背景信息是否合理
5. 多种可疑模式叠加时风险加倍

输出格式(严格JSON):
{
    "is_suspicious": true/false,     // 最终判断: 是否可疑
    "risk_level": "low/medium/high/critical", // 风险等级
    "confidence": 0.0-1.0,           // 判断置信度
    "analysis": "分析理由(200字以内)",
    "false_positive_reason": ""      // 如果是误报，说明原因
}
"""


def _build_txn_context(s: SuspiciousTransaction) -> str:
    """将可疑交易上下文格式化为LLM可读文本"""
    txn = s["transaction"]
    context_lines = [
        f"交易ID: {txn.get('transaction_id', 'N/A')}",
        f"交易类型: {txn.get('transaction_type', 'N/A')}",
        f"付款账户: {txn.get('from_account', 'N/A')}",
        f"收款账户: {txn.get('to_account', 'N/A')}",
        f"交易金额: {txn.get('amount', 0):,.2f} 元",
        f"交易时间: {txn.get('timestamp', 'N/A')}",
        f"交易备注: {txn.get('remark', '无')}",
        f"是否夜间: {'是' if txn.get('is_night') else '否'}",
        f"是否周末: {'是' if txn.get('is_weekend') else '否'}",
        f"金额等级: {txn.get('amount_level', 'N/A')}",
        "",
        "命中规则:",
    ]
    for rule in s.get("rule_hits", []):
        context_lines.append(f"  - {rule}")

    context_lines.append("")
    context_lines.append("证据链:")
    for ev in s.get("evidence", []):
        context_lines.append(f"  - {ev}")

    if s.get("community_id"):
        context_lines.append("")
        context_lines.append(f"所属团伙: {s['community_id']}")

    context_lines.append("")
    context_lines.append(f"初筛风险评分: {s.get('risk_score', 0.5):.2f}")

    return "\n".join(context_lines)


def _analyze_with_llm(llm, s: SuspiciousTransaction) -> dict:
    """
    调用LLM分析单条可疑交易

    Returns:
        解析后的LLM判断结果dict
    """
    user_prompt = f"""请分析以下可疑交易，判断是真正的洗钱可疑交易还是正常业务的误报:

{_build_txn_context(s)}

请严格输出JSON格式的判断结果。"""

    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        response = llm.invoke(messages)
        content = response.content if hasattr(response, "content") else str(response)

        # 尝试解析JSON
        import json
        import re

        # 提取JSON部分(可能被```json包裹)
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            result = json.loads(json_match.group())
        else:
            result = json.loads(content.strip())

        return result
    except Exception as e:
        # 降级: 基于规则评分做保守判断
        return {
            "is_suspicious": True,
            "risk_level": "medium",
            "confidence": 0.5,
            "analysis": f"LLM分析失败，保留初筛结果。错误: {str(e)[:100]}",
            "false_positive_reason": "",
        }


def _fallback_analysis(s: SuspiciousTransaction) -> dict:
    """
    无LLM时的降级分析: 基于规则数量和评分
    - 命中规则越多 → 越可疑
    - 风险评分高 → 越可疑
    """
    rule_count = len(s.get("rule_hits", []))
    risk_score = s.get("risk_score", 0.5)

    # 基于规则数和评分的综合判断
    if rule_count >= 2 and risk_score >= 0.6:
        return {
            "is_suspicious": True,
            "risk_level": "high",
            "confidence": 0.7,
            "analysis": f"命中{rule_count}条规则，综合风险评分{risk_score:.2f}，判定为可疑。",
            "false_positive_reason": "",
        }
    elif rule_count >= 1 and risk_score >= 0.5:
        return {
            "is_suspicious": True,
            "risk_level": "medium",
            "confidence": 0.55,
            "analysis": f"命中{rule_count}条规则，风险评分{risk_score:.2f}，需关注。",
            "false_positive_reason": "",
        }
    else:
        return {
            "is_suspicious": False,
            "risk_level": "low",
            "confidence": 0.6,
            "analysis": f"仅命中{rule_count}条规则，风险评分{risk_score:.2f}，初步判断误报可能性大。",
            "false_positive_reason": "证据不足，单一规则命中且风险评分低，可能为正常交易。",
        }


def create_llm_reviewer_agent(llm=None):
    """
    创建LLM深审Agent

    Args:
        llm: LLM实例(DeepSeek等)，为None时使用规则降级分析

    Returns:
        可直接传入 StateGraph.add_node 的节点函数
    """

    def llm_reviewer_node(state: AMLState) -> dict:
        """
        LLM深审节点函数

        对规则引擎和图分析的可疑交易逐一复核，
        区分为"确认可疑"和"误报"
        """
        start_time = time.time()
        print("\n" + "=" * 60)
        print("[Agent 4] LLM 深审 Agent 启动")
        print("=" * 60)

        # 优先使用图分析增强后的结果，其次用规则引擎结果
        suspicious_list = state.get("graph_suspicious") or state.get("rule_hits", [])
        total = len(suspicious_list)

        print(f"  待审核可疑交易数: {total}")
        print(f"  LLM 可用: {'是' if llm is not None else '否(降级模式)'}")

        if total == 0:
            print("[Agent 4] 无可疑交易需审核")
            return {
                "llm_reviewed": [],
                "llm_confirmed": [],
                "false_positives": [],
                "llm_analysis_count": 0,
                "current_step": "llm_reviewer",
                "llm_stats": {"total": 0, "confirmed": 0, "false_positive": 0},
            }

        confirmed = []
        false_positives = []
        reviewed = []

        for i, s in enumerate(suspicious_list):
            idx = i + 1
            tid = s["transaction"].get("transaction_id", "N/A")
            print(f"  [{idx}/{total}] 分析交易 {tid} ...", end=" ")

            # 调用LLM或降级分析
            if llm is not None:
                result = _analyze_with_llm(llm, s)
            else:
                result = _fallback_analysis(s)

            # 复制并更新可疑交易对象
            s_copy = dict(s)
            s_copy["rule_hits"] = list(s.get("rule_hits", []))
            s_copy["evidence"] = list(s.get("evidence", []))
            s_copy["transaction"] = dict(s["transaction"])

            s_copy["llm_analysis"] = result.get("analysis", "")
            s_copy["llm_confidence"] = float(result.get("confidence", 0.5))
            s_copy["is_false_positive"] = not result.get("is_suspicious", True)

            # 更新风险等级
            risk_level = result.get("risk_level", "medium")
            risk_map = {"low": 0.3, "medium": 0.55, "high": 0.8, "critical": 0.95}
            llm_risk = risk_map.get(risk_level, 0.5)
            # 综合初筛和LLM评分
            s_copy["risk_score"] = round(
                0.4 * s.get("risk_score", 0.5) + 0.6 * llm_risk,
                3
            )
            s_copy["transaction"]["risk_score"] = s_copy["risk_score"]

            if result.get("is_suspicious"):
                confirmed.append(s_copy)
                print(f"✓ 可疑 ({risk_level}, 置信度 {s_copy['llm_confidence']:.2f})")
            else:
                false_positives.append(s_copy)
                print(f"✗ 误报 (置信度 {s_copy['llm_confidence']:.2f})")

            reviewed.append(s_copy)

        elapsed = time.time() - start_time

        # 按风险重排
        confirmed.sort(key=lambda x: x["risk_score"], reverse=True)
        reviewed.sort(key=lambda x: x["risk_score"], reverse=True)

        print(f"\n  {'─' * 50}")
        print(f"  LLM深审汇总:")
        print(f"    - 总审核数: {total}")
        print(f"    - 确认可疑: {len(confirmed)} 笔")
        print(f"    - 判定误报: {len(false_positives)} 笔")
        print(f"    - 误报过滤率: {len(false_positives) / total * 100:.1f}%" if total > 0 else "")
        print(f"  耗时: {elapsed:.2f} 秒")
        print("[Agent 4] LLM深审完成")

        return {
            "llm_reviewed": reviewed,
            "llm_confirmed": confirmed,
            "false_positives": false_positives,
            "llm_analysis_count": total,
            "current_step": "llm_reviewer",
            "step_times": {"llm_reviewer": elapsed},
            "llm_stats": {
                "total": total,
                "confirmed": len(confirmed),
                "false_positive": len(false_positives),
                "false_positive_rate": round(len(false_positives) / total, 3) if total > 0 else 0,
            },
        }

    return llm_reviewer_node
