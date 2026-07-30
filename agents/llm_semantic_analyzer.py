"""
LLM 语义分析 Agent — 深度交易语义理解 & 混合裁决

核心能力:
1. 交易语义异常检测: 分析备注/金额/时间的语义一致性
2. LLM + 规则 混合裁决: 规则引擎与 GNN 不一致时由 LLM 仲裁
3. 自然语言风险报告: LLM 生成专业反洗钱分析报告
4. 模式识别: 识别"蚂蚁搬家"、"集资诈骗"等复杂模式

设计准则:
- M1: 基于真实交易数据分析，不编造
- M2: 分析结论必须附带证据
- P1: 高风险不漏判
- 可解释: 每个判定都有 LLM 思维链
"""
import time
import json
import os
from typing import List, Dict, Any, Optional, Tuple
from graph.state import AMLState, SuspiciousTransaction
from utils import get_logger

logger = get_logger("llm_semantic")


# ============================================================
# 1. 语义异常检测
# ============================================================

SEMANTIC_SYSTEM_PROMPT = """你是一名金融语义分析师，擅长从交易的"语言-金额-时间"三个维度
检测异常语义组合。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【语义异常检测手册】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 常见语义异常模式

1. 【金额-备注不匹配】
   - "工资" 却转账 50 万元
   - "餐费" 却转账 10 万元
   - "房租" 却转账 100 万元
   - 判断规则: 金额偏离同类交易均值超过 5 倍

2. 【时间-业务不匹配】
   - 凌晨 3 点进行大额"货款"转账
   - 周末进行连续大额"借款"
   - 判断规则: 非工作时间 + 大额 + 商业性质

3. 【行为模式异常】
   - 短时间内多笔小额"借款" -> 可能是分拆
   - 多个不同账户同时向同一账户"还款" -> 可能是集资
   - 交易备注频繁变化但金额相似 -> 可能是对敲

4. 【上下文矛盾】
   - "退款"金额大于原付款金额
   - "借款"在几分钟内就"还款"
   - "投资"却没有对应协议/合同

## 输出格式 (严格 JSON)
{
    "anomaly_detected": true/false,
    "anomaly_type": "amount_mismatch|time_mismatch|behavioral|contextual",
    "severity": "low|medium|high|critical",
    "explanation": "具体的异常说明",
    "evidence": ["证据1", "证据2"],
    "normal_explanations": ["可能的正常解释"],
    "risk_amplification": 0.0-1.0  // 对原风险分的放大系数
}
"""


def _detect_semantic_anomaly(
    llm: Any,
    transaction: Dict[str, Any],
    account_history: Optional[List[Dict]] = None,
) -> Dict:
    """
    检测交易的语义异常

    Args:
        llm: LLM 实例
        transaction: 交易数据
        account_history: 账户历史交易 (可选)

    Returns:
        语义分析结果
    """
    # 构建交易上下文
    context = f"""
请分析以下交易是否存在语义异常:

【当前交易】
- 交易ID: {transaction.get('transaction_id', 'N/A')}
- 交易备注: {transaction.get('remark', '无')}
- 交易金额: {transaction.get('amount', 0):,.2f} 元
- 交易时间: {transaction.get('timestamp', 'N/A')}
- 付款账户: {transaction.get('from_account', 'N/A')}
- 收款账户: {transaction.get('to_account', 'N/A')}
- 渠道: {transaction.get('channel', 'N/A')}
"""

    if account_history:
        recent = account_history[-10:]  # 最近10笔
        context += "\n【近期账户交易】"
        for txn in recent:
            context += f"\n- {txn.get('timestamp', '?')}: {txn.get('remark', '?')} {txn.get('amount', 0):,.2f}元"

    try:
        messages = [
            {"role": "system", "content": SEMANTIC_SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ]
        response = llm.invoke(messages)
        content = response.content if hasattr(response, "content") else str(response)

        # 解析 JSON
        import re
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            return json.loads(json_match.group())
        return {"anomaly_detected": False, "explanation": "LLM 响应格式异常"}

    except Exception as e:
        logger.error(f"语义分析失败: {e}", exc_info=True)
        # 降级: 基于规则的简单检测
        return _fallback_semantic_check(transaction)


def _fallback_semantic_check(transaction: Dict) -> Dict:
    """
    降级语义检测 (无 LLM 时)

    基于简单规则检测语义异常
    """
    remark = transaction.get("remark", "")
    amount = transaction.get("amount", 0)
    timestamp = transaction.get("timestamp", "")

    anomalies = []

    # 1. 金额-备注不匹配检测
    remark_amount_limits = {
        "工资": (1000, 50000),
        "餐费": (10, 5000),
        "房租": (500, 30000),
        "借款": (1000, 100000),
        "还款": (1000, 100000),
    }

    if remark in remark_amount_limits:
        low, high = remark_amount_limits[remark]
        if amount > high * 3:
            anomalies.append(f"'{remark}'金额({amount:,.0f}元)远超正常范围({low}-{high}元)")

    # 2. 时间检测
    try:
        if timestamp:
            hour = int(timestamp.split(" ")[-1].split(":")[0])
            if 0 <= hour < 6 and amount > 50000:
                anomalies.append(f"凌晨{hour}点进行大额交易({amount:,.0f}元)")
    except (ValueError, IndexError):
        pass

    if anomalies:
        return {
            "anomaly_detected": True,
            "anomaly_type": "amount_mismatch" if "金额" in anomalies[0] else "time_mismatch",
            "severity": "medium",
            "explanation": "; ".join(anomalies),
            "evidence": anomalies,
            "normal_explanations": ["可能是特殊业务需求"],
            "risk_amplification": 1.2,  # 轻微放大风险
        }

    return {
        "anomaly_detected": False,
        "anomaly_type": "none",
        "severity": "low",
        "explanation": "未检测到明显语义异常",
        "evidence": [],
        "normal_explanations": [],
        "risk_amplification": 1.0,
    }


# ============================================================
# 2. LLM + 规则 混合裁决
# ============================================================

HYBRID_ADJUDICATION_PROMPT = """你是一名反洗钱高级裁决官。现在需要综合多个检测系统的结果，
做出最终判断。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【裁决规则】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 冲突解决矩阵

| 规则引擎判定 | GNN判定 | 语义分析 | 最终裁决 | 置信度 |
|------------|--------|---------|---------|--------|
| 高风险(≥70) | 高风险 | 异常 | 确认可疑 | 0.95 |
| 高风险(≥70) | 高风险 | 正常 | 确认可疑(降级) | 0.75 |
| 高风险(≥70) | 低风险 | 异常 | 可疑(GNN待验证) | 0.65 |
| 高风险(≥70) | 低风险 | 正常 | 需人工审核 | 0.50 |
| 低风险(<70) | 高风险 | 异常 | 可疑(规则待验证) | 0.60 |
| 低风险(<70) | 高风险 | 正常 | 需人工审核 | 0.45 |
| 低风险(<70) | 低风险 | 异常 | 低风险关注 | 0.35 |
| 低风险(<70) | 低风险 | 正常 | 正常 | 0.90 |

## 输出格式 (严格 JSON)
{
    "final_verdict": "suspicious|normal|needs_review",
    "confidence": 0.0-1.0,
    "combined_score": 0-100,
    "reasoning": "裁决推理过程",
    "contributing_signals": [
        {"source": "rule_engine", "score": 80, "weight": 0.4},
        {"source": "gnn_model", "score": 75, "weight": 0.35},
        {"source": "semantic", "score": 90, "weight": 0.25}
    ],
    "recommended_actions": ["提交STR", "持续监控", "解除关注"]
}
"""


def hybrid_adjudication(
    llm: Any,
    transaction: Dict,
    rule_score: float,
    gnn_score: float,
    semantic_result: Dict,
    rule_hits: List[str],
) -> Dict:
    """
    LLM + 规则 混合裁决

    Args:
        llm: LLM 实例
        transaction: 交易数据
        rule_score: 规则引擎风险分 (0-100)
        gnn_score: GNN 模型风险分 (0-100)
        semantic_result: 语义分析结果
        rule_hits: 命中的规则列表

    Returns:
        最终裁决结果
    """
    # 1. 检查是否可以降级处理
    if llm is None:
        return _fallback_hybrid(rule_score, gnn_score, semantic_result, rule_hits)

    # 2. 构建裁决上下文
    anomaly_text = "存在异常" if semantic_result.get("anomaly_detected") else "无异常"
    anomaly_detail = semantic_result.get("explanation", "N/A")

    context = f"""
请对以下交易做出最终裁决:

【交易信息】
- 交易ID: {transaction.get('transaction_id', 'N/A')}
- 备注: {transaction.get('remark', '无')}
- 金额: {transaction.get('amount', 0):,.2f}元
- 时间: {transaction.get('timestamp', 'N/A')}

【各检测系统评分】
- 规则引擎: {rule_score:.1f}分 (命中: {', '.join(rule_hits) if rule_hits else '无'})
- GNN模型: {gnn_score:.1f}分
- 语义分析: {anomaly_text} ({anomaly_detail})

【权重分配】
- 规则引擎权重: 0.40
- GNN模型权重: 0.35
- 语义分析权重: 0.25

请根据冲突解决矩阵做出裁决。
"""

    try:
        messages = [
            {"role": "system", "content": HYBRID_ADJUDICATION_PROMPT},
            {"role": "user", "content": context},
        ]
        response = llm.invoke(messages)
        content = response.content if hasattr(response, "content") else str(response)

        import re
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            result = json.loads(json_match.group())
            # 添加来源信息
            result["raw_scores"] = {
                "rule_engine": rule_score,
                "gnn_model": gnn_score,
                "semantic_anomaly": semantic_result.get("anomaly_detected", False),
            }
            return result
        else:
            return _fallback_hybrid(rule_score, gnn_score, semantic_result, rule_hits)

    except Exception as e:
        logger.error(f"LLM裁决失败: {e}", exc_info=True)
        return _fallback_hybrid(rule_score, gnn_score, semantic_result, rule_hits)


def _fallback_hybrid(
    rule_score: float,
    gnn_score: float,
    semantic_result: Dict,
    rule_hits: List[str],
) -> Dict:
    """
    降级混合裁决 (无 LLM 时)

    使用加权平均 + 规则矩阵
    """
    # 加权分数
    anomaly_score = 80 if semantic_result.get("anomaly_detected") else 30
    combined = (rule_score * 0.4 + gnn_score * 0.35 + anomaly_score * 0.25)

    # 裁决逻辑
    rule_high = rule_score >= 70
    gnn_high = gnn_score >= 70
    anomaly = semantic_result.get("anomaly_detected", False)

    if rule_high and gnn_high and anomaly:
        verdict = "suspicious"
        confidence = 0.90
    elif rule_high and gnn_high:
        verdict = "suspicious"
        confidence = 0.75
    elif rule_high and anomaly:
        verdict = "suspicious"
        confidence = 0.65
    elif rule_high or gnn_high:
        verdict = "needs_review"
        confidence = 0.50
    else:
        verdict = "normal"
        confidence = 0.80

    actions = {
        "suspicious": ["提交STR报告", "冻结可疑账户"],
        "needs_review": ["人工复核", "持续监控"],
        "normal": ["解除关注", "归档"],
    }

    return {
        "final_verdict": verdict,
        "confidence": confidence,
        "combined_score": round(combined, 1),
        "reasoning": f"规则({rule_score:.0f})×0.4 + GNN({gnn_score:.0f})×0.35 + 语义({anomaly_score})×0.25 = {combined:.1f}",
        "contributing_signals": [
            {"source": "rule_engine", "score": rule_score, "weight": 0.40},
            {"source": "gnn_model", "score": gnn_score, "weight": 0.35},
            {"source": "semantic", "score": anomaly_score, "weight": 0.25},
        ],
        "recommended_actions": actions.get(verdict, ["人工复核"]),
        "_fallback": True,
    }


# ============================================================
# 3. 自然语言报告生成
# ============================================================

REPORT_SYSTEM_PROMPT = """你是一名资深反洗钱分析师，需要将复杂的检测结果转化为
清晰、专业、可执行的分析报告。

报告要求:
1. 结构化: 使用标题、分点、表格等形式
2. 重点突出: 高风险交易和可疑模式要醒目标注
3. 证据充分: 每个判定都要有数据支撑
4. 行动导向: 给出具体的建议和下一步操作
5. 合规性: 符合监管机构 (FATF、FINRA) 的报告标准

输出格式: Markdown 格式的分析报告
"""


def generate_risk_report(
    llm: Any,
    suspicious_transactions: List[Dict],
    adjudications: List[Dict],
    graph_analysis: Optional[Dict] = None,
) -> str:
    """
    生成反洗钱风险报告

    Args:
        llm: LLM 实例
        suspicious_transactions: 可疑交易列表
        adjudications: 裁决结果列表
        graph_analysis: 图分析结果 (可选)

    Returns:
        Markdown 格式的报告
    """
    if llm is None:
        return _generate_fallback_report(suspicious_transactions, adjudications)

    # 构建报告上下文
    context = f"""
请根据以下检测结果生成专业的反洗钱风险报告:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【检测汇总】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 可疑交易总数: {len(suspicious_transactions)}
- 确认可疑: {sum(1 for a in adjudications if a.get('final_verdict') == 'suspicious')}
- 需人工审核: {sum(1 for a in adjudications if a.get('final_verdict') == 'needs_review')}
- 正常: {sum(1 for a in adjudications if a.get('final_verdict') == 'normal')}

【高风险交易详情】
"""

    # 添加 Top 10 高风险交易
    high_risk = sorted(
        [
            (t, a) for t, a in zip(suspicious_transactions, adjudications)
            if a.get("final_verdict") == "suspicious"
        ],
        key=lambda x: x[1].get("combined_score", 0),
        reverse=True,
    )[:10]

    for i, (txn, adj) in enumerate(high_risk, 1):
        t = txn.get("transaction", txn)
        context += f"""
{i}. **交易 {t.get('transaction_id', 'N/A')}**
   - 金额: {t.get('amount', 0):,.2f}元
   - 备注: {t.get('remark', '无')}
   - 时间: {t.get('timestamp', 'N/A')}
   - 裁决分数: {adj.get('combined_score', 0)}
   - 裁决理由: {adj.get('reasoning', 'N/A')}
"""

    if graph_analysis:
        context += f"""
【资金流向图分析】
- 检测到的资金环: {graph_analysis.get('cycles', 0)}
- 可疑账户聚类: {graph_analysis.get('communities', [])}
- 最大资金流: {graph_analysis.get('max_flow', 'N/A')}
"""

    context += """
请生成完整的反洗钱风险报告，包括:
1. 执行摘要
2. 检测概况
3. 高风险交易详情
4. 可疑模式分析
5. 建议和行动项
6. 附录: 全部检测结果表格
"""

    try:
        messages = [
            {"role": "system", "content": REPORT_SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ]
        response = llm.invoke(messages)
        return response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.error(f"语义分析失败: {e}", exc_info=True)
        return _generate_fallback_report(suspicious_transactions, adjudications)


def _generate_fallback_report(
    suspicious_transactions: List[Dict],
    adjudications: List[Dict],
) -> str:
    """降级报告生成 (无 LLM)"""
    suspicious_count = sum(1 for a in adjudications if a.get("final_verdict") == "suspicious")
    review_count = sum(1 for a in adjudications if a.get("final_verdict") == "needs_review")

    lines = [
        "# 反洗钱风险检测报告 (降级模式)",
        "",
        "## 执行摘要",
        "",
        f"- 检测可疑交易: **{len(suspicious_transactions)}** 笔",
        f"- 确认可疑: **{suspicious_count}** 笔",
        f"- 需人工审核: **{review_count}** 笔",
        f"- 正常: **{len(suspicious_transactions) - suspicious_count - review_count}** 笔",
        "",
        "## 高风险交易",
        "",
        "| 交易ID | 金额(元) | 备注 | 风险分 | 裁决 |",
        "|--------|----------|------|--------|------|",
    ]

    for txn, adj in zip(suspicious_transactions, adjudications):
        t = txn.get("transaction", txn)
        verdict_map = {"suspicious": "可疑", "needs_review": "待审核", "normal": "正常"}
        lines.append(
            f"| {t.get('transaction_id', 'N/A')} | {t.get('amount', 0):,.0f} | "
            f"{t.get('remark', '无')} | {adj.get('combined_score', 0)} | "
            f"{verdict_map.get(adj.get('final_verdict'), '?')} |"
        )

    lines.extend([
        "",
        "## 建议",
        "",
        "1. 对确认可疑的交易立即提交 STR 报告",
        "2. 对需要审核的交易进行人工复核",
        "3. 持续监控高风险账户的后续交易",
        "",
        "> 注: 本报告由降级模式生成，建议启用 LLM 以获得更精准的分析",
    ])

    return "\n".join(lines)


# ============================================================
# 4. 创建 Agent 节点
# ============================================================

def create_llm_semantic_agent(llm: Any = None):
    """
    创建 LLM 语义分析 Agent 节点

    功能:
    1. 语义异常检测
    2. 混合裁决
    3. 报告生成

    Args:
        llm: LLM 实例

    Returns:
        StateGraph 节点函数
    """

    def semantic_agent_node(state: AMLState) -> dict:
        """语义分析 Agent 节点"""
        start_time = time.time()
        print("\n" + "=" * 60)
        print("[Agent] LLM 语义分析 Agent 启动")
        print("=" * 60)

        # 获取输入：优先使用 LLM 深审确认的可疑，其次用图分析结果
        suspicious_list = state.get("llm_confirmed") or state.get("llm_reviewed") or state.get("graph_suspicious", [])
        # 戒律 P4: 安全访问 graph_data.gnn_result.scores
        graph_data = state.get("graph_data", {}) or {}
        gnn_result = graph_data.get("gnn_result") or {}
        gnn_scores = gnn_result.get("scores", {}) or {}
        graph_analysis = {
            "communities": graph_data.get("suspicious_communities", []),
            "node_count": graph_data.get("node_count", 0),
            "edge_count": graph_data.get("edge_count", 0),
        }

        print(f"  待分析交易数: {len(suspicious_list)}")
        print(f"  LLM 可用: {'是' if llm is not None else '否(降级模式)'}")

        if not suspicious_list:
            return {
                "semantic_results": [],
                "adjudications": [],
                "risk_report": "# 无待分析交易",
                "current_step": "semantic_analyzer",
                "step_times": {"semantic_analyzer": time.time() - start_time},
            }

        # 1. 语义异常检测（戒律 P2: 单条失败不阻塞整体）
        semantic_results = []
        for s in suspicious_list:
            txn = s.get("transaction", s)
            try:
                result = _detect_semantic_anomaly(llm, txn)
            except Exception as e:
                logger.error(f"语义分析失败: {e}", exc_info=True)
                result = _fallback_semantic_check(txn)
                result["_degraded"] = True
                result["_error"] = str(e)[:80]
            semantic_results.append(result)

        # 2. 混合裁决
        adjudications = []
        for i, s in enumerate(suspicious_list):
            txn = s.get("transaction", s)
            # 规则分（百分制，戒律 M3: 默认 50）
            try:
                rule_score = float(s.get("risk_score", 50))
            except (TypeError, ValueError):
                rule_score = 50.0
            # GNN 分（百分制）：从账户映射到交易
            gnn_score = 50.0
            from_acc = txn.get("from_account")
            to_acc = txn.get("to_account")
            if gnn_scores:
                # 戒律 M3: gnn_scores 是 0-1 概率，转百分制
                try:
                    if from_acc and from_acc in gnn_scores:
                        gnn_score = max(gnn_score, float(gnn_scores[from_acc]) * 100)
                    if to_acc and to_acc in gnn_scores:
                        gnn_score = max(gnn_score, float(gnn_scores[to_acc]) * 100)
                except (TypeError, ValueError):
                    pass
            semantic_result = semantic_results[i]
            rule_hits = s.get("rule_hits", [])

            try:
                adjudication = hybrid_adjudication(
                    llm, txn, rule_score, gnn_score, semantic_result, rule_hits
                )
            except Exception as e:
                logger.error(f"LLM裁决失败: {e}", exc_info=True)
                # 降级：使用 fallback 裁决
                adjudication = _fallback_hybrid(rule_score, gnn_score, semantic_result, rule_hits)
                adjudication["_degraded"] = True
                adjudication["_error"] = str(e)[:80]
            adjudications.append(adjudication)

            verdict = adjudication.get("final_verdict", "?")
            score = adjudication.get("combined_score", 0)
            print(f"  [{i+1}/{len(suspicious_list)}] {txn.get('transaction_id', 'N/A')}: "
                  f"{verdict} (分数={score})")

        # 3. 生成报告
        try:
            report = generate_risk_report(llm, suspicious_list, adjudications, graph_analysis)
        except Exception as e:
            logger.error(f"语义分析失败: {e}", exc_info=True)
            report = _generate_fallback_report(suspicious_list, adjudications)

        elapsed = time.time() - start_time
        print(f"\n  耗时: {elapsed:.2f}s")
        print("[Agent] LLM 语义分析完成")

        return {
            "semantic_results": semantic_results,
            "adjudications": adjudications,
            "risk_report": report,
            "current_step": "semantic_analyzer",
            "step_times": {"semantic_analyzer": elapsed},
        }

    return semantic_agent_node
