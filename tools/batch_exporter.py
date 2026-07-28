"""
批量报告导出 + 汇总表

将多份 STR 报告一次性导出为 Excel + PDF，并生成 CSV 汇总表与 ZIP 打包。

戒律:
- M1: 汇总表数据全部来自真实报告字段，不编造
- P1: 不遗漏任何报告（每份报告都进入汇总）
- M4: 汇总表与单报告对应关系可追溯

输出结构:
    exports/batch_<timestamp>/
        ├── summary.csv         # 汇总表（一行一份报告）
        ├── <report_id>.xlsx    # 各报告的Excel
        ├── <report_id>.pdf     # 各报告的PDF
        └── batch_<timestamp>.zip  # 全部打包
"""
import os
import csv
import zipfile
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

from graph.state import STRReport
from tools.excel_exporter import ExcelExporter
from tools.pdf_exporter import PdfExporter


# 汇总表字段
SUMMARY_HEADERS = [
    "序号",
    "报告编号",
    "报告日期",
    "主涉案账户",
    "风险等级",
    "可疑交易数",
    "可疑交易总金额(元)",
    "可疑模式",
    "合规状态",
    "Excel文件",
    "PDF文件",
]


def _risk_cn(level: str) -> str:
    """风险等级转中文"""
    return {
        "critical": "极高",
        "high": "高",
        "medium": "中",
        "low": "低",
    }.get(level, level)


def _compliance_cn(status: str) -> str:
    """合规状态转中文"""
    return {
        "pending": "待审核",
        "passed": "审核通过",
        "rejected": "已驳回",
    }.get(status, status)


class BatchExporter:
    """批量报告导出器"""

    def __init__(self, output_dir: str = None):
        """
        Args:
            output_dir: 输出根目录，None时使用 EXPORTS_DIR
        """
        if output_dir is None:
            from config import EXPORTS_DIR
            output_dir = EXPORTS_DIR
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def export(
        self,
        reports: List[STRReport],
        batch_name: str = None,
        formats: List[str] = None,
        create_zip: bool = True,
        create_summary: bool = True,
    ) -> Dict[str, Any]:
        """
        批量导出报告

        Args:
            reports: STR 报告列表
            batch_name: 批次名称，None时按时间戳生成
            formats: 导出格式列表，支持 ["excel", "pdf"]，None时两者都导出
            create_zip: 是否创建ZIP打包
            create_summary: 是否生成CSV汇总表

        Returns:
            导出结果字典:
            - batch_dir: 批次目录
            - files: 所有文件路径列表
            - summary_path: 汇总表路径（如生成）
            - zip_path: ZIP路径（如生成）
            - report_count: 报告数
        """
        if not reports:
            return {
                "batch_dir": None,
                "files": [],
                "summary_path": None,
                "zip_path": None,
                "report_count": 0,
            }

        if formats is None:
            formats = ["excel", "pdf"]

        # 批次目录
        if batch_name is None:
            batch_name = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        batch_dir = os.path.join(self.output_dir, batch_name)
        os.makedirs(batch_dir, exist_ok=True)

        all_files: List[str] = []

        # 导出各格式（戒律 P4: 单报告失败不影响其他报告）
        for fmt in formats:
            if fmt == "excel":
                exporter = ExcelExporter(output_dir=batch_dir)
                for report in reports:
                    try:
                        path = exporter.export_report(report)
                        all_files.append(path)
                    except Exception as e:
                        print(f"  [批量导出] Excel 报告 {report.get('report_id', '?')} 失败: {e}")
            elif fmt == "pdf":
                exporter = PdfExporter(output_dir=batch_dir)
                for report in reports:
                    try:
                        path = exporter.export_report(report)
                        all_files.append(path)
                    except Exception as e:
                        print(f"  [批量导出] PDF 报告 {report.get('report_id', '?')} 失败: {e}")

        # 生成汇总表
        summary_path = None
        if create_summary:
            summary_path = self._write_summary_csv(reports, batch_dir, all_files, formats)
            all_files.append(summary_path)

        # 创建ZIP打包
        zip_path = None
        if create_zip:
            zip_path = os.path.join(self.output_dir, f"{batch_name}.zip")
            self._create_zip(all_files, zip_path)

        return {
            "batch_dir": batch_dir,
            "files": all_files,
            "summary_path": summary_path,
            "zip_path": zip_path,
            "report_count": len(reports),
        }

    def _write_summary_csv(
        self,
        reports: List[STRReport],
        batch_dir: str,
        all_files: List[str],
        formats: List[str],
    ) -> str:
        """生成汇总CSV"""
        summary_path = os.path.join(batch_dir, "summary.csv")

        # 构造 report_id -> 文件路径 映射
        file_map: Dict[str, Dict[str, str]] = {}
        for f in all_files:
            basename = os.path.basename(f)
            for report in reports:
                rid = report.get("report_id", "")
                if basename.startswith(rid):
                    if f.endswith(".xlsx"):
                        file_map.setdefault(rid, {})["excel"] = basename
                    elif f.endswith(".pdf"):
                        file_map.setdefault(rid, {})["pdf"] = basename

        with open(summary_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(SUMMARY_HEADERS)

            for i, report in enumerate(reports, start=1):
                rid = report.get("report_id", "")
                files = file_map.get(rid, {})
                excel_name = files.get("excel", "") if "excel" in formats else ""
                pdf_name = files.get("pdf", "") if "pdf" in formats else ""

                row = [
                    i,
                    rid,
                    report.get("report_date", ""),
                    report.get("primary_account", ""),
                    _risk_cn(report.get("risk_level", "")),
                    len(report.get("suspicious_transactions", [])),
                    f"{report.get('total_suspicious_amount', 0):,.2f}",
                    "、".join(report.get("suspicious_patterns", [])),
                    _compliance_cn(report.get("compliance_status", "pending")),
                    excel_name,
                    pdf_name,
                ]
                writer.writerow(row)

        return summary_path

    def _create_zip(self, files: List[str], zip_path: str):
        """创建ZIP打包"""
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                if os.path.exists(f):
                    # 在ZIP中使用文件名（避免嵌套目录）
                    arcname = os.path.basename(f)
                    zf.write(f, arcname)
