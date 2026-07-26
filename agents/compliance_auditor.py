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
from typing import List, Tuple
from graph.state import AMLState, STRReport


# 合规检查权重
COMPLIANCE_CHECKS = {
    "completeness": {
        "name": "报告完整性",
        "weight": 0.3,
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
        "weight": 0.35,
    },
    "risk_consistency": {
        "name": "风险等级一致性",
        "weight": 0.2,
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


def _check_risk_consistency(report: STRReport) -> Tuple[float, List[str]]:
    """检查风险等级一致性"""
    issues = []
    risk_level = report.get("risk_level", "low")
    txns = report.get("suspicious_transactions", [])

    if not txns:
        return 0.5, ["无可疑交易但标记为可疑"]

    avg_score = sum(s.get("risk_score", 0.5) for s in txns) / len(txns)
    max_score = max(s.get("risk_score", 0.5) for s in txns)

    # 期望的风险等级范围
    expected = "low"
    if max_score >= 0.85 or (avg_score >= 0.7 and len(txns) >= 5):
        expected = "critical"
    elif max_score >= 0.7 or (avg_score >= 0.55 and len(txns) >= 3):
        expected = "high"
    elif max_score >= 0.5 or len(txns) >= 2:
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


def _audit_report(report: STRReport) -> Tuple[str, float, List[str], str]:
    """
    审核单份报告

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

    risk_score, risk_issues = _check_risk_consistency(report)
    all_issues.extend(risk_issues)

    format_score, format_issues = _check_format(report)
    all_issues.extend(format_issues)

    # 加权总分
    total_score = (
        completeness_score * COMPLIANCE_CHECKS["completeness"]["weight"]
        + evidence_score * COMPLIANCE_CHECKS["evidence_sufficiency"]["weight"]
        + risk_score * COMPLIANCE_CHECKS["risk_consistency"]["weight"]
        + format_score * COMPLIANCE_CHECKS["format_compliance"]["weight"]
    )

    # 判断结果
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
            "passed": len([r for r in final_reports if r["compliance_status"] == "passed"]),
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
