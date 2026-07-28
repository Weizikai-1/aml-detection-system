"""
STR 报告 PDF 导出工具

将可疑交易报告导出为专业格式的 PDF 文件，便于打印和正式归档。

戒律:
- M1: 所有数据来自真实报告字段，不编造
- M2: 每笔可疑交易附带规则命中和证据
- M3: 风险评分0-100
- M4: 证据链完整保留

PDF 结构:
1. 标题区 - 报告ID、日期、风险等级
2. 报告概要 - 主涉案账户、关联账户、可疑交易数等
3. 可疑交易明细表 - 每笔交易的详细信息
4. 证据链 - 编号列表
5. 模式与处置 - 可疑模式、分析摘要、处置建议
"""
import os
from typing import List, Optional
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether,
)

from graph.state import STRReport


# ============================================================
# 中文字体注册（仅注册一次）
# ============================================================
_FONT_REGISTERED = False

def _ensure_cjk_font():
    """注册中文字体，保证全局只注册一次"""
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        _FONT_REGISTERED = True
    except Exception as e:
        # 降级：使用默认字体（中文可能显示为方块，但不会崩溃）
        # 戒律 M4: 字体注册失败时记录 WARN 日志，便于追溯
        print(f"  [WARN] PDF 中文字体注册失败，降级为默认字体: {e}")


# ============================================================
# 颜色定义
# ============================================================
COLOR_PRIMARY = colors.HexColor("#305496")
COLOR_HEADER_BG = colors.HexColor("#305496")
COLOR_HEADER_TEXT = colors.white
COLOR_ROW_ALT = colors.HexColor("#F2F2F2")
COLOR_BORDER = colors.HexColor("#BFBFBF")

RISK_COLORS = {
    "critical": colors.HexColor("#FFC7CE"),
    "high":     colors.HexColor("#FFEB9C"),
    "medium":   colors.HexColor("#FFF2CC"),
    "low":      colors.HexColor("#C6EFCE"),
}
RISK_TEXT_COLORS = {
    "critical": colors.HexColor("#9C0006"),
    "high":     colors.HexColor("#9C6500"),
    "medium":   colors.HexColor("#7F6000"),
    "low":      colors.HexColor("#006100"),
}


def _risk_cn(level: str) -> str:
    """风险等级转中文"""
    return {
        "critical": "极高",
        "high": "高",
        "medium": "中",
        "low": "低",
    }.get(level, level)


# ============================================================
# 样式定义
# ============================================================
def _build_styles():
    """构建段落样式（使用中文字体）"""
    _ensure_cjk_font()
    font_name = "STSong-Light" if _FONT_REGISTERED else "Helvetica"

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "CjkTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=18,
        leading=24,
        alignment=TA_CENTER,
        textColor=COLOR_PRIMARY,
        spaceAfter=12,
    )
    h2_style = ParagraphStyle(
        "CjkH2",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=13,
        leading=18,
        textColor=COLOR_PRIMARY,
        spaceBefore=12,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "CjkBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=10,
        leading=15,
        alignment=TA_JUSTIFY,
        spaceAfter=4,
    )
    cell_style = ParagraphStyle(
        "CjkCell",
        fontName=font_name,
        fontSize=9,
        leading=12,
    )
    cell_bold = ParagraphStyle(
        "CjkCellBold",
        fontName=font_name,
        fontSize=9,
        leading=12,
        textColor=colors.white,
    )
    return {
        "title": title_style,
        "h2": h2_style,
        "body": body_style,
        "cell": cell_style,
        "cell_bold": cell_bold,
        "font_name": font_name,
    }


# ============================================================
# 页眉页脚
# ============================================================
def _make_on_page(report_id: str):
    """生成页脚回调函数（带页码和报告ID）"""
    def on_page(canvas, doc):
        canvas.saveState()
        _ensure_cjk_font()
        font_name = "STSong-Light" if _FONT_REGISTERED else "Helvetica"
        # 页脚
        canvas.setFont(font_name, 8)
        canvas.setFillColor(colors.grey)
        # 左侧：报告ID
        canvas.drawString(2 * cm, 1 * cm, f"报告编号: {report_id}")
        # 右侧：页码
        page_num = canvas.getPageNumber()
        canvas.drawRightString(A4[0] - 2 * cm, 1 * cm, f"第 {page_num} 页")
        # 底部线
        canvas.setStrokeColor(COLOR_BORDER)
        canvas.line(2 * cm, 1.3 * cm, A4[0] - 2 * cm, 1.3 * cm)
        canvas.restoreState()
    return on_page


# ============================================================
# 各部分构建
# ============================================================
def _build_title_section(report: STRReport, styles: dict) -> list:
    """标题区"""
    elements = []
    title = Paragraph("可疑交易报告 (STR)", styles["title"])
    elements.append(title)

    # 副标题：报告ID + 日期
    subtitle_text = (
        f"报告编号: {report.get('report_id', '')}　|　"
        f"报告日期: {report.get('report_date', '')}　|　"
        f"报告类型: {report.get('report_type', '')}"
    )
    subtitle = Paragraph(subtitle_text, styles["body"])
    elements.append(subtitle)

    # 风险等级条
    risk = report.get("risk_level", "low")
    risk_color = RISK_COLORS.get(risk, colors.white)
    risk_text_color = RISK_TEXT_COLORS.get(risk, colors.black)
    risk_table = Table(
        [[Paragraph(f"<b>风险等级: {_risk_cn(risk)}</b>", styles["body"])]],
        colWidths=[16 * cm],
    )
    risk_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), risk_color),
        ("TEXTCOLOR", (0, 0), (-1, -1), risk_text_color),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BOX", (0, 0), (-1, -1), 1, COLOR_BORDER),
    ]))
    elements.append(Spacer(1, 6))
    elements.append(risk_table)
    elements.append(Spacer(1, 12))
    return elements


def _build_summary_section(report: STRReport, styles: dict) -> list:
    """报告概要"""
    elements = [Paragraph("一、报告概要", styles["h2"])]

    related = report.get("related_accounts", [])
    related_str = "、".join(related) if related else "无"

    profile = report.get("customer_profile", {})
    profile_str = ""
    if profile:
        parts = []
        if profile.get("account_type"):
            parts.append(f"账户类型: {profile['account_type']}")
        if profile.get("risk_rating"):
            parts.append(f"风险评级: {_risk_cn(profile['risk_rating'])}")
        if profile.get("monitoring_status"):
            parts.append(f"监控状态: {profile['monitoring_status']}")
        profile_str = "；".join(parts)

    rows = [
        ["主涉案账户", report.get("primary_account", "")],
        ["关联账户", related_str],
        ["可疑交易数", f"{len(report.get('suspicious_transactions', []))} 笔"],
        ["可疑交易总金额", f"{report.get('total_suspicious_amount', 0):,.2f} 元"],
        ["可疑模式", "、".join(report.get("suspicious_patterns", [])) or "无"],
        ["客户画像", profile_str or "无"],
    ]

    font_name = styles["font_name"]
    table_data = []
    for label, value in rows:
        table_data.append([
            Paragraph(f"<b>{label}</b>", styles["cell"]),
            Paragraph(str(value), styles["cell"]),
        ])

    t = Table(table_data, colWidths=[4 * cm, 12 * cm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), COLOR_ROW_ALT),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, COLOR_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 12))
    return elements


def _build_transactions_section(report: STRReport, styles: dict) -> list:
    """可疑交易明细"""
    elements = [Paragraph("二、可疑交易明细", styles["h2"])]

    txns = report.get("suspicious_transactions", [])
    if not txns:
        elements.append(Paragraph("无可疑交易。", styles["body"]))
        return elements

    # 表头
    headers = ["序号", "交易ID", "付款账户", "收款账户", "金额(元)", "时间", "命中规则", "风险分"]
    header_row = [Paragraph(f"<b>{h}</b>", styles["cell_bold"]) for h in headers]

    table_data = [header_row]
    for i, s in enumerate(txns, start=1):
        t = s.get("transaction", {})
        rule_hits = "、".join(s.get("rule_hits", []))
        risk_score = s.get("risk_score", 0)
        row = [
            Paragraph(str(i), styles["cell"]),
            Paragraph(t.get("transaction_id", ""), styles["cell"]),
            Paragraph(t.get("from_account", ""), styles["cell"]),
            Paragraph(t.get("to_account", ""), styles["cell"]),
            Paragraph(f"{float(t.get('amount', 0)):,.2f}", styles["cell"]),
            Paragraph(t.get("timestamp", ""), styles["cell"]),
            Paragraph(rule_hits, styles["cell"]),
            Paragraph(str(risk_score), styles["cell"]),
        ]
        table_data.append(row)

    # 列宽（总宽16cm）
    col_widths = [1 * cm, 2.2 * cm, 2 * cm, 2 * cm, 2.2 * cm, 2.5 * cm, 2.5 * cm, 1.6 * cm]

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("FONTNAME", (0, 0), (-1, -1), styles["font_name"]),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), COLOR_HEADER_TEXT),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, COLOR_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    # 隔行底色
    for i in range(1, len(table_data)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), COLOR_ROW_ALT))
    # 风险分单元格着色
    for i, s in enumerate(txns, start=1):
        risk_score = s.get("risk_score", 0)
        risk = "critical" if risk_score >= 85 else "high" if risk_score >= 70 else "medium" if risk_score >= 50 else "low"
        style_cmds.append(("BACKGROUND", (7, i), (7, i), RISK_COLORS[risk]))
        style_cmds.append(("TEXTCOLOR", (7, i), (7, i), RISK_TEXT_COLORS[risk]))

    t.setStyle(TableStyle(style_cmds))
    elements.append(t)
    elements.append(Spacer(1, 12))
    return elements


def _build_evidence_section(report: STRReport, styles: dict) -> list:
    """证据链"""
    elements = [Paragraph("三、证据链（完整可追溯）", styles["h2"])]

    evidence_chain = report.get("evidence_chain", [])
    if not evidence_chain:
        elements.append(Paragraph("无证据记录。", styles["body"]))
        return elements

    # 构造证据-交易映射
    txns = report.get("suspicious_transactions", [])
    evidence_txn_map = {}
    for s in txns:
        t_id = s.get("transaction", {}).get("transaction_id", "")
        for ev in s.get("evidence", []):
            evidence_txn_map.setdefault(ev, []).append(t_id)

    for i, ev in enumerate(evidence_chain, start=1):
        related = evidence_txn_map.get(ev, [])
        related_str = "、".join(related[:3])
        if len(related) > 3:
            related_str += f" 等{len(related)}笔"
        text = f"<b>{i}.</b> {ev}"
        if related_str:
            text += f" <font size=8 color='#666666'>(关联: {related_str})</font>"
        elements.append(Paragraph(text, styles["body"]))

    elements.append(Spacer(1, 12))
    return elements


def _build_patterns_section(report: STRReport, styles: dict) -> list:
    """模式与处置"""
    elements = [Paragraph("四、可疑模式分析与处置建议", styles["h2"])]

    # 可疑模式
    elements.append(Paragraph("<b>可疑模式:</b>", styles["body"]))
    patterns = report.get("suspicious_patterns", [])
    if patterns:
        for p in patterns:
            elements.append(Paragraph(f"• {p}", styles["body"]))
    else:
        elements.append(Paragraph("无", styles["body"]))

    elements.append(Spacer(1, 6))

    # 分析摘要
    elements.append(Paragraph("<b>分析摘要:</b>", styles["body"]))
    elements.append(Paragraph(report.get("analysis_summary", "无"), styles["body"]))

    elements.append(Spacer(1, 6))

    # 处置建议
    elements.append(Paragraph("<b>处置建议:</b>", styles["body"]))
    elements.append(Paragraph(report.get("disposal_suggestion", "无"), styles["body"]))

    elements.append(Spacer(1, 6))

    # 合规状态
    elements.append(Paragraph("<b>合规状态:</b>", styles["body"]))
    compliance = report.get("compliance_status", "pending")
    compliance_text = {
        "pending": "待审核",
        "passed": "审核通过",
        "rejected": "已驳回",
    }.get(compliance, compliance)
    elements.append(Paragraph(compliance_text, styles["body"]))
    if report.get("compliance_notes"):
        elements.append(Paragraph(f"备注: {report['compliance_notes']}", styles["body"]))

    return elements


# ============================================================
# 导出器类
# ============================================================
class PdfExporter:
    """STR 报告 PDF 导出器"""

    def __init__(self, output_dir: str = None):
        """
        Args:
            output_dir: 默认输出目录，None时使用 EXPORTS_DIR
        """
        if output_dir is None:
            from config import EXPORTS_DIR
            output_dir = EXPORTS_DIR
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def export_report(
        self,
        report: STRReport,
        output_path: str = None,
    ) -> str:
        """
        导出单个报告为 PDF 文件

        Args:
            report: STR 报告
            output_path: 输出路径，None时自动生成

        Returns:
            实际保存路径
        """
        if output_path is None:
            report_id = report.get("report_id", "STR-UNKNOWN")
            output_path = os.path.join(self.output_dir, f"{report_id}.pdf")

        # 注册中文字体
        _ensure_cjk_font()
        styles = _build_styles()

        # 创建文档
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=2 * cm,
            rightMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
            title=f"STR报告 - {report.get('report_id', '')}",
            author="反洗钱多Agent系统",
        )

        # 构建内容
        elements = []
        elements.extend(_build_title_section(report, styles))
        elements.extend(_build_summary_section(report, styles))
        elements.extend(_build_transactions_section(report, styles))
        elements.extend(_build_evidence_section(report, styles))
        elements.extend(_build_patterns_section(report, styles))

        # 生成PDF（带页脚）
        report_id = report.get("report_id", "")
        doc.build(elements, onFirstPage=_make_on_page(report_id), onLaterPages=_make_on_page(report_id))

        return output_path

    def export_reports(
        self,
        reports: List[STRReport],
        output_dir: str = None,
    ) -> List[str]:
        """
        批量导出多个报告

        Args:
            reports: STR 报告列表
            output_dir: 输出目录，None时使用默认目录

        Returns:
            保存路径列表
        """
        out_dir = output_dir or self.output_dir
        os.makedirs(out_dir, exist_ok=True)

        # 戒律 P4: 单报告失败不影响其他报告（错误隔离）
        paths = []
        for report in reports:
            report_id = report.get("report_id", f"STR-{datetime.now().strftime('%Y%m%d%H%M%S')}")
            path = os.path.join(out_dir, f"{report_id}.pdf")
            try:
                self.export_report(report, path)
                paths.append(path)
            except Exception as e:
                print(f"  [PDF批量导出] 报告 {report_id} 失败: {e}")
        return paths
