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
from config import LLM_CONFIG, RISK_CONFIG
from utils import get_logger

logger = get_logger("llm_reviewer")


SYSTEM_PROMPT = """你是一名拥有10年银行反洗钱团队经验的资深反洗钱(AML)分析师。
你的任务是对初筛出来的可疑交易进行深度复核，判断是否真正可疑，还是正常交易的误报。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 【强制要求 · 违反即不合格】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
M1. 必须使用真实数据
    - 所有分析判断必须基于下方提供的真实交易数据
    - 禁止臆测、假设或编造不存在的交易信息、账户信息、金额等
    - 交易数据中没有的字段，不得自行补充或脑补

M2. 必须标注每个可疑交易的理由
    - 每笔判定为可疑的交易必须写明具体违规行为
    - 必须说明触发了哪条规则、为什么可疑
    - 分析理由不得泛泛而谈，必须指向具体数据

M3. 风险评分范围：0-100 分
    - 风险评分必须在 0-100 分范围内
    - 分值越高表示可疑程度越高
    - 0分=完全正常，100分=确定可疑
    - 输出必须附带具体分数，不得只给等级

M4. 证据链完整可追溯
    - 所有可疑判定必须有完整证据链
    - 从交易数据 → 规则命中 → 分析结论，每一步都可验证
    - 引用的证据必须能在交易数据中找到对应

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚫 【严格禁止 · 触碰即失败】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
P1. 禁止遗漏高风险交易
    - 规则引擎已命中的高风险交易（初筛评分≥70分）禁止无理由降级或排除
    - 如判定为误报，必须给出明确、充分的排除依据
    - 宁可多审不可漏过

P2. 禁止误报正常交易
    - 缺乏充分证据时不得随意将正常交易标记为可疑
    - 严禁为了提高召回率而牺牲准确率
    - 存疑时结合交易背景综合判断

P3. 禁止没有证据就判定可疑
    - 没有真实交易数据支撑，不得判定为可疑
    - 没有规则命中证据，不得判定为可疑
    - 没有合理业务解释，不得判定为可疑
    - 三者缺一不可

P4. 禁止主观臆断
    - 不得基于账户名称、交易对手等表面信息做主观推断
    - 不得用"感觉""可能""也许"等模糊表述做结论
    - 所有结论必须有数据和规则支撑

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

分析时请综合考虑以下因素：
1. 交易金额与模式是否符合账户的正常经营/使用规律
2. 交易时间是否符合正常作息或经营规律
3. 账户之间的关系和资金流向是否合理
4. 交易备注或背景信息是否与交易性质匹配
5. 多种可疑模式叠加时风险相应提高
6. 初筛规则的命中理由是否充分、是否存在合理的正常解释

输出格式(严格JSON，不要任何额外文字):
{
    "is_suspicious": true/false,     // 最终判断: 是否可疑
    "risk_level": "low/medium/high/critical", // 风险等级
    "confidence": 0.0-1.0,           // 判断置信度
    "risk_score": 0-100,             // 风险评分(百分制，0-100)
    "analysis": "分析理由(200字以内，必须具体说明可疑/不可疑的依据)",
    "false_positive_reason": ""      // 如果判定为误报，说明具体原因和证据
}
"""


def _compute_virtual_llm_score(s: SuspiciousTransaction) -> int:
    """
    降级路径虚拟LLM评分

    当LLM不可用或调用失败时，根据规则命中数和证据强度推算一个"虚拟LLM评分"，
    使其参与权重合并，保证降级和非降级交易的评分口径一致。

    策略:
    - 多规则命中且高分: virtual = min(base + 10, 100)  — 倾向确认
    - 单规则命中且中分: virtual = base                  — 保持中性
    - 低风险:            virtual = max(base - 10, 0)    — 倾向排除
    """
    rule_count = len(s.get("rule_hits", []))
    base_score = s.get("risk_score", 50)
    if not isinstance(base_score, (int, float)):
        base_score = 50

    if rule_count >= 2 and base_score >= 60:
        return min(int(base_score) + 10, 100)
    elif rule_count >= 1 and base_score >= 50:
        return int(base_score)
    else:
        return max(int(base_score) - 10, 0)


def _build_txn_context(s: SuspiciousTransaction) -> str:
    """将可疑交易上下文格式化为LLM可读文本"""
    txn = s.get("transaction", {})
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
    # 戒律 M3: 风险评分量纲为 0-100，默认值改为 50（不再是 0.5）
    risk_score = s.get("risk_score", 50)
    if not isinstance(risk_score, (int, float)):
        risk_score = 50
    context_lines.append(f"初筛风险评分: {risk_score:.2f}")

    return "\n".join(context_lines)


def _parse_llm_response(content: str, s: SuspiciousTransaction) -> dict:
    """
    解析 LLM 响应 JSON

    Args:
        content: LLM 响应文本
        s: 原始可疑交易对象（用于降级时的评分参考）

    Returns:
        解析后的判断结果dict
    """
    import json
    import re

    try:
        # 提取JSON部分(可能被```json包裹)
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            result = json.loads(json_match.group())
        else:
            result = json.loads(content.strip())
        return result
    except Exception as e:
        # 降级: 基于规则评分做保守判断（不默认判可疑）
        logger.warning(f"LLM评分降级: {e}")
        orig = s.get("risk_score", 50)
        if orig >= 70:
            degraded_level = "high"
        elif orig >= 50:
            degraded_level = "medium"
        else:
            degraded_level = "low"
        return {
            "is_suspicious": orig >= 60,
            "risk_level": degraded_level,
            "confidence": 0.3,
            "analysis": f"LLM响应解析失败，基于规则评分降级判断。错误: {str(e)[:80]}",
            "false_positive_reason": "" if orig >= 60 else "LLM不可用，规则评分不足60分，保守判定为误报",
            "_degraded": True,
            "_virtual_llm_score": _compute_virtual_llm_score(s),
        }


def _analyze_with_llm(llm, s: SuspiciousTransaction) -> dict:
    """
    调用LLM分析单条可疑交易（同步）

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
        return _parse_llm_response(content, s)
    except Exception as e:
        # 降级: 基于规则评分做保守判断（不默认判可疑）
        logger.error(f"LLM深审调用失败: {e}", exc_info=True)
        orig = s.get("risk_score", 50)
        if orig >= 70:
            degraded_level = "high"
        elif orig >= 50:
            degraded_level = "medium"
        else:
            degraded_level = "low"
        return {
            "is_suspicious": orig >= 60,
            "risk_level": degraded_level,
            "confidence": 0.3,
            "analysis": f"LLM调用失败，基于规则评分降级判断。错误: {str(e)[:80]}",
            "false_positive_reason": "" if orig >= 60 else "LLM不可用，规则评分不足60分，保守判定为误报",
            "_degraded": True,
            "_virtual_llm_score": _compute_virtual_llm_score(s),
        }


def _batch_analyze_with_llm(llm, suspicious_list: list) -> list[dict]:
    """
    批量并发调用 LLM 分析多条可疑交易

    严格遵守戒律 M1: 使用真实数据，每条交易独立分析
    严格遵守戒律 P1: 高风险交易不遗漏 — 失败时降级为规则评分判断

    Returns:
        解析后的LLM判断结果dict列表（与输入顺序一致）
    """
    # 检查 LLM 是否支持异步调用（mock 对象通常用串行调用）
    from unittest.mock import MagicMock
    is_mock = isinstance(llm, MagicMock)
    has_async = (not is_mock) and hasattr(llm, "ainvoke") and callable(getattr(llm, "ainvoke", None))

    if not has_async or not LLM_CONFIG.get("concurrency_enabled", False):
        # 不支持异步或并发禁用时，回退到串行
        return [_analyze_with_llm(llm, s) for s in suspicious_list]

    import asyncio

    from tools.llm_client import _run_async

    user_prompts = []
    for s in suspicious_list:
        user_prompt = f"""请分析以下可疑交易，判断是真正的洗钱可疑交易还是正常业务的误报:

{_build_txn_context(s)}

请严格输出JSON格式的判断结果。"""
        user_prompts.append(user_prompt)

    max_conc = LLM_CONFIG.get("max_concurrency", 5)

    async def _call_one(prompt: str, sem: asyncio.Semaphore) -> str:
        async with sem:
            try:
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]
                response = await llm.ainvoke(messages)
                return response.content if hasattr(response, "content") else str(response)
            except Exception as e:
                logger.error(f"LLM深审调用失败: {e}", exc_info=True)
                print(f"  LLM 异步调用失败: {e}")
                return ""

    async def _batch():
        sem = asyncio.Semaphore(max_conc)
        tasks = [_call_one(p, sem) for p in user_prompts]
        return await asyncio.gather(*tasks, return_exceptions=True)

    try:
        responses = _run_async(_batch())
    except Exception as e:
        logger.warning(f"LLM评分降级: {e}")
        print(f"  批量LLM调用异常，回退到串行模式: {e}")
        return [_analyze_with_llm(llm, s) for s in suspicious_list]

    results = []
    for content, s in zip(responses, suspicious_list):
        if isinstance(content, Exception) or not content:
            # 空响应按降级处理（戒律 P1：不遗漏高风险交易）
            orig = s.get("risk_score", 50)
            if orig >= 70:
                degraded_level = "high"
            elif orig >= 50:
                degraded_level = "medium"
            else:
                degraded_level = "low"
            results.append({
                "is_suspicious": orig >= 60,
                "risk_level": degraded_level,
                "confidence": 0.3,
                "analysis": "LLM调用失败，基于规则评分降级判断。",
                "false_positive_reason": "" if orig >= 60 else "LLM不可用，规则评分不足60分，保守判定为误报",
                "_degraded": True,
                "_virtual_llm_score": _compute_virtual_llm_score(s),
            })
        else:
            results.append(_parse_llm_response(content, s))

    return results


def _fallback_analysis(s: SuspiciousTransaction) -> dict:
    """
    无LLM时的降级分析: 基于规则数量和评分
    - 命中规则越多 → 越可疑
    - 风险评分高 → 越可疑
    （百分制 0-100）
    """
    rule_count = len(s.get("rule_hits", []))
    risk_score = s.get("risk_score", 50)

    # 基于规则数和评分的综合判断
    if rule_count >= 2 and risk_score >= 60:
        return {
            "is_suspicious": True,
            "risk_level": "high",
            "confidence": 0.7,
            "analysis": f"命中{rule_count}条规则，综合风险评分{risk_score}分，判定为可疑。",
            "false_positive_reason": "",
        }
    elif rule_count >= 1 and risk_score >= 50:
        return {
            "is_suspicious": True,
            "risk_level": "medium",
            "confidence": 0.55,
            "analysis": f"命中{rule_count}条规则，风险评分{risk_score}分，需关注。",
            "false_positive_reason": "",
        }
    else:
        return {
            "is_suspicious": False,
            "risk_level": "low",
            "confidence": 0.6,
            "analysis": f"仅命中{rule_count}条规则，风险评分{risk_score}分，初步判断误报可能性大。",
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

        # 调用LLM或降级分析
        if llm is not None:
            # 批量并发调用 LLM（默认并发数 5，戒律 P2：不误报 — 单条失败不影响其他）
            print(f"  LLM 并发模式: 启用 (最大并发 {LLM_CONFIG.get('max_concurrency', 5)})")
            llm_results = _batch_analyze_with_llm(llm, suspicious_list)
        else:
            # 降级模式：规则评分判断
            llm_results = [_fallback_analysis(s) for s in suspicious_list]

        # 处理结果
        for i, (s, result) in enumerate(zip(suspicious_list, llm_results)):
            idx = i + 1
            tid = s.get("transaction", {}).get("transaction_id", "N/A")
            risk_level = result.get("risk_level", "medium")
            # 戒律 M4/P2: is_suspicious 缺失时默认 False（保守不误报），并记录日志
            is_susp = result.get("is_suspicious")
            if is_susp is None:
                is_susp = False
                print(f"\n  ⚠️ LLM响应缺失is_suspicious字段，交易{tid}降级为非可疑")
            # 戒律 M1: confidence 可能为非法值，try/except 包裹
            try:
                conf = float(result.get("confidence", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            degraded = result.get("_degraded", False)

            print(f"  [{idx}/{total}] 分析交易 {tid} ...", end=" ")
            status = "✓ 可疑" if is_susp else "✗ 误报"
            deg_tag = " (降级)" if degraded else ""
            print(f"{status} ({risk_level}, 置信度 {conf:.2f}){deg_tag}")

            # 复制并更新可疑交易对象
            s_copy = dict(s)
            s_copy["rule_hits"] = list(s.get("rule_hits", []))
            s_copy["evidence"] = list(s.get("evidence", []))
            s_copy["transaction"] = dict(s.get("transaction", {}))

            s_copy["llm_analysis"] = result.get("analysis", "")
            s_copy["llm_confidence"] = conf
            s_copy["is_false_positive"] = not is_susp

            # 戒律 M3: 风险等级映射（代表分值，非最低分）
            # 注: RISK_CONFIG["levels"] 存储的是各等级"最低分"，此处需要"代表分值"用于加权
            # 因此保留显式映射，但 LLM 权重从 RISK_CONFIG 读取
            risk_map = {"low": 30, "medium": 55, "high": 80, "critical": 95}
            llm_risk = risk_map.get(risk_level, 50)
            # 戒律 M3: LLM 权重从 config.py RISK_CONFIG 读取（不硬编码）
            llm_weight = RISK_CONFIG.get("llm_weight", 0.4)
            rule_weight = 1.0 - llm_weight
            # 降级路径使用虚拟LLM评分参与权重合并，保证评分口径一致
            if degraded:
                virtual_llm = result.get("_virtual_llm_score", 50)
                try:
                    base_score = float(s.get("risk_score") or 50)
                except (TypeError, ValueError):
                    base_score = 50.0
                combined_score = round(rule_weight * base_score + llm_weight * virtual_llm)
                s_copy["risk_score"] = max(min(combined_score, 100), 0)
            else:
                # 综合初筛和LLM评分——百分制
                # 戒律 M3: 限制在 0-100 范围
                try:
                    base_score = float(s.get("risk_score") or 50)
                except (TypeError, ValueError):
                    base_score = 50.0
                combined_score = round(rule_weight * base_score + llm_weight * llm_risk)
                s_copy["risk_score"] = max(min(combined_score, 100), 0)
            s_copy["transaction"]["risk_score"] = s_copy["risk_score"]

            if is_susp:
                confirmed.append(s_copy)
            else:
                false_positives.append(s_copy)

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
