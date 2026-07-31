"""
合规审核 Agent
职责: 参考央行《大额交易和可疑交易报告管理办法》进行格式和内容校验

检查维度:
  1. 报告结构完整性 (原有6项 + 增强)
  2. 内容实质性 (不可空泛)
  3. 证据链完整性
  4. 风险等级合理性

产出:
  compliance: {passed, issues, warnings, score, format_check, content_check, status}
"""
import logging
from datetime import datetime
from graph.state import AMLState

log = logging.getLogger("aml.agent.compliance")

# ---- 结构检查 (央行报告必要章节) ----
_STRUCTURE_CHECKS = [
    ("标题",         lambda r: "可疑交易报告" in r.get("str_report", "")),
    ("报告时间",     lambda r: "报告时间" in r.get("str_report", "")),
    ("数据来源",     lambda r: "数据来源" in r.get("str_report", "")),
    ("数据概览",     lambda r: "数据概览" in r.get("str_report", "")),
    ("规则检测结果", lambda r: "规则引擎" in r.get("str_report", "")),
    ("GNN/图分析",   lambda r: "GNN" in r.get("str_report", "") or
                               "图分析" in r.get("str_report", "")),
    ("风险分层",     lambda r: "高风险" in r.get("str_report", "") or
                               "中风险" in r.get("str_report", "")),
    ("建议措施",     lambda r: "建议" in r.get("str_report", "")),
    ("交易详情",     lambda r: "付款方" in r.get("str_report", "")),
]

# ---- 内容实质性检查 ----
def _check_content_substance(report: str) -> list:
    """检查报告内容是否空泛"""
    issues = []
    if len(report) < 200:
        issues.append("报告内容过短 (<200字符)")
    if "N/A" in report:
        count_na = report.count("N/A")
        if count_na > 5:
            issues.append(f"过多未填充字段 ({count_na}处 N/A)")
    if "无高风险交易" in report:
        pass  # 合法情况
    return issues


# ---- 证据链检查 ----
def _check_evidence_chain(report: str) -> list:
    """检查报告中是否有具体的证据支撑"""
    issues = []
    indicators = ["证据", "付款方", "收款方", "金额", "规则分布"]
    missing = [i for i in indicators if i not in report]
    if len(missing) > 2:
        issues.append(f"证据链不完整，缺失: {', '.join(missing[:3])}")
    return issues


# ---- 风险评分合理性 ----
def _check_risk_reasonableness(state: AMLState) -> list:
    """检查风险评分的合理性"""
    warnings = []
    rr = state.get("rule_report", {})
    summary = rr.get("summary", {})
    high = summary.get("high_risk", 0)
    total = summary.get("total_hits", 0)

    if total > 0 and high == 0:
        warnings.append("有命中但无高风险交易，检查评分阈值是否合理")
    if total > 100:
        warnings.append(f"命中率偏高 ({total}笔), 可能存在规则过度触发")
    return warnings


def run(state: AMLState) -> dict:
    """合规格式 + 内容审核"""
    updates = {"current_step": "合规审核", "compliance": {}}

    try:
        report = state.get("str_report", "")
        if not report:
            updates["compliance"] = {
                "passed": False, "issues": ["报告为空"],
                "warnings": [], "score": 0,
                "status": "合规未通过: 报告为空",
            }
            return updates

        # 1. 结构完整性
        structure_issues = []
        format_check = {}
        for name, check_fn in _STRUCTURE_CHECKS:
            ok = check_fn(state)
            format_check[name] = ok
            if not ok:
                structure_issues.append(f"缺少: {name}")

        # 2. 内容实质性
        content_issues = _check_content_substance(report)

        # 3. 证据链
        evidence_issues = _check_evidence_chain(report)

        # 4. 风险评分合理性 (warnings, not blocking)
        risk_warnings = _check_risk_reasonableness(state)

        # 综合评估
        all_issues = structure_issues + content_issues + evidence_issues
        has_content = len(report) > 100
        structure_pass = len(structure_issues) <= 1  # 允许缺1项
        content_pass = len(content_issues) == 0
        passed = structure_pass and content_pass and has_content

        # 评分 (满分100)
        score = 100
        score -= len(structure_issues) * 8
        score -= len(content_issues) * 10
        score -= len(evidence_issues) * 6
        if not has_content:
            score -= 30
        score = max(0, min(100, score))

        status = (
            f"合规通过 (评分: {score}/100)"
            if passed and score >= 60
            else f"合规未通过 (评分: {score}/100): "
                 f"{'; '.join(all_issues[:3])}"
        )

        result = {
            "passed": passed,
            "issues": all_issues,
            "warnings": risk_warnings,
            "score": score,
            "format_check": format_check,
            "content_check": {
                "has_evidence": len(evidence_issues) == 0,
                "has_substance": len(content_issues) == 0,
                "content_length": len(report),
            },
            "status": status,
        }

        updates["compliance"] = result
        updates["messages"] = [_msg("ok", status)]
        log.info(f"合规审核: {status}")
    except Exception as e:
        log.warning(f"合规审核失败: {e}")
        updates["errors"] = [f"合规审核: {e}"]

    return updates


def _msg(status: str, summary: str) -> dict:
    return {
        "agent": "compliance",
        "timestamp": datetime.now().isoformat(),
        "summary": summary,
        "status": status,
    }

# ---- 维护旧字段名 compatibility ----
# 注: 下游通过 state.get("compliance") 读取，无需额外兼容。
