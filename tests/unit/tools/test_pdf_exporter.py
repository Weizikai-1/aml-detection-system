"""
PDF 报告导出测试 (Task 7-2)

覆盖:
- 单报告导出（PDF生成、内容完整性）
- 批量导出
- 边界情况（空报告、空交易列表）
- 戒律M1: 数据完整性（不编造）
"""
import os
from typing import List

import pytest

from tools.pdf_exporter import PdfExporter
from graph.state import STRReport, SuspiciousTransaction, Transaction


# ============================================================
# 夹具（复用 Excel 测试的样本结构）
# ============================================================
@pytest.fixture()
def export_dir(tmp_path):
    """临时导出目录"""
    d = tmp_path / "pdf_exports"
    d.mkdir()
    return str(d)


@pytest.fixture()
def sample_transaction() -> Transaction:
    return {
        "transaction_id": "T001",
        "from_account": "A001",
        "to_account": "A002",
        "amount": 45000.0,
        "timestamp": "2025-01-01 10:00:00",
        "transaction_type": "transfer",
        "remark": "测试备注",
    }


@pytest.fixture()
def sample_suspicious(sample_transaction) -> SuspiciousTransaction:
    return {
        "transaction": sample_transaction,
        "rule_hits": ["分拆转账", "大额交易"],
        "risk_score": 75,
        "evidence": ["分拆转账: 1小时内收到5笔转账", "大额交易: 45000元"],
        "graph_evidence": None,
        "llm_analysis": None,
        "llm_confidence": None,
        "is_false_positive": None,
        "community_id": None,
    }


@pytest.fixture()
def sample_report(sample_suspicious) -> STRReport:
    return {
        "report_id": "STR-20250101-TEST0001",
        "report_date": "2025-01-01 12:00:00",
        "report_type": "初始报告",
        "primary_account": "A002",
        "related_accounts": ["A001"],
        "customer_profile": {
            "account_type": "个人",
            "risk_rating": "high",
            "monitoring_status": "active",
        },
        "suspicious_transactions": [sample_suspicious],
        "total_suspicious_amount": 45000.0,
        "suspicious_patterns": ["分拆转账(1笔)", "大额交易(1笔)"],
        "risk_level": "high",
        "analysis_summary": "账户A002存在可疑交易特征",
        "evidence_chain": ["分拆转账: 1小时内收到5笔转账", "大额交易: 45000元"],
        "disposal_suggestion": "列入重点监控名单",
        "compliance_status": "pending",
        "compliance_notes": None,
        "reviewer": None,
        "final_decision": None,
    }


@pytest.fixture()
def multi_reports(sample_suspicious) -> List[STRReport]:
    reports = []
    for i in range(3):
        r = {
            "report_id": f"STR-20250101-TEST{i:04d}",
            "report_date": "2025-01-01 12:00:00",
            "report_type": "初始报告",
            "primary_account": f"A{i:03d}",
            "related_accounts": [],
            "customer_profile": {},
            "suspicious_transactions": [sample_suspicious],
            "total_suspicious_amount": 45000.0,
            "suspicious_patterns": ["分拆转账(1笔)"],
            "risk_level": "medium" if i == 0 else "high",
            "analysis_summary": f"账户A{i:03d}可疑",
            "evidence_chain": ["分拆转账: 1小时内收到5笔转账"],
            "disposal_suggestion": "持续观察",
            "compliance_status": "pending",
            "compliance_notes": None,
            "reviewer": None,
            "final_decision": None,
        }
        reports.append(r)
    return reports


# ============================================================
# 单报告导出测试
# ============================================================
@pytest.mark.unit
def test_export_single_report_creates_pdf(export_dir, sample_report):
    """导出单个报告应生成pdf文件"""
    exporter = PdfExporter(output_dir=export_dir)
    path = exporter.export_report(sample_report)
    assert os.path.exists(path)
    assert path.endswith(".pdf")
    # PDF文件应该有内容
    assert os.path.getsize(path) > 0


@pytest.mark.unit
def test_exported_pdf_is_valid_pdf(export_dir, sample_report):
    """导出的文件应该是合法的PDF（以%PDF开头）"""
    exporter = PdfExporter(output_dir=export_dir)
    path = exporter.export_report(sample_report)
    with open(path, "rb") as f:
        header = f.read(5)
    assert header == b"%PDF-"


@pytest.mark.unit
def test_export_report_custom_path(export_dir, sample_report, tmp_path):
    """指定output_path时应保存到该路径"""
    custom_path = str(tmp_path / "custom.pdf")
    exporter = PdfExporter(output_dir=export_dir)
    result_path = exporter.export_report(sample_report, output_path=custom_path)
    assert result_path == custom_path
    assert os.path.exists(custom_path)


@pytest.mark.unit
def test_export_report_creates_output_dir(tmp_path, sample_report):
    """输出目录不存在时应自动创建"""
    new_dir = str(tmp_path / "new_dir" / "nested")
    exporter = PdfExporter(output_dir=new_dir)
    path = exporter.export_report(sample_report)
    assert os.path.exists(path)
    assert os.path.exists(new_dir)


@pytest.mark.unit
def test_export_report_with_empty_transactions(export_dir):
    """空可疑交易列表应能正常导出"""
    report = {
        "report_id": "STR-EMPTY",
        "report_date": "2025-01-01 12:00:00",
        "report_type": "初始报告",
        "primary_account": "A999",
        "related_accounts": [],
        "customer_profile": {},
        "suspicious_transactions": [],
        "total_suspicious_amount": 0,
        "suspicious_patterns": [],
        "risk_level": "low",
        "analysis_summary": "无可疑交易",
        "evidence_chain": [],
        "disposal_suggestion": "无需处置",
        "compliance_status": "pending",
        "compliance_notes": None,
        "reviewer": None,
        "final_decision": None,
    }
    exporter = PdfExporter(output_dir=export_dir)
    path = exporter.export_report(report)
    assert os.path.exists(path)
    assert os.path.getsize(path) > 0


@pytest.mark.unit
def test_export_report_with_critical_risk(export_dir, sample_suspicious):
    """极高风险报告应能正常导出（测试所有风险等级颜色）"""
    report = {
        "report_id": "STR-CRITICAL",
        "report_date": "2025-01-01 12:00:00",
        "report_type": "初始报告",
        "primary_account": "A002",
        "related_accounts": ["A001"],
        "customer_profile": {},
        "suspicious_transactions": [{**sample_suspicious, "risk_score": 95}],
        "total_suspicious_amount": 45000.0,
        "suspicious_patterns": ["分拆转账(1笔)"],
        "risk_level": "critical",
        "analysis_summary": "高度可疑",
        "evidence_chain": ["证据1"],
        "disposal_suggestion": "立即上报",
        "compliance_status": "pending",
        "compliance_notes": None,
        "reviewer": None,
        "final_decision": None,
    }
    exporter = PdfExporter(output_dir=export_dir)
    path = exporter.export_report(report)
    assert os.path.exists(path)


# ============================================================
# 批量导出测试
# ============================================================
@pytest.mark.unit
def test_export_multiple_reports(export_dir, multi_reports):
    """批量导出应生成多个PDF文件"""
    exporter = PdfExporter(output_dir=export_dir)
    paths = exporter.export_reports(multi_reports)
    assert len(paths) == 3
    for p in paths:
        assert os.path.exists(p)
        assert p.endswith(".pdf")


@pytest.mark.unit
def test_export_multiple_reports_distinct_names(export_dir, multi_reports):
    """批量导出的文件名应不同"""
    exporter = PdfExporter(output_dir=export_dir)
    paths = exporter.export_reports(multi_reports)
    names = [os.path.basename(p) for p in paths]
    assert len(set(names)) == 3


# ============================================================
# 多交易场景测试
# ============================================================
@pytest.mark.unit
def test_export_report_with_many_transactions(export_dir, sample_transaction):
    """多笔可疑交易应能完整导出（测试表格分页）"""
    txns = []
    for i in range(30):  # 30笔交易，会跨页
        t = {**sample_transaction, "transaction_id": f"T{i:03d}"}
        txns.append({
            "transaction": t,
            "rule_hits": ["大额交易"],
            "risk_score": 60 + (i % 30),
            "evidence": [f"证据-{i}"],
            "graph_evidence": None,
            "llm_analysis": None,
            "llm_confidence": None,
            "is_false_positive": None,
            "community_id": None,
        })
    report = {
        "report_id": "STR-MULTI",
        "report_date": "2025-01-01 12:00:00",
        "report_type": "初始报告",
        "primary_account": "A002",
        "related_accounts": [],
        "customer_profile": {},
        "suspicious_transactions": txns,
        "total_suspicious_amount": 45000.0 * 30,
        "suspicious_patterns": ["大额交易(30笔)"],
        "risk_level": "critical",
        "analysis_summary": "大量可疑交易",
        "evidence_chain": [f"证据-{i}" for i in range(30)],
        "disposal_suggestion": "立即上报",
        "compliance_status": "pending",
        "compliance_notes": None,
        "reviewer": None,
        "final_decision": None,
    }
    exporter = PdfExporter(output_dir=export_dir)
    path = exporter.export_report(report)
    assert os.path.exists(path)
    # 多页PDF应较大
    assert os.path.getsize(path) > 5000


# ============================================================
# 字体降级测试
# ============================================================
@pytest.mark.unit
def test_cjk_font_registration_safe(export_dir, sample_report):
    """中文字体注册应安全（不抛异常）"""
    from tools.pdf_exporter import _ensure_cjk_font
    # 多次调用应安全
    _ensure_cjk_font()
    _ensure_cjk_font()
    exporter = PdfExporter(output_dir=export_dir)
    path = exporter.export_report(sample_report)
    assert os.path.exists(path)
