"""
Agent 5: 报告生成 Agent

职责: 将LLM确认的可疑交易生成符合央行格式的STR可疑交易报告
模式: create_report_generator_agent(llm) -> node_function

报告结构:
1. 报告基本信息(编号、日期、类型)
2. 主体信息(主涉案账户、关联账户)
3. 可疑交易明细
4. 可疑模式分析
5. 证据链
6. 处置建议
"""
import time
import uuid
from datetime import datetime
from collections import defaultdict
from typing import Dict, List
from graph.state import AMLState, SuspiciousTransaction, STRReport


def _determine_primary_account(rule_hits: list, transaction: dict) -> str:
    """根据命中规则类型智能确定主涉案方

    Args:
        rule_hits: 命中规则列表
        transaction: 交易数据
    Returns:
        主涉案方账户ID
    """
    rules = set()
    for hit in rule_hits:
        if isinstance(hit, str):
            rules.add(hit)

    # 分拆转账：付款方是主涉案方（拆分方）
    if "分拆转账" in rules:
        return transaction.get("from_account", "UNKNOWN")

    # 快进快出：收款方（中间账户）是主涉案方
    if "快进快出" in rules:
        return transaction.get("to_account", "UNKNOWN")

    # 对敲交易：取付款方
    if "对敲交易" in rules:
        return transaction.get("from_account", "UNKNOWN")

    # 默认：收款方
    return transaction.get("to_account", "UNKNOWN")


def _group_by_account(suspicious_list: List[SuspiciousTransaction]) -> Dict[str, List[SuspiciousTransaction]]:
    """按主涉案账户分组可疑交易——根据命中规则智能确定主涉案方"""
    groups: Dict[str, List[SuspiciousTransaction]] = defaultdict(list)

    for s in suspicious_list:
        txn = s.get("transaction", {})
        # 根据命中规则类型智能确定主涉案方（戒律 M1: 缺失字段不抛错，标记为 UNKNOWN）
        primary = _determine_primary_account(s.get("rule_hits", []), txn)
        groups[primary].append(s)

    return groups


def _assess_risk_level(suspicious_list: List[SuspiciousTransaction]) -> str:
    """评估一组可疑交易的整体风险等级（百分制）"""
    if not suspicious_list:
        return "low"

    max_score = max(s.get("risk_score", 50) for s in suspicious_list)
    avg_score = sum(s.get("risk_score", 50) for s in suspicious_list) / len(suspicious_list)
    count = len(suspicious_list)

    # 综合判断
    if max_score >= 85 or (avg_score >= 70 and count >= 5):
        return "critical"
    elif max_score >= 70 or (avg_score >= 55 and count >= 3):
        return "high"
    elif max_score >= 50 or count >= 2:
        return "medium"
    else:
        return "low"


def _generate_evidence_chain(suspicious_list: List[SuspiciousTransaction]) -> List[str]:
    """汇总证据链"""
    evidence_set = set()
    for s in suspicious_list:
        for ev in s.get("evidence", []):
            evidence_set.add(ev)
    return sorted(list(evidence_set))


def _summarize_patterns(suspicious_list: List[SuspiciousTransaction]) -> List[str]:
    """总结可疑模式"""
    rule_counts = defaultdict(int)
    for s in suspicious_list:
        for rule in s.get("rule_hits", []):
            rule_counts[rule] += 1

    patterns = []
    for rule, count in sorted(rule_counts.items(), key=lambda x: x[1], reverse=True):
        patterns.append(f"{rule}({count}笔)")
    return patterns


def cluster_into_cases(suspicious_list: List[SuspiciousTransaction]) -> List[dict]:
    """
    将可疑交易按「主账户 + 命中规则」聚成案件

    同一账户、同一规则的多笔交易归为一个案件，便于分析师整体把握。

    戒律:
    - P1 不遗漏: 所有可疑交易都会被分配到某个案件
    - P3 有证据: 每个案件有完整的证据汇总

    Returns:
        案件列表，每个案件包含:
        - case_id: 案件ID
        - primary_account: 主涉案账户
        - rule_type: 规则类型
        - transactions: 涉及交易列表
        - txn_count: 交易数
        - total_amount: 涉案总金额
        - max_risk_score: 最高风险分
        - evidence_summary: 证据汇总
    """
    # 先按账户分组
    account_groups = _group_by_account(suspicious_list)

    cases = []
    case_num = 0
    for account, txns in account_groups.items():
        # 再按规则分组（一笔交易可能命中多规则，归到第一个规则）
        rule_groups: Dict[str, List[SuspiciousTransaction]] = defaultdict(list)
        for s in txns:
            rules = s.get("rule_hits", [])
            primary_rule = rules[0] if rules else "未知"
            rule_groups[primary_rule].append(s)

        for rule, rule_txns in rule_groups.items():
            case_num += 1
            total_amount = sum(
                s.get("transaction", {}).get("amount", 0) for s in rule_txns
            )
            # 戒律 M3: 默认值改为 50（0-100 量纲），避免误报风险等级偏低
            max_score = max((s.get("risk_score") or 50) for s in rule_txns)

            # 证据汇总（去重前 5 条）
            all_evidence = []
            seen = set()
            for s in rule_txns:
                for ev in s.get("evidence", []):
                    if ev not in seen:
                        seen.add(ev)
                        all_evidence.append(ev)
                    if len(all_evidence) >= 5:
                        break
                if len(all_evidence) >= 5:
                    break

            cases.append({
                "case_id": f"CASE-{case_num:04d}",
                "primary_account": account,
                "rule_type": rule,
                "transaction_ids": [
                    s.get("transaction", {}).get("transaction_id", "")
                    for s in rule_txns
                ],
                "txn_count": len(rule_txns),
                "total_amount": round(total_amount, 2),
                "max_risk_score": max_score,
                "evidence_summary": all_evidence,
            })

    # 按风险分降序
    cases.sort(key=lambda x: x["max_risk_score"], reverse=True)
    return cases


def _build_str_report(
    account: str,
    suspicious_list: List[SuspiciousTransaction],
) -> STRReport:
    """
    为单个主涉案账户生成一份STR报告
    """
    now = datetime.now()
    report_id = f"STR-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

    # 收集关联账户
    related_accounts = set()
    total_amount = 0.0
    for s in suspicious_list:
        txn = s.get("transaction", {})
        # 戒律 M1: 缺失账户字段不抛错，使用 .get()
        from_a = txn.get("from_account")
        to_a = txn.get("to_account")
        if from_a:
            related_accounts.add(from_a)
        if to_a:
            related_accounts.add(to_a)
        try:
            total_amount += float(txn.get("amount", 0))
        except (TypeError, ValueError):
            pass

    # 移除主账户自己
    related_accounts.discard(account)

    risk_level = _assess_risk_level(suspicious_list)
    patterns = _summarize_patterns(suspicious_list)
    evidence_chain = _generate_evidence_chain(suspicious_list)

    # 生成分析摘要
    if risk_level == "critical":
        summary = f"账户[{account}]存在高度可疑的洗钱特征，涉及{len(suspicious_list)}笔可疑交易，总金额{total_amount:,.2f}元。多种可疑模式叠加，建议立即上报并冻结相关账户。"
        disposal = "立即上报人民银行反洗钱局，冻结涉案账户，启动司法协查流程。"
    elif risk_level == "high":
        summary = f"账户[{account}]存在明显可疑交易特征，涉及{len(suspicious_list)}笔交易，总金额{total_amount:,.2f}元。可疑模式明确，需重点关注。"
        disposal = "列入重点监控名单，上报可疑交易报告，加强后续交易监测。"
    elif risk_level == "medium":
        summary = f"账户[{account}]存在若干可疑交易迹象，涉及{len(suspicious_list)}笔交易，总金额{total_amount:,.2f}元。需持续关注。"
        disposal = "提交可疑交易报告，列入关注名单，增加监测频次。"
    else:
        summary = f"账户[{account}]存在少量可疑交易提示，涉及{len(suspicious_list)}笔交易，总金额{total_amount:,.2f}元。证据相对较弱。"
        disposal = "持续观察，暂不正式上报，记录备查。"

    return {
        "report_id": report_id,
        "report_date": now.strftime("%Y-%m-%d %H:%M:%S"),
        "report_type": "初始报告",
        "primary_account": account,
        "related_accounts": sorted(list(related_accounts)),
        "customer_profile": {
            # 戒律 M1: 不编造账户类型，缺失字段标记为 None 并加 missing 标记
            "account_type": None,
            "account_type_missing": True,
            "risk_rating": risk_level,
            "monitoring_status": "active",
        },
        "suspicious_transactions": suspicious_list,
        "total_suspicious_amount": round(total_amount, 2),
        "suspicious_patterns": patterns,
        "risk_level": risk_level,
        "analysis_summary": summary,
        "evidence_chain": evidence_chain,
        "disposal_suggestion": disposal,
        "compliance_status": "pending",
        "compliance_notes": None,
        "reviewer": None,
        "final_decision": None,
    }


def create_report_generator_agent(llm=None):
    """
    创建报告生成Agent

    Args:
        llm: LLM实例(可用于生成更自然语言的报告摘要，当前版本为模板生成)

    Returns:
        可直接传入 StateGraph.add_node 的节点函数
    """

    def report_generator_node(state: AMLState) -> dict:
        """
        报告生成节点函数

        将LLM确认的可疑交易按主涉案账户分组，
        生成结构化STR报告
        """
        start_time = time.time()
        print("\n" + "=" * 60)
        print("[Agent 5] 报告生成 Agent 启动")
        print("=" * 60)

        confirmed = state.get("llm_confirmed", [])
        print(f"  待生成报告的确认可疑交易数: {len(confirmed)}")

        if len(confirmed) == 0:
            print("[Agent 5] 无确认可疑交易，不生成报告")
            return {
                "str_reports": [],
                "report_count": 0,
                "current_step": "report_generator",
                "report_generation_stats": {"total": 0, "by_risk": {}},
            }

        # 按主账户分组
        groups = _group_by_account(confirmed)
        print(f"  涉及主涉案账户数: {len(groups)}")

        # 为每个主账户生成一份报告
        reports = []
        for account, txns in groups.items():
            report = _build_str_report(account, txns)
            reports.append(report)

        # 按风险等级排序
        risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        reports.sort(key=lambda r: risk_order.get(r.get("risk_level", "low"), 99))

        # 统计
        risk_counts = defaultdict(int)
        for r in reports:
            risk_counts[r.get("risk_level", "unknown")] += 1

        elapsed = time.time() - start_time

        # 案件聚类（戒律 P3: 把关联交易聚成案件，便于整体分析）
        cases = cluster_into_cases(confirmed)

        print(f"\n  {'─' * 50}")
        print(f"  报告生成汇总:")
        print(f"    - 报告总数: {len(reports)} 份")
        for risk in ["critical", "high", "medium", "low"]:
            if risk_counts.get(risk, 0) > 0:
                print(f"    - {risk.upper()}风险: {risk_counts[risk]} 份")
        print(f"    - 涉及可疑交易: {len(confirmed)} 笔")
        print(f"    - 聚类案件数: {len(cases)} 个")
        print(f"  耗时: {elapsed:.2f} 秒")
        print("[Agent 5] 报告生成完成")

        return {
            "str_reports": reports,
            "report_count": len(reports),
            "suspicious_cases": cases,
            "case_count": len(cases),
            "current_step": "report_generator",
            "step_times": {"report_generator": elapsed},
            "report_generation_stats": {
                "total": len(reports),
                "total_transactions": len(confirmed),
                "by_risk": dict(risk_counts),
                "case_count": len(cases),
            },
        }

    return report_generator_node
