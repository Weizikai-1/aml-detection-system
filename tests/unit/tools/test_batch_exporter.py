"""
批量报告导出测试 (Task 7-3)

覆盖:
- 批量导出（Excel+PDF+CSV汇总+ZIP打包）
- 汇总表内容完整性
- ZIP打包完整性
- 边界情况（空列表、单报告）
- 戒律M1/P1: 数据完整性、不遗漏
"""
import os
import csv
import zipfile
from typing import List

import pytest

from tools.batch_exporter import BatchExporter, SUMMARY_HEADERS
from graph.state import STRReport, SuspiciousTransaction, Transaction


# ============================================================
# 夹具
# ============================================================
@pytest.fixture()
def export_dir(tmp_path):
    d = tmp_path / "batch_exports"
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
        "remark": "",
    }


@pytest.fixture()
def sample_suspicious(sample_transaction) -> SuspiciousTransaction:
    return {
        "transaction": sample_transaction,
        "rule_hits": ["分拆转账"],
        "risk_score": 75,
        "evidence": ["分拆转账: 1小时内5笔"],
        "graph_evidence": None,
        "llm_analysis": None,
        "llm_confidence": None,
        "is_false_positive": None,
        "community_id": None,
    }


@pytest.fixture()
def sample_reports(sample_suspicious) -> List[STRReport]:
    """3份报告样本"""
    reports = []
    for i in range(3):
        r = {
            "report_id": f"STR-20250101-BATCH{i:04d}",
            "report_date": "2025-01-01 12:00:00",
            "report_type": "初始报告",
            "primary_account": f"A{i:03d}",
            "related_accounts": [],
            "customer_profile": {},
            "suspicious_transactions": [sample_suspicious],
            "total_suspicious_amount": 45000.0,
            "suspicious_patterns": ["分拆转账(1笔)"],
            "risk_level": "high" if i % 2 == 0 else "medium",
            "analysis_summary": f"账户A{i:03d}可疑",
            "evidence_chain": ["分拆转账: 1小时内5笔"],
            "disposal_suggestion": "持续观察",
            "compliance_status": "pending",
            "compliance_notes": None,
            "reviewer": None,
            "final_decision": None,
        }
        reports.append(r)
    return reports


# ============================================================
# 批量导出测试
# ============================================================
@pytest.mark.unit
def test_batch_export_creates_all_files(export_dir, sample_reports):
    """批量导出应生成Excel+PDF+CSV+ZIP"""
    exporter = BatchExporter(output_dir=export_dir)
    result = exporter.export(sample_reports, batch_name="test_batch")

    assert result["report_count"] == 3
    assert result["batch_dir"] is not None
    assert os.path.exists(result["batch_dir"])
    # 应包含3个xlsx + 3个pdf + 1个csv = 7个文件
    assert len(result["files"]) == 7
    for f in result["files"]:
        assert os.path.exists(f)


@pytest.mark.unit
def test_batch_export_creates_zip(export_dir, sample_reports):
    """应创建ZIP打包"""
    exporter = BatchExporter(output_dir=export_dir)
    result = exporter.export(sample_reports, batch_name="test_zip")

    assert result["zip_path"] is not None
    assert os.path.exists(result["zip_path"])
    # ZIP应包含所有文件
    with zipfile.ZipFile(result["zip_path"], "r") as zf:
        names = zf.namelist()
        # 3 xlsx + 3 pdf + 1 csv = 7
        assert len(names) == 7


@pytest.mark.unit
def test_batch_export_summary_csv_content(export_dir, sample_reports):
    """汇总CSV应包含所有报告的完整信息"""
    exporter = BatchExporter(output_dir=export_dir)
    result = exporter.export(sample_reports, batch_name="test_summary")

    summary_path = result["summary_path"]
    assert summary_path is not None
    assert os.path.exists(summary_path)

    with open(summary_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    # 表头 + 3行数据 = 4行
    assert len(rows) == 4
    # 表头
    assert rows[0] == SUMMARY_HEADERS
    # 数据行 - 检查每个报告的ID都在
    report_ids = [row[1] for row in rows[1:]]
    for report in sample_reports:
        assert report["report_id"] in report_ids


@pytest.mark.unit
def test_batch_export_summary_contains_correct_fields(export_dir, sample_reports):
    """汇总表每行应包含正确的字段值"""
    exporter = BatchExporter(output_dir=export_dir)
    result = exporter.export(sample_reports, batch_name="test_fields")

    with open(result["summary_path"], "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 3
    # 检查第一行字段
    first = rows[0]
    assert first["报告编号"] == "STR-20250101-BATCH0000"
    assert first["主涉案账户"] == "A000"
    assert first["风险等级"] == "高"  # i=0 是 high
    assert first["可疑交易数"] == "1"
    assert first["可疑交易总金额(元)"] == "45,000.00"
    # Excel和PDF文件名应填充
    assert first["Excel文件"].endswith(".xlsx")
    assert first["PDF文件"].endswith(".pdf")


@pytest.mark.unit
def test_batch_export_only_excel(export_dir, sample_reports):
    """只导出Excel格式"""
    exporter = BatchExporter(output_dir=export_dir)
    result = exporter.export(sample_reports, formats=["excel"], batch_name="excel_only")

    # 3 xlsx + 1 csv = 4
    assert len(result["files"]) == 4
    # 不应有PDF
    for f in result["files"]:
        assert not f.endswith(".pdf")


@pytest.mark.unit
def test_batch_export_only_pdf(export_dir, sample_reports):
    """只导出PDF格式"""
    exporter = BatchExporter(output_dir=export_dir)
    result = exporter.export(sample_reports, formats=["pdf"], batch_name="pdf_only")

    # 3 pdf + 1 csv = 4
    assert len(result["files"]) == 4
    for f in result["files"]:
        assert not f.endswith(".xlsx") or f.endswith(".csv") == False


@pytest.mark.unit
def test_batch_export_no_zip(export_dir, sample_reports):
    """不创建ZIP时不应有zip_path"""
    exporter = BatchExporter(output_dir=export_dir)
    result = exporter.export(sample_reports, create_zip=False, batch_name="no_zip")

    assert result["zip_path"] is None


@pytest.mark.unit
def test_batch_export_no_summary(export_dir, sample_reports):
    """不生成汇总表时不应有summary_path"""
    exporter = BatchExporter(output_dir=export_dir)
    result = exporter.export(sample_reports, create_summary=False, batch_name="no_summary")

    assert result["summary_path"] is None


# ============================================================
# 边界情况
# ============================================================
@pytest.mark.unit
def test_batch_export_empty_reports(export_dir):
    """空报告列表应返回空结果"""
    exporter = BatchExporter(output_dir=export_dir)
    result = exporter.export([])

    assert result["report_count"] == 0
    assert result["files"] == []
    assert result["batch_dir"] is None


@pytest.mark.unit
def test_batch_export_single_report(export_dir, sample_reports):
    """单份报告也应正常导出"""
    exporter = BatchExporter(output_dir=export_dir)
    result = exporter.export([sample_reports[0]], batch_name="single")

    assert result["report_count"] == 1
    assert len(result["files"]) == 3  # 1 xlsx + 1 pdf + 1 csv


@pytest.mark.unit
def test_batch_export_auto_batch_name(export_dir, sample_reports):
    """不指定batch_name时应自动生成时间戳名称"""
    exporter = BatchExporter(output_dir=export_dir)
    result = exporter.export(sample_reports)

    assert result["batch_dir"] is not None
    batch_dir_name = os.path.basename(result["batch_dir"])
    assert batch_dir_name.startswith("batch_")


# ============================================================
# 数据完整性测试（戒律 M1/P1）
# ============================================================
@pytest.mark.unit
def test_batch_export_no_report_lost(export_dir, sample_reports):
    """每份报告都应出现在汇总表中（戒律 P1: 不遗漏）"""
    exporter = BatchExporter(output_dir=export_dir)
    result = exporter.export(sample_reports, batch_name="completeness")

    with open(result["summary_path"], "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # 每份报告都应在
    summary_ids = {row["报告编号"] for row in rows}
    original_ids = {r["report_id"] for r in sample_reports}
    assert summary_ids == original_ids


@pytest.mark.unit
def test_batch_export_files_match_reports(export_dir, sample_reports):
    """每份报告都应有对应的Excel和PDF文件"""
    exporter = BatchExporter(output_dir=export_dir)
    result = exporter.export(sample_reports, batch_name="file_match")

    batch_dir = result["batch_dir"]
    for report in sample_reports:
        rid = report["report_id"]
        excel_path = os.path.join(batch_dir, f"{rid}.xlsx")
        pdf_path = os.path.join(batch_dir, f"{rid}.pdf")
        assert os.path.exists(excel_path), f"Excel文件缺失: {rid}"
        assert os.path.exists(pdf_path), f"PDF文件缺失: {rid}"
