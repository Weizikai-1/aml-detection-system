"""
智能报告模板自适应 (Report Template Selector)

职责:
- 按案件类型选择专用模板（分拆/对敲/虚拟货币/跨境/混合/默认）
- 每种模板突出对应案件类型的证据链
- 模板变量自动填充

设计原则:
- M1: 模板选择基于真实规则命中，不编造
- M2: 每种模板突出对应案件类型的证据链
- M4: 报告附模板版本号
- P4: 渲染失败回退到 default 模板
"""
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

# 模块版本（戒律 M4: 可追溯）
__REPORT_TEMPLATE_SELECTOR_VERSION__ = "1.0.0"

# 模板版本
TEMPLATE_VERSION = "1.0.0"


# 规则关键词到模板的映射（戒律 M1: 基于真实规则名）
_RULE_TEMPLATE_MAP = {
    # 分拆转账模板
    "smurfing": {"分拆转账", "smurfing", "分拆"},
    # 对敲交易模板
    "round_trip": {"对敲交易", "对敲", "round_trip", "资金回流"},
    # 虚拟货币模板
    "crypto": {"虚拟货币", "场外OTC", "混币器", "OTC", "crypto_pattern",
               "法币兑换", "平台关联"},
    # 跨境交易模板
    "cross_border": {"跨境交易", "跨境分拆", "频繁跨境", "cross_border",
                     "高风险地区", "制裁国家"},
}


class ReportTemplateSelector:
    """
    智能报告模板选择器

    主入口:
        select_template(rule_hits, suspicious_transactions) -> str
        render(template_name, context) -> str

    戒律遵守:
    - M1: 模板选择基于真实规则命中
    - M2: 每种模板突出对应案件类型的证据链
    - M4: 报告附模板版本号
    - P4: 渲染失败回退 default
    """

    # 支持的模板列表
    SUPPORTED_TEMPLATES = ["smurfing", "round_trip", "crypto", "cross_border",
                           "mixed", "default"]

    def __init__(self):
        pass

    # ============================================================
    # 模板选择
    # ============================================================
    def select_template(
        self,
        rule_hits: List[Any] = None,
        suspicious_transactions: List[Dict[str, Any]] = None,
    ) -> str:
        """
        根据规则命中选择模板

        Args:
            rule_hits: 规则命中列表（SuspiciousTransaction 或字符串列表）
            suspicious_transactions: 可疑交易列表（备选数据源）

        Returns:
            模板名: smurfing / round_trip / crypto / cross_border / mixed / default

        选择逻辑:
        1. 收集所有命中的规则名
        2. 按模板映射统计各模板命中数
        3. 若仅命中单一模板类型 → 该模板
        4. 若命中多个模板类型 → mixed
        5. 无命中 → default
        """
        try:
            rule_names = self._collect_rule_names(rule_hits, suspicious_transactions)
            if not rule_names:
                return "default"

            # 统计各模板命中情况
            template_hits = {}
            for template_name, keywords in _RULE_TEMPLATE_MAP.items():
                count = sum(
                    1 for r in rule_names
                    if any(kw in r for kw in keywords)
                )
                if count > 0:
                    template_hits[template_name] = count

            if not template_hits:
                return "default"

            if len(template_hits) == 1:
                return list(template_hits.keys())[0]

            # 多模板命中 → mixed
            return "mixed"
        except Exception as e:
            print(f"  [模板选择器] 选择失败，回退 default: {e}")
            return "default"

    # ============================================================
    # 模板渲染
    # ============================================================
    def render(self, template_name: str, context: Dict[str, Any]) -> str:
        """
        渲染指定模板

        Args:
            template_name: 模板名
            context: 渲染上下文，应包含:
                - report_id: 报告ID
                - report_date: 报告日期
                - primary_account: 主涉案账户
                - related_accounts: 关联账户列表
                - suspicious_transactions: 可疑交易列表
                - total_suspicious_amount: 可疑交易总金额
                - suspicious_patterns: 可疑模式描述
                - evidence_chain: 证据链
                - risk_level: 风险等级
                - disposal_suggestion: 处置建议

        Returns:
            渲染后的 Markdown 字符串
        """
        try:
            if template_name not in self.SUPPORTED_TEMPLATES:
                template_name = "default"

            renderer = getattr(self, f"_render_{template_name}", None)
            if renderer is None:
                return self._render_default(context)

            content = renderer(context)
            # 戒律 M4: 附模板版本号
            return self._append_template_metadata(content, template_name)
        except Exception as e:
            print(f"  [模板选择器] 渲染失败，回退 default: {e}")
            try:
                return self._append_template_metadata(
                    self._render_default(context), "default"
                )
            except Exception:
                return ""

    # ============================================================
    # 各模板实现
    # ============================================================
    def _render_smurfing(self, ctx: Dict[str, Any]) -> str:
        """分拆转账模板（强调小额多笔特征）"""
        return self._base_template(ctx, """
## 分拆转账特征分析

本案件呈现典型分拆转账（Smurfing）特征，具体表现：

1. **小额多笔**: 涉案交易金额均处于规避申报阈值区间
2. **时间集中**: 多笔交易在短时间内集中发生
3. **账户分散**: 涉及多个收款账户，疑似化整为零

### 分拆模式量化指标

{smurfing_metrics}

### 资金流向路径

{fund_flow}

> ⚠️ 分拆转账是反洗钱核心监管对象，需重点关注资金最终去向。
""".strip())

    def _render_round_trip(self, ctx: Dict[str, Any]) -> str:
        """对敲交易模板（强调资金回流路径）"""
        return self._base_template(ctx, """
## 对敲交易特征分析

本案件呈现典型对敲交易（Round Trip）特征，具体表现：

1. **资金回流**: 资金经过多账户流转后回到起点
2. **金额接近**: 往返交易金额高度匹配
3. **时间间隔**: 资金回流周期符合典型对敲模式

### 资金回流路径

{round_trip_path}

### 往返金额对比

{amount_comparison}

> ⚠️ 对敲交易常用于虚增交易量、资金洗白，需追踪完整回流链路。
""".strip())

    def _render_crypto(self, ctx: Dict[str, Any]) -> str:
        """虚拟货币模板（强调OTC/混币器/平台关联）"""
        return self._base_template(ctx, """
## 虚拟货币交易特征分析

本案件呈现虚拟货币相关可疑交易特征，具体表现：

1. **OTC场外模式**: 多对一汇聚→中心账户→一对多分发
2. **混币器特征**: 多笔小额进账 + 单笔大额出账
3. **平台关联**: 涉及已知虚拟货币交易平台
4. **法币兑换**: 备注含换U/买币/卖币等关键词

### 虚拟货币交易模式

{crypto_pattern}

### 关联平台/地址

{platform_association}

> ⚠️ 虚拟货币交易是当前反洗钱重点监管领域，需关注链上资金流向。
""".strip())

    def _render_cross_border(self, ctx: Dict[str, Any]) -> str:
        """跨境交易模板（强调地理风险/制裁国家）"""
        return self._base_template(ctx, """
## 跨境交易特征分析

本案件呈现跨境交易可疑特征，具体表现：

1. **高频跨境**: 短时间内多次跨境资金往来
2. **地理风险**: 涉及高风险/制裁国家地区
3. **分拆跨境**: 大额资金分拆为多笔跨境转移
4. **换汇特征**: 疑似通过跨境交易实现币种转换

### 跨境地理风险

{geo_risk}

### 跨境资金流向

{cross_border_flow}

> ⚠️ 跨境交易需结合OFAC制裁名单与FATF高风险国家名单核查。
""".strip())

    def _render_mixed(self, ctx: Dict[str, Any]) -> str:
        """混合模式模板（综合）"""
        return self._base_template(ctx, """
## 混合可疑模式分析

本案件呈现多种可疑交易模式叠加特征，复杂度较高：

1. **多模式并存**: 同时存在分拆、对敲、跨境等多种可疑模式
2. **资金链路复杂**: 资金流向涉及多种洗钱手法组合
3. **风险叠加**: 多维度风险因素累积，整体风险等级高

### 各模式命中情况

{pattern_breakdown}

### 综合资金流向

{fund_flow}

> ⚠️ 混合模式案件需逐项分析各子模式证据，避免遗漏关键线索。
""".strip())

    def _render_default(self, ctx: Dict[str, Any]) -> str:
        """默认模板"""
        return self._base_template(ctx, """
## 可疑交易模式分析

本案件命中以下可疑交易规则：

### 命中规则统计

{pattern_breakdown}

### 资金流向

{fund_flow}

> 建议结合具体规则证据进行深度分析。
""".strip())

    # ============================================================
    # 内部：基础模板框架
    # ============================================================
    def _base_template(self, ctx: Dict[str, Any], specific_section: str) -> str:
        """基础模板框架（公共部分）"""
        report_id = ctx.get("report_id", "STR-UNKNOWN")
        report_date = ctx.get("report_date", "")
        primary_account = ctx.get("primary_account", "")
        related_accounts = ctx.get("related_accounts", []) or []
        suspicious_transactions = ctx.get("suspicious_transactions", []) or []
        total_amount = ctx.get("total_suspicious_amount", 0)
        patterns = ctx.get("suspicious_patterns", []) or []
        evidence_chain = ctx.get("evidence_chain", []) or []
        risk_level = ctx.get("risk_level", "medium")
        disposal = ctx.get("disposal_suggestion", "建议进一步调查")

        # 填充特定章节的占位符
        specific_filled = specific_section.format(
            smurfing_metrics=self._build_smurfing_metrics(suspicious_transactions),
            fund_flow=self._build_fund_flow(suspicious_transactions),
            round_trip_path=self._build_round_trip_path(suspicious_transactions),
            amount_comparison=self._build_amount_comparison(suspicious_transactions),
            crypto_pattern=self._build_crypto_pattern(suspicious_transactions),
            platform_association=self._build_platform_association(suspicious_transactions),
            geo_risk=self._build_geo_risk(suspicious_transactions),
            cross_border_flow=self._build_cross_border_flow(suspicious_transactions),
            pattern_breakdown=self._build_pattern_breakdown(patterns),
        )

        # 交易明细表
        txn_table = self._build_transaction_table(suspicious_transactions)

        # 证据链
        evidence_text = "\n".join(
            f"- {e}" for e in evidence_chain
        ) if evidence_chain else "- 暂无证据链"

        return f"""# 可疑交易报告 (STR)

## 报告基本信息

| 项目 | 内容 |
|------|------|
| 报告编号 | {report_id} |
| 报告日期 | {report_date} |
| 主涉案账户 | {primary_account} |
| 关联账户 | {', '.join(str(a) for a in related_accounts) if related_accounts else '无'} |
| 可疑交易笔数 | {len(suspicious_transactions)} |
| 可疑交易总金额 | ¥{total_amount:,.2f} |
| 风险等级 | {risk_level} |

{specific_section if False else specific_filled}

## 可疑交易明细

{txn_table}

## 完整证据链

{evidence_text}

## 处置建议

{disposal}

---
*本报告由反洗钱多Agent系统自动生成，附模板版本号（见报告末尾）*
"""

    # ============================================================
    # 内部：占位符填充
    # ============================================================
    def _build_smurfing_metrics(self, txns: List[Dict[str, Any]]) -> str:
        """构建分拆指标"""
        if not txns:
            return "暂无数据"
        amounts = []
        for s in txns:
            t = s.get("transaction") or {}
            if isinstance(t, dict):
                amt = t.get("amount", 0)
                try:
                    amounts.append(float(amt))
                except (TypeError, ValueError):
                    pass
        if not amounts:
            return "暂无数据"
        return (
            f"- 交易笔数: {len(amounts)}\n"
            f"- 平均金额: ¥{sum(amounts)/len(amounts):,.2f}\n"
            f"- 最大金额: ¥{max(amounts):,.2f}\n"
            f"- 最小金额: ¥{min(amounts):,.2f}"
        )

    def _build_fund_flow(self, txns: List[Dict[str, Any]]) -> str:
        """构建资金流向"""
        if not txns:
            return "暂无数据"
        lines = []
        for s in txns[:10]:  # 最多展示10笔
            t = s.get("transaction") or {}
            if isinstance(t, dict):
                src = t.get("from_account", "?")
                dst = t.get("to_account", "?")
                amt = t.get("amount", 0)
                try:
                    amt = float(amt)
                except (TypeError, ValueError):
                    amt = 0
                lines.append(f"- {src} → {dst} : ¥{amt:,.2f}")
        return "\n".join(lines) if lines else "暂无数据"

    def _build_round_trip_path(self, txns: List[Dict[str, Any]]) -> str:
        """构建对敲路径"""
        if not txns:
            return "暂无数据"
        accounts = []
        for s in txns:
            t = s.get("transaction") or {}
            if isinstance(t, dict):
                src = t.get("from_account")
                dst = t.get("to_account")
                if src and src not in accounts:
                    accounts.append(src)
                if dst and dst not in accounts:
                    accounts.append(dst)
        if not accounts:
            return "暂无数据"
        return " → ".join(accounts)

    def _build_amount_comparison(self, txns: List[Dict[str, Any]]) -> str:
        """构建金额对比"""
        if not txns:
            return "暂无数据"
        amounts = []
        for s in txns:
            t = s.get("transaction") or {}
            if isinstance(t, dict):
                amt = t.get("amount", 0)
                try:
                    amounts.append(float(amt))
                except (TypeError, ValueError):
                    pass
        if not amounts:
            return "暂无数据"
        return (
            f"- 最大金额: ¥{max(amounts):,.2f}\n"
            f"- 最小金额: ¥{min(amounts):,.2f}\n"
            f"- 差异比例: {(max(amounts)-min(amounts))/max(amounts)*100:.1f}%"
        )

    def _build_crypto_pattern(self, txns: List[Dict[str, Any]]) -> str:
        """构建虚拟货币模式"""
        if not txns:
            return "暂无数据"
        # 统计备注中的虚拟货币关键词
        crypto_keywords = ["换u", "收u", "出u", "买币", "卖币", "otc", "btc", "usdt"]
        hits = []
        for s in txns:
            t = s.get("transaction") or {}
            if isinstance(t, dict):
                remark = str(t.get("remark", "")).lower()
                for kw in crypto_keywords:
                    if kw in remark:
                        hits.append(kw)
        if not hits:
            return "未检测到明显虚拟货币关键词"
        counter = Counter(hits)
        return "\n".join(f"- {k}: {v}次" for k, v in counter.most_common())

    def _build_platform_association(self, txns: List[Dict[str, Any]]) -> str:
        """构建平台关联"""
        platforms = ["binance", "huobi", "okex", "okx", "coinbase", "kraken",
                     "gate.io", "bybit", "kucoin", "bitfinex", "抹茶", "芝麻开门"]
        hits = []
        for s in txns:
            t = s.get("transaction") or {}
            if isinstance(t, dict):
                remark = str(t.get("remark", "")).lower()
                for p in platforms:
                    if p in remark:
                        hits.append(p)
        if not hits:
            return "未检测到已知平台关联"
        counter = Counter(hits)
        return "\n".join(f"- {k}: {v}次" for k, v in counter.most_common())

    def _build_geo_risk(self, txns: List[Dict[str, Any]]) -> str:
        """构建地理风险"""
        # 从交易中提取国家/地区信息
        regions = []
        for s in txns:
            t = s.get("transaction") or {}
            if isinstance(t, dict):
                country = t.get("country") or t.get("region") or t.get("to_country")
                if country:
                    regions.append(str(country))
        if not regions:
            return "暂无地理风险信息（未检测到跨境交易）"
        counter = Counter(regions)
        return "\n".join(f"- {k}: {v}次" for k, v in counter.most_common())

    def _build_cross_border_flow(self, txns: List[Dict[str, Any]]) -> str:
        """构建跨境资金流向"""
        return self._build_fund_flow(txns)

    def _build_pattern_breakdown(self, patterns: List[str]) -> str:
        """构建模式命中统计"""
        if not patterns:
            return "暂无可疑模式"
        return "\n".join(f"- {p}" for p in patterns)

    def _build_transaction_table(self, txns: List[Dict[str, Any]]) -> str:
        """构建交易明细表"""
        if not txns:
            return "暂无可疑交易"
        lines = [
            "| 交易ID | 付款方 | 收款方 | 金额 | 时间 | 命中规则 | 风险分 |",
            "|--------|--------|--------|------|------|----------|--------|",
        ]
        for s in txns[:20]:  # 最多20笔
            t = s.get("transaction") or {}
            if not isinstance(t, dict):
                continue
            tid = t.get("transaction_id", "?")
            src = t.get("from_account", "?")
            dst = t.get("to_account", "?")
            amt = t.get("amount", 0)
            try:
                amt = f"¥{float(amt):,.2f}"
            except (TypeError, ValueError):
                amt = str(amt)
            ts = t.get("timestamp", "?")
            rules = ", ".join(s.get("rule_hits", []) or [])
            risk = s.get("risk_score", 0)
            try:
                risk = f"{float(risk):.1f}"
            except (TypeError, ValueError):
                risk = str(risk)
            lines.append(f"| {tid} | {src} | {dst} | {amt} | {ts} | {rules} | {risk} |")
        return "\n".join(lines)

    # ============================================================
    # 内部：辅助
    # ============================================================
    def _collect_rule_names(
        self,
        rule_hits: List[Any] = None,
        suspicious_transactions: List[Dict[str, Any]] = None,
    ) -> List[str]:
        """收集所有命中的规则名"""
        rule_names = []
        try:
            # 从 rule_hits 收集
            if rule_hits:
                for r in rule_hits:
                    if isinstance(r, str):
                        rule_names.append(r)
                    elif isinstance(r, dict):
                        # SuspiciousTransaction 结构
                        rules = r.get("rule_hits", []) or []
                        rule_names.extend(rules)
            # 从 suspicious_transactions 收集
            if suspicious_transactions:
                for s in suspicious_transactions:
                    if isinstance(s, dict):
                        rules = s.get("rule_hits", []) or []
                        rule_names.extend(rules)
        except Exception:
            pass
        return rule_names

    def _append_template_metadata(self, content: str, template_name: str) -> str:
        """追加模板元数据（戒律 M4: 可追溯）"""
        metadata = (
            f"\n\n---\n"
            f"*模板类型: {template_name} | 模板版本: {TEMPLATE_VERSION} | "
            f"选择器版本: {__REPORT_TEMPLATE_SELECTOR_VERSION__}*"
        )
        return content + metadata


# ============================================================
# 模块级便捷函数
# ============================================================
def select_and_render(
    rule_hits: List[Any] = None,
    suspicious_transactions: List[Dict[str, Any]] = None,
    context: Dict[str, Any] = None,
) -> Tuple[str, str]:
    """
    便捷函数: 选择模板并渲染

    Args:
        rule_hits: 规则命中列表
        suspicious_transactions: 可疑交易列表
        context: 渲染上下文

    Returns:
        (template_name, rendered_content)
    """
    try:
        selector = ReportTemplateSelector()
        template_name = selector.select_template(rule_hits, suspicious_transactions)
        ctx = context or {}
        # 若 context 缺少 suspicious_transactions，自动补充
        if "suspicious_transactions" not in ctx and suspicious_transactions:
            ctx["suspicious_transactions"] = suspicious_transactions
        content = selector.render(template_name, ctx)
        return template_name, content
    except Exception as e:
        print(f"  [模板选择器] 便捷渲染失败: {e}")
        return "default", ""
