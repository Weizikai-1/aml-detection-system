"""
Agent 6: 合规审核 Agent

职责: 对生成的STR报告进行合规性检查，
      确认是否符合反洗钱报告要求，
      分流为: 自动通过 / 需人工审核 / 驳回
模式: create_compliance_auditor_agent(llm) -> node_function

合规检查项:
1. 报告完整性(必填字段是否齐全)
2. 证据充分性(是否有足够证据支撑可疑判断)
3. 风险等级合理性
4. 格式规范性
"""
import time
from typing import List, Tuple, Optional
from graph.state import AMLState, STRReport

# 合规检查权重
COMPLIANCE_CHECKS = {
    "completeness": {
        "name": "报告完整性",
        "weight": 0.2,
        "required_fields": [
            "report_id",
            "primary_account",
            "suspicious_transactions",
            "total_suspicious_amount",
            "risk_level",
            "analysis_summary",
            "evidence_chain",
            "disposal_suggestion",
        ],
    },
    "evidence_sufficiency": {
        "name": "证据充分性",
        "weight": 0.25,
    },
    "rule_compliance": {
        "name": "戒律合规性",
        "weight": 0.25,
    },
    "risk_consistency": {
        "name": "风险等级一致性",
        "weight": 0.15,
    },
    "format_compliance": {
        "name": "格式规范性",
        "weight": 0.15,
    },
}


def _check_completeness(report: STRReport) -> Tuple[float, List[str]]:
    """检查报告完整性"""
    fields = COMPLIANCE_CHECKS["completeness"]["required_fields"]
    missing = []
    for field in fields:
        value = report.get(field)
        if value is None or value == "" or (isinstance(value, list) and len(value) == 0):
            missing.append(field)

    score = 1.0 - len(missing) / len(fields)
    issues = [f"缺失必填字段: {f}" for f in missing]
    return max(score, 0), issues


def _check_evidence(report: STRReport) -> Tuple[float, List[str]]:
    """检查证据充分性"""
    issues = []
    evidence = report.get("evidence_chain", [])
    txns = report.get("suspicious_transactions", [])

    score = 0.0

    # 证据条数
    if len(evidence) >= 3:
        score += 0.4
    elif len(evidence) >= 2:
        score += 0.25
    elif len(evidence) >= 1:
        score += 0.1
    else:
        issues.append("证据链为空")

    # 可疑交易数量
    if len(txns) >= 5:
        score += 0.3
    elif len(txns) >= 3:
        score += 0.2
    elif len(txns) >= 1:
        score += 0.1

    # 可疑模式多样性
    patterns = set()
    for s in txns:
        for r in s.get("rule_hits", []):
            patterns.add(r)
    if len(patterns) >= 3:
        score += 0.3
    elif len(patterns) >= 2:
        score += 0.2
    elif len(patterns) >= 1:
        score += 0.1

    return min(score, 1.0), issues


def _check_rule_compliance(report: STRReport) -> Tuple[float, List[str]]:
    """检查业务戒律合规性（M1-M4 强制要求，P1-P4 严格禁止）"""
    issues = []
    score = 1.0
    txns = report.get("suspicious_transactions", [])

    if not txns:
        return 0.0, ["无可疑交易，无法校验戒律合规性"]

    # M2: 必须标注每个可疑交易的理由 —— 检查每笔是否有 rule_hits
    no_rule_hits = [i for i, s in enumerate(txns) if not s.get("rule_hits")]
    if no_rule_hits:
        score -= 0.25
        issues.append(f"M2违规: {len(no_rule_hits)} 笔交易缺少命中规则标注")

    # M2: 检查每笔是否有 evidence（可疑理由证据）
    no_evidence = [i for i, s in enumerate(txns) if not s.get("evidence")]
    if no_evidence:
        score -= 0.2
        issues.append(f"M2违规: {len(no_evidence)} 笔交易缺少证据说明")

    # M3: 风险评分范围 0-100 —— 检查每笔 risk_score 是否在范围内
    # 戒律 M1: risk_score 可能为 None（键存在但值为 None），先判断 isinstance
    invalid_scores = []
    none_scores = []
    for i, s in enumerate(txns):
        rs = s.get("risk_score")
        if rs is None or not isinstance(rs, (int, float)):
            none_scores.append((i, rs))
        elif rs < 0 or rs > 100:
            invalid_scores.append((i, rs))
    if invalid_scores:
        score -= 0.2
        issues.append(f"M3违规: {len(invalid_scores)} 笔交易风险评分超出 0-100 范围")
    if none_scores:
        score -= 0.1
        issues.append(f"M3违规: {len(none_scores)} 笔交易风险评分为 None 或非法类型")

    # P3: 禁止无证据判定可疑 —— rule_hits + evidence 都空的直接违规
    no_evidence_at_all = [
        i for i, s in enumerate(txns)
        if not s.get("rule_hits") and not s.get("evidence")
    ]
    if no_evidence_at_all:
        score -= 0.3
        issues.append(f"P3违规: {len(no_evidence_at_all)} 笔交易无任何证据却被判定可疑")

    # P1: 高风险交易不遗漏 —— 检查高风险(≥70)是否都有明确分析记录
    # 戒律 M1: risk_score 可能为 None，先过滤非数值
    high_risk = [s for s in txns if isinstance(s.get("risk_score"), (int, float)) and s.get("risk_score") >= 70]
    if high_risk:
        no_llm_analysis = [s for s in high_risk if not s.get("llm_analysis")]
        # 降级模式下没有 llm_analysis 是正常的，只在有 LLM 时检查
        has_any_llm = any(s.get("llm_analysis") for s in txns)
        if has_any_llm and len(no_llm_analysis) > 0:
            score -= 0.1
            issues.append(f"P1提示: {len(no_llm_analysis)} 笔高风险交易缺少LLM分析记录")

    return max(score, 0.0), issues


def _check_risk_consistency(report: STRReport) -> Tuple[float, List[str]]:
    """检查风险等级一致性（百分制）"""
    issues = []
    risk_level = report.get("risk_level", "low")
    txns = report.get("suspicious_transactions", [])

    if not txns:
        return 0.0, ["无可疑交易但标记为可疑"]

    # 戒律 M1: risk_score 可能为 None，过滤 None 或用 (s.get("risk_score") or 50) 兜底
    valid_scores = [s.get("risk_score") or 50 for s in txns]
    # 二次保护：过滤掉非数值（保守用 50 兜底）
    valid_scores = [s if isinstance(s, (int, float)) else 50 for s in valid_scores]
    avg_score = sum(valid_scores) / len(valid_scores) if valid_scores else 0
    max_score = max(valid_scores) if valid_scores else 0

    # 期望的风险等级范围
    expected = "low"
    if max_score >= 85 or (avg_score >= 70 and len(txns) >= 5):
        expected = "critical"
    elif max_score >= 70 or (avg_score >= 55 and len(txns) >= 3):
        expected = "high"
    elif max_score >= 50 or len(txns) >= 2:
        expected = "medium"

    risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    diff = abs(risk_order.get(risk_level, 0) - risk_order.get(expected, 0))

    if diff == 0:
        score = 1.0
    elif diff == 1:
        score = 0.7
        issues.append(f"风险等级{risk_level}与预期{expected}有偏差")
    else:
        score = 0.4
        issues.append(f"风险等级{risk_level}与预期{expected}偏差较大")

    return score, issues


def _check_format(report: STRReport) -> Tuple[float, List[str]]:
    """检查格式规范性"""
    issues = []
    score = 1.0

    # 报告ID格式
    report_id = report.get("report_id", "")
    if not report_id.startswith("STR-"):
        score -= 0.2
        issues.append("报告ID格式不符合规范")

    # 日期格式
    report_date = report.get("report_date", "")
    if len(report_date) < 10:
        score -= 0.1
        issues.append("报告日期格式不完整")

    # 摘要长度
    summary = report.get("analysis_summary", "")
    if len(summary) < 20:
        score -= 0.2
        issues.append("分析摘要过短，信息不足")

    # 处置建议
    disposal = report.get("disposal_suggestion", "")
    if len(disposal) < 10:
        score -= 0.15
        issues.append("处置建议过于简略")

    return max(score, 0), issues


def _audit_report(report: STRReport, use_memory: bool = True) -> Tuple[str, float, List[str], str]:
    """
    审核单份报告

    Args:
        report: STR报告
        use_memory: 是否使用记忆系统参考（默认开启，失败自动降级）

    Returns:
        status: passed / human_review / rejected
        score: 合规评分(0-1)
        issues: 问题列表
        notes: 审核备注
    """
    all_issues = []
    total_score = 0.0

    # 各项检查
    completeness_score, completeness_issues = _check_completeness(report)
    all_issues.extend(completeness_issues)

    evidence_score, evidence_issues = _check_evidence(report)
    all_issues.extend(evidence_issues)

    rule_score, rule_issues = _check_rule_compliance(report)
    all_issues.extend(rule_issues)

    risk_score, risk_issues = _check_risk_consistency(report)
    all_issues.extend(risk_issues)

    format_score, format_issues = _check_format(report)
    all_issues.extend(format_issues)

    # 加权总分
    total_score = (
        completeness_score * COMPLIANCE_CHECKS["completeness"]["weight"]
        + evidence_score * COMPLIANCE_CHECKS["evidence_sufficiency"]["weight"]
        + rule_score * COMPLIANCE_CHECKS["rule_compliance"]["weight"]
        + risk_score * COMPLIANCE_CHECKS["risk_consistency"]["weight"]
        + format_score * COMPLIANCE_CHECKS["format_compliance"]["weight"]
    )

    # 综合评分
    if total_score >= 0.8:
        status = "passed"
        notes = f"合规审核通过，综合评分{total_score:.2f}"
    elif total_score >= 0.5:
        status = "human_review"
        notes = f"需人工审核，综合评分{total_score:.2f}，存在{len(all_issues)}项待确认"
    else:
        status = "rejected"
        notes = f"合规审核未通过，综合评分{total_score:.2f}，需补充完善后重新提交"

    return status, round(total_score, 3), all_issues, notes


def create_compliance_auditor_agent(llm=None):
    """
    创建合规审核Agent

    Args:
        llm: LLM实例(可用于更智能的合规判断，当前版本为规则审核)

    Returns:
        可直接传入 StateGraph.add_node 的节点函数
    """

    def compliance_auditor_node(state: AMLState) -> dict:
        """
        合规审核节点函数

        对所有STR报告进行合规检查，
        分流为: 通过 / 需人工审核 / 驳回
        """
        start_time = time.time()
        print("\n" + "=" * 60)
        print("[Agent 6] 合规审核 Agent 启动")
        print("=" * 60)

        reports = state.get("str_reports", [])
        print(f"  待审核报告数: {len(reports)}")

        if len(reports) == 0:
            print("[Agent 6] 无报告需审核")
            return {
                "final_reports": [],
                "rejected_reports": [],
                "human_review_tasks": [],
                "compliance_stats": {"total": 0, "passed": 0, "human_review": 0, "rejected": 0},
                "compliance_summary": "无报告",
                "current_step": "compliance_auditor",
            }

        final_reports = []
        rejected_reports = []
        human_review_tasks = []

        for i, report in enumerate(reports):
            idx = i + 1
            primary = report.get("primary_account", "N/A")
            risk = report.get("risk_level", "unknown")
            print(f"  [{idx}/{len(reports)}] 审核报告 {report.get('report_id', 'N/A')}", end=" ")
            print(f"(账户: {primary}, 风险: {risk}) ...", end=" ")

            status, score, issues, notes = _audit_report(report)

            # 更新报告的合规状态
            report_copy = dict(report)
            report_copy["compliance_status"] = status
            report_copy["compliance_notes"] = notes

            if status == "passed":
                report_copy["final_decision"] = "自动通过，准予上报"
                final_reports.append(report_copy)
                print(f"✓ 通过 ({score:.2f})")
            elif status == "human_review":
                human_review_tasks.append({
                    "report_id": report_copy["report_id"],
                    "primary_account": primary,
                    "risk_level": risk,
                    "compliance_score": score,
                    "issues": issues,
                    "priority": "high" if risk in ["critical", "high"] else "medium",
                })
                final_reports.append(report_copy)  # 也放入最终报告，标记待人工
                print(f"⚠ 人工审核 ({score:.2f})")
            else:
                report_copy["final_decision"] = "驳回，需补充完善"
                rejected_reports.append(report_copy)
                print(f"✗ 驳回 ({score:.2f})")

        elapsed = time.time() - start_time

        # 统计
        stats = {
            "total": len(reports),
            # 戒律 M1: 使用 .get() 避免 KeyError（compliance_status 可能为 None）
            "passed": len([r for r in final_reports if r.get("compliance_status") == "passed"]),
            "human_review": len(human_review_tasks),
            "rejected": len(rejected_reports),
        }

        # 生成摘要
        summary_lines = [
            f"共审核 {len(reports)} 份STR报告",
            f"自动通过: {stats['passed']} 份",
            f"需人工审核: {stats['human_review']} 份",
            f"驳回: {stats['rejected']} 份",
        ]
        if human_review_tasks:
            high_priority = [t for t in human_review_tasks if t["priority"] == "high"]
            if high_priority:
                summary_lines.append(f"其中高优先级人工审核任务: {len(high_priority)} 份")

        compliance_summary = "\n".join(summary_lines)

        print(f"\n  {'─' * 50}")
        print(f"  合规审核汇总:")
        print(f"    - 总报告数: {len(reports)}")
        print(f"    - 自动通过: {stats['passed']} 份")
        print(f"    - 需人工审核: {stats['human_review']} 份")
        print(f"    - 驳回: {stats['rejected']} 份")
        print(f"  耗时: {elapsed:.2f} 秒")
        print("[Agent 6] 合规审核完成")

        return {
            "final_reports": final_reports,
            "rejected_reports": rejected_reports,
            "human_review_tasks": human_review_tasks,
            "compliance_stats": stats,
            "compliance_summary": compliance_summary,
            "current_step": "compliance_auditor",
            "step_times": {"compliance_auditor": elapsed},
        }

    return compliance_auditor_node
