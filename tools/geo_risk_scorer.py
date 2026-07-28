"""
地理风险因子评分模块（B1-2）

职责: 基于交易的国家/地区信息计算地理风险评分(0-100)

严格遵守戒律:
- M1: 国家分级基于公开FATF/OFAC名单，不臆测
- M3: 评分范围 0-100
- P2: T4 一般国家不加分，避免误报
- M4: 评分理由可追溯，记录命中的分级与加成项

评分模型:
    geo_risk_score = base_country_score      (按国家分级 T0-T4)
                   + transit_country_bonus   (经停制裁国家)
                   + multi_high_risk_bonus   (多个高风险地区汇聚)
                   + structuring_geo_bonus   (跨境分拆地理特征)

国家分级:
- T0 制裁国家 (OFAC全面制裁): 命中即 90 分
- T1 FATF黑名单: 80 分
- T2 FATF灰名单: 65 分
- T3 重点关注(避税天堂/离岸): 50 分
- T4 一般国家: 0 分

使用方式:
    from tools.geo_risk_scorer import geo_risk_scorer
    score, reasons = geo_risk_scorer.score_transaction(txn)
"""
from typing import Dict, List, Tuple, Optional, Any


# ============================================================
# 国家/地区分级表（基于公开监管名单，戒律 M1: 真实可验证）
# ============================================================

# T0: OFAC 全面制裁国家（命中即 90 分）
# 来源: https://ofac.treasury.gov/sanctions-programs-and-country-information
T0_SANCTIONED_COUNTRIES = {
    "KP": "朝鲜 (DPRK) - OFAC全面制裁",
    "IR": "伊朗 - OFAC全面制裁",
    "SY": "叙利亚 - OFAC全面制裁",
    "CU": "古巴 - OFAC全面制裁",
    "VE": "委内瑞拉 - 部分全面制裁",
}

# T1: FATF 黑名单（呼吁采取行动的高风险管辖区）
# 来源: https://www.fatf-gafi.org/en/topics/high-risk-and-other-monitored-jurisdictions.html
T1_FATF_BLACKLIST = {
    "MM": "缅甸 - FATF黑名单",
    "AF": "阿富汗 - FATF黑名单",
}

# T2: FATF 灰名单（加强监控的管辖区）
# 注: FATF 灰名单动态调整，此处基于近期公开名单
T2_FATF_GREYLIST = {
    "AL": "阿尔巴尼亚 - FATF灰名单",
    "BB": "巴巴多斯 - FATF灰名单",
    "BF": "布基纳法索 - FATF灰名单",
    "BG": "保加利亚 - FATF灰名单",
    "CF": "中非共和国 - FATF灰名单",
    "KH": "柬埔寨 - FATF灰名单",
    "CD": "刚果(金) - FATF灰名单",
    "HT": "海地 - FATF灰名单",
    "JM": "牙买加 - FATF灰名单",
    "KE": "肯尼亚 - FATF灰名单",
    "ML": "马里 - FATF灰名单",
    "NI": "尼加拉瓜 - FATF灰名单",
    "YE": "也门 - FATF灰名单",
    "UG": "乌干达 - FATF灰名单",
    "AE": "阿联酋 - FATF灰名单(历史)",
    "ZA": "南非 - FATF灰名单(历史)",
    "SN": "塞内加尔 - FATF灰名单",
    "TZ": "坦桑尼亚 - FATF灰名单",
}

# T3: 重点关注地区（避税天堂/离岸金融中心）
# 来源: OECD 税收天堂名单 + 国际反洗钱监管实践
T3_HIGH_RISK_OFFSHORE = {
    "PA": "巴拿马 - 避税天堂",
    "KY": "开曼群岛 - 离岸金融中心",
    "VG": "英属维京群岛 - 离岸金融中心",
    "BVI": "英属维京群岛 - 离岸金融中心",
    "BS": "巴哈马 - 避税天堂",
    "BZ": "伯利兹 - 避税天堂",
    "DM": "多米尼克 - 避税天堂",
    "GD": "格林纳达 - 避税天堂",
    "VC": "圣文森特 - 避税天堂",
    "KN": "圣基茨 - 避税天堂",
    "LC": "圣卢西亚 - 避税天堂",
    "AD": "安道尔 - 避税天堂",
    "MC": "摩纳哥 - 避税天堂",
    "LI": "列支敦士登 - 避税天堂",
    "SM": "圣马力诺 - 避税天堂",
    "SC": "塞舌尔 - 避税天堂",
    "MU": "毛里求斯 - 离岸金融中心",
    "BN": "文莱 - 避税天堂",
    "NR": "瑙鲁 - 避税天堂",
    "TV": "图瓦卢 - 避税天堂",
    "CK": "库克群岛 - 避税天堂",
    "MH": "马绍尔群岛 - 避税天堂",
}

# T4: 主要正常国家（白名单，0 分）
# 此处不一一列举，未列入 T0-T3 的视为 T4


# ============================================================
# 评分配置（戒律: 配置外部化，便于热更新）
# ============================================================

GEO_RISK_CONFIG = {
    # 基础分（按分级）
    "T0_base_score": 90,             # OFAC 制裁国家
    "T1_base_score": 80,             # FATF 黑名单
    "T2_base_score": 65,             # FATF 灰名单
    "T3_base_score": 50,             # 重点关注/避税天堂
    "T4_base_score": 0,              # 一般国家
    # 加成项（戒律 M3: 总分钳制 0-100）
    "transit_country_bonus": 10,     # 经停制裁国家加成
    "multi_high_risk_bonus": 15,     # 多个高风险地区汇聚加成
    "structuring_geo_bonus": 20,     # 跨境分拆地理特征加成
    "multi_high_risk_min_count": 2,  # 触发多风险汇聚的最小地区数
    "structuring_min_amount": 200000,  # 跨境分拆总额阈值
    "structuring_min_count": 3,      # 跨境分拆最小笔数
}


class GeoRiskScorer:
    """
    地理风险因子评分器

    单笔交易评分:
        score_transaction(txn) -> (score, reasons)

    批量评分:
        score_transactions(txns) -> txns (含 geo_risk_score 字段)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or GEO_RISK_CONFIG

    def _classify_country(self, country_code: str) -> Tuple[str, int, str]:
        """
        国家分级（戒律 M1: 基于公开名单）

        Returns:
            (tier, base_score, description)
            tier: "T0"/"T1"/"T2"/"T3"/"T4"
        """
        if not country_code:
            return ("T4", 0, "未知国家(默认T4)")

        code = country_code.upper().strip()
        if code in T0_SANCTIONED_COUNTRIES:
            return ("T0", self.config["T0_base_score"], T0_SANCTIONED_COUNTRIES[code])
        if code in T1_FATF_BLACKLIST:
            return ("T1", self.config["T1_base_score"], T1_FATF_BLACKLIST[code])
        if code in T2_FATF_GREYLIST:
            return ("T2", self.config["T2_base_score"], T2_FATF_GREYLIST[code])
        if code in T3_HIGH_RISK_OFFSHORE:
            return ("T3", self.config["T3_base_score"], T3_HIGH_RISK_OFFSHORE[code])
        # CN 视为本国，0分
        if code == "CN":
            return ("T4", 0, "中国(本国)")
        return ("T4", 0, f"一般国家({code})")

    def score_transaction(
        self,
        txn: Dict[str, Any],
        account_history: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> Tuple[int, List[str]]:
        """
        计算单笔交易的地理风险分（戒律 M3: 0-100）

        Args:
            txn: 交易字典，含 counterparty_country / currency / amount / from_account / to_account
            account_history: 该账户的历史跨境交易（用于检测分拆）{account_id: [txns]}

        Returns:
            (score, reasons)  reasons 为加成理由列表（M4: 证据可追溯）
        """
        reasons: List[str] = []
        score = 0

        # 1. 基础分: 取付款方/收款方/交易对手国家中最高分级
        counterparty_country = str(txn.get("counterparty_country", "")).upper().strip()
        currency = str(txn.get("currency", "CNY")).upper().strip()

        # 收集所有相关国家代码
        countries_to_check = []
        if counterparty_country:
            countries_to_check.append(counterparty_country)

        # 如果没有 counterparty_country，但有非CNY货币，按货币推断地区
        if not countries_to_check and currency not in ("CNY", "RMB", ""):
            # 戒律 M1: 仅作为推断依据，不臆测具体国家，给一般跨境分
            # 戒律 P2: 10分基础分不会触发告警阈值(60)，仅作风险标记
            score = max(score, 10)
            reasons.append(f"非本币交易(货币={currency})，疑似跨境，基础分10")
            # 没有具体国家信息，无法应用后续加成，直接返回
            return (score, reasons)

        if not countries_to_check:
            # 完全没有地理信息，返回 0 分（戒律 P2: 不误报）
            return (0, [])

        # 取所有相关国家的最高分级
        max_tier = "T4"
        max_base_score = 0
        max_country_desc = ""
        for country in countries_to_check:
            tier, base_score, desc = self._classify_country(country)
            if base_score > max_base_score:
                max_base_score = base_score
                max_tier = tier
                max_country_desc = desc

        score = max(score, max_base_score)
        if max_base_score > 0:
            reasons.append(f"基础分{max_base_score}({max_tier}: {max_country_desc})")

        # 2. 经停制裁国家加成
        # 如果交易对手国家是T0制裁国家，本身就是90分，不再加成
        # 此处加成针对：付款方/收款方/中间方任一涉及T0但交易对手非T0的场景
        # 简化：如果同时有多个国家字段(未来扩展)，任一为T0则加成
        # 当前实现: 如果交易对手国家是T0，已经90分；如果是其他T级，看是否有transit_country字段
        transit_country = str(txn.get("transit_country", "")).upper().strip()
        if transit_country:
            t_tier, t_score, t_desc = self._classify_country(transit_country)
            if t_tier == "T0" and max_tier != "T0":
                bonus = self.config["transit_country_bonus"]
                score = min(100, score + bonus)
                reasons.append(f"经停制裁国家加成+{bonus}({t_desc})")

        # 3. 多个高风险地区汇聚加成（需要账户历史）
        if account_history:
            from_acc = txn.get("from_account", "")
            to_acc = txn.get("to_account", "")
            for acc in [from_acc, to_acc]:
                if not acc:
                    continue
                history = account_history.get(acc, [])
                if len(history) < self.config["multi_high_risk_min_count"]:
                    continue
                # 统计该账户历史涉及的不同高风险地区(T0-T3)
                high_risk_countries = set()
                for h_txn in history:
                    h_country = str(h_txn.get("counterparty_country", "")).upper().strip()
                    if not h_country:
                        continue
                    tier, _, _ = self._classify_country(h_country)
                    if tier in ("T0", "T1", "T2", "T3"):
                        high_risk_countries.add(h_country)
                # 加上当前交易的国家
                if counterparty_country:
                    tier, _, _ = self._classify_country(counterparty_country)
                    if tier in ("T0", "T1", "T2", "T3"):
                        high_risk_countries.add(counterparty_country)
                if len(high_risk_countries) >= self.config["multi_high_risk_min_count"]:
                    bonus = self.config["multi_high_risk_bonus"]
                    score = min(100, score + bonus)
                    reasons.append(
                        f"多高风险地区汇聚加成+{bonus}"
                        f"(账户{acc}涉及{len(high_risk_countries)}个高风险地区: "
                        f"{', '.join(sorted(high_risk_countries))})"
                    )
                    break  # 一个账户触发即可

        # 4. 跨境分拆地理特征加成（需要账户历史）
        if account_history and counterparty_country:
            from_acc = txn.get("from_account", "")
            to_acc = txn.get("to_account", "")
            for acc in [from_acc, to_acc]:
                if not acc:
                    continue
                history = account_history.get(acc, [])
                # 筛选同对手国家的交易
                same_country_txns = [
                    h for h in history
                    if str(h.get("counterparty_country", "")).upper().strip() == counterparty_country
                ]
                if len(same_country_txns) < self.config["structuring_min_count"]:
                    continue
                total_amount = sum(h.get("amount", 0) for h in same_country_txns)
                if total_amount >= self.config["structuring_min_amount"]:
                    bonus = self.config["structuring_geo_bonus"]
                    score = min(100, score + bonus)
                    reasons.append(
                        f"跨境分拆地理特征加成+{bonus}"
                        f"(账户{acc}与{counterparty_country}发生"
                        f"{len(same_country_txns)}笔交易，总额{total_amount:,.0f}元)"
                    )
                    break

        # 戒律 M3: 钳制 0-100
        score = max(0, min(100, score))
        return (score, reasons)

    def score_transactions(
        self,
        transactions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        批量评分（戒律 M1: 基于真实交易字段，不编造地理信息）

        Args:
            transactions: 交易列表

        Returns:
            交易列表（每笔交易新增 geo_risk_score 和 geo_risk_reasons 字段）
        """
        # 先按账户聚合历史跨境交易（用于多风险汇聚和分拆检测）
        account_history: Dict[str, List[Dict[str, Any]]] = {}
        for txn in transactions:
            for acc in [txn.get("from_account", ""), txn.get("to_account", "")]:
                if not acc:
                    continue
                if acc not in account_history:
                    account_history[acc] = []
                account_history[acc].append(txn)

        for txn in transactions:
            score, reasons = self.score_transaction(txn, account_history)
            txn["geo_risk_score"] = score
            txn["geo_risk_reasons"] = reasons

        return transactions

    def get_country_tier(self, country_code: str) -> str:
        """查询国家分级（供其他模块使用）"""
        tier, _, _ = self._classify_country(country_code)
        return tier


# 全局单例（供规则引擎直接调用）
geo_risk_scorer = GeoRiskScorer()
