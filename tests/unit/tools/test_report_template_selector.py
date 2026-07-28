"""
智能报告模板选择器测试 (B3-2)

覆盖:
- 5 种模板正确选择
- 混合案件回退 mixed
- 无匹配回退 default
- 各模板渲染包含特定章节
- 模板版本号附在末尾（戒律 M4）
- 渲染失败回退 default（戒律 P4）
- 交易明细表/证据链/资金流向正确填充
"""
import pytest

from tools.report_template_selector import (
    ReportTemplateSelector,
    select_and_render,
    TEMPLATE_VERSION,
)


# ============================================================
# 测试夹具
# ============================================================
@pytest.fixture
def selector():
    return ReportTemplateSelector()


@pytest.fixture
def base_context():
    """基础渲染上下文"""
    return {
        "report_id": "STR-001",
        "report_date": "2026-07-27",
        "primary_account": "A1",
        "related_accounts": ["A2", "A3"],
        "suspicious_transactions": [
            {"transaction": {"transaction_id": "T001", "from_account": "A1",
                             "to_account": "A2", "amount": 45000,
                             "timestamp": "2026-07-27 10:00:00",
                             "remark": "货款"},
             "rule_hits": ["分拆转账"], "risk_score": 75,
             "evidence": ["小额多笔", "时间集中"]},
            {"transaction": {"transaction_id": "T002", "from_account": "A2",
                             "to_account": "A3", "amount": 46000,
                             "timestamp": "2026-07-27 10:30:00",
                             "remark": "货款"},
             "rule_hits": ["分拆转账"], "risk_score": 72,
             "evidence": ["金额接近阈值"]},
        ],
        "total_suspicious_amount": 91000,
        "suspicious_patterns": ["分拆转账(2笔)"],
        "evidence_chain": ["小额多笔", "时间集中", "金额接近阈值"],
        "risk_level": "high",
        "disposal_suggestion": "建议冻结账户并上报",
    }


# ============================================================
# 模板选择测试
# ============================================================
def test_select_smurfing_template(selector):
    """分拆转账 → smurfing 模板"""
    rule_hits = [{"rule_hits": ["分拆转账", "分拆转账"]}]
    assert selector.select_template(rule_hits=rule_hits) == "smurfing"


def test_select_round_trip_template(selector):
    """对敲交易 → round_trip 模板"""
    rule_hits = [{"rule_hits": ["对敲交易"]}]
    assert selector.select_template(rule_hits=rule_hits) == "round_trip"


def test_select_crypto_template(selector):
    """虚拟货币 → crypto 模板"""
    rule_hits = [{"rule_hits": ["虚拟货币OTC", "混币器特征"]}]
    assert selector.select_template(rule_hits=rule_hits) == "crypto"


def test_select_cross_border_template(selector):
    """跨境交易 → cross_border 模板"""
    rule_hits = [{"rule_hits": ["跨境交易", "频繁跨境"]}]
    assert selector.select_template(rule_hits=rule_hits) == "cross_border"


def test_select_mixed_template(selector):
    """多模式 → mixed 模板"""
    rule_hits = [{"rule_hits": ["分拆转账", "对敲交易"]}]
    assert selector.select_template(rule_hits=rule_hits) == "mixed"


def test_select_default_template_no_hits(selector):
    """无命中 → default 模板"""
    assert selector.select_template(rule_hits=[]) == "default"
    assert selector.select_template(rule_hits=None) == "default"


def test_select_default_template_no_match(selector):
    """规则名不匹配任何模板 → default"""
    rule_hits = [{"rule_hits": ["未知规则"]}]
    assert selector.select_template(rule_hits=rule_hits) == "default"


def test_select_template_from_suspicious_transactions(selector):
    """从 suspicious_transactions 提取规则"""
    suspicious = [
        {"rule_hits": ["分拆转账"]},
        {"rule_hits": ["分拆转账"]},
    ]
    assert selector.select_template(suspicious_transactions=suspicious) == "smurfing"


def test_select_template_string_rule_hits(selector):
    """rule_hits 为字符串列表"""
    rule_hits = ["对敲交易", "对敲"]
    assert selector.select_template(rule_hits=rule_hits) == "round_trip"


def test_select_template_crypto_by_keyword(selector):
    """虚拟货币关键词匹配"""
    rule_hits = [{"rule_hits": ["场外OTC模式"]}]
    assert selector.select_template(rule_hits=rule_hits) == "crypto"


# ============================================================
# 模板渲染测试
# ============================================================
def test_render_smurfing_contains_specific_section(selector, base_context):
    """smurfing 模板包含分拆特征章节"""
    content = selector.render("smurfing", base_context)
    assert "分拆转账特征分析" in content
    assert "小额多笔" in content


def test_render_round_trip_contains_specific_section(selector, base_context):
    """round_trip 模板包含对敲特征章节"""
    content = selector.render("round_trip", base_context)
    assert "对敲交易特征分析" in content
    assert "资金回流" in content


def test_render_crypto_contains_specific_section(selector, base_context):
    """crypto 模板包含虚拟货币章节"""
    content = selector.render("crypto", base_context)
    assert "虚拟货币交易特征分析" in content
    assert "OTC" in content


def test_render_cross_border_contains_specific_section(selector, base_context):
    """cross_border 模板包含跨境章节"""
    content = selector.render("cross_border", base_context)
    assert "跨境交易特征分析" in content
    assert "地理风险" in content


def test_render_mixed_contains_specific_section(selector, base_context):
    """mixed 模板包含混合模式章节"""
    content = selector.render("mixed", base_context)
    assert "混合可疑模式分析" in content


def test_render_default_contains_basic_section(selector, base_context):
    """default 模板包含基础章节"""
    content = selector.render("default", base_context)
    assert "可疑交易模式分析" in content


# ============================================================
# 公共内容测试
# ============================================================
def test_render_contains_report_id(selector, base_context):
    """所有模板包含报告ID"""
    for tpl in ["smurfing", "round_trip", "crypto", "cross_border", "mixed", "default"]:
        content = selector.render(tpl, base_context)
        assert "STR-001" in content, f"模板 {tpl} 缺少报告ID"


def test_render_contains_primary_account(selector, base_context):
    """所有模板包含主涉案账户"""
    content = selector.render("default", base_context)
    assert "A1" in content


def test_render_contains_transaction_table(selector, base_context):
    """包含交易明细表"""
    content = selector.render("default", base_context)
    assert "交易ID" in content
    assert "T001" in content
    assert "付款方" in content


def test_render_contains_evidence_chain(selector, base_context):
    """包含证据链"""
    content = selector.render("default", base_context)
    assert "完整证据链" in content
    assert "小额多笔" in content


def test_render_contains_disposal_suggestion(selector, base_context):
    """包含处置建议"""
    content = selector.render("default", base_context)
    assert "处置建议" in content
    assert "建议冻结账户并上报" in content


def test_render_contains_template_metadata(selector, base_context):
    """包含模板版本号（戒律 M4）"""
    content = selector.render("smurfing", base_context)
    assert "模板类型: smurfing" in content
    assert f"模板版本: {TEMPLATE_VERSION}" in content


def test_render_contains_risk_level(selector, base_context):
    """包含风险等级"""
    content = selector.render("default", base_context)
    assert "high" in content


def test_render_contains_total_amount(selector, base_context):
    """包含可疑交易总金额"""
    content = selector.render("default", base_context)
    assert "91,000" in content


# ============================================================
# 异常处理测试（戒律 P4）
# ============================================================
def test_render_unknown_template_falls_back_default(selector, base_context):
    """未知模板回退 default"""
    content = selector.render("nonexistent", base_context)
    assert "可疑交易模式分析" in content
    assert "模板类型: default" in content


def test_render_empty_context_does_not_raise(selector):
    """空上下文不抛异常"""
    content = selector.render("default", {})
    assert isinstance(content, str)
    assert len(content) > 0


def test_select_template_does_not_raise_on_invalid_input(selector):
    """非法输入不抛异常"""
    assert selector.select_template(rule_hits="not a list") == "default"
    assert selector.select_template(rule_hits=[None, 123]) == "default"


def test_render_handles_missing_transactions(selector):
    """缺少 suspicious_transactions 不抛异常"""
    ctx = {"report_id": "STR-001"}
    content = selector.render("default", ctx)
    assert "暂无可疑交易" in content or "STR-001" in content


# ============================================================
# 便捷函数测试
# ============================================================
def test_select_and_render_returns_tuple(selector, base_context):
    """便捷函数返回 (template_name, content)"""
    template_name, content = select_and_render(
        rule_hits=[{"rule_hits": ["分拆转账"]}],
        context=base_context,
    )
    assert template_name == "smurfing"
    assert "分拆转账特征分析" in content


def test_select_and_render_auto_fills_transactions():
    """便捷函数自动补充 suspicious_transactions"""
    suspicious = [
        {"transaction": {"transaction_id": "T1", "amount": 45000},
         "rule_hits": ["分拆转账"], "risk_score": 75},
    ]
    template_name, content = select_and_render(
        suspicious_transactions=suspicious,
        context={"report_id": "STR-001"},
    )
    assert template_name == "smurfing"
    assert "T1" in content


def test_select_and_render_handles_exception():
    """便捷函数异常时返回 default"""
    template_name, content = select_and_render(
        rule_hits=None,
        context=None,
    )
    assert template_name == "default"


# ============================================================
# 模板特定内容测试
# ============================================================
def test_smurfing_template_contains_metrics(selector, base_context):
    """smurfing 模板包含分拆量化指标"""
    content = selector.render("smurfing", base_context)
    assert "分拆模式量化指标" in content
    assert "平均金额" in content


def test_crypto_template_platform_association(selector):
    """crypto 模板检测平台关联"""
    ctx = {
        "report_id": "STR-002",
        "suspicious_transactions": [
            {"transaction": {"transaction_id": "T1", "from_account": "A",
                             "to_account": "B", "amount": 50000,
                             "remark": "binance收款"},
             "rule_hits": ["虚拟货币"], "risk_score": 80},
        ],
    }
    content = selector.render("crypto", ctx)
    assert "关联平台" in content
    assert "binance" in content.lower()


def test_cross_border_template_geo_risk(selector):
    """cross_border 模板包含地理风险"""
    ctx = {
        "report_id": "STR-003",
        "suspicious_transactions": [
            {"transaction": {"transaction_id": "T1", "from_account": "A",
                             "to_account": "B", "amount": 100000,
                             "country": "AE"},
             "rule_hits": ["跨境交易"], "risk_score": 85},
        ],
    }
    content = selector.render("cross_border", ctx)
    assert "跨境地理风险" in content
    assert "AE" in content


def test_mixed_template_pattern_breakdown(selector):
    """mixed 模板包含模式命中统计"""
    ctx = {
        "report_id": "STR-004",
        "suspicious_patterns": ["分拆转账(3笔)", "对敲交易(2笔)"],
        "suspicious_transactions": [],
    }
    content = selector.render("mixed", ctx)
    assert "各模式命中情况" in content
    assert "分拆转账(3笔)" in content
