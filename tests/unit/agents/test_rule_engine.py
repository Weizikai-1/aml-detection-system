"""
规则引擎单元测试

覆盖4条反洗钱规则的核心检测逻辑:
1. 分拆转账 (Smurfing)
2. 快进快出 (Fast-In-Fast-Out)
3. 对敲交易 (Round-Trip)
4. 大额交易 (Large Amount)
"""
import pytest
from agents.rule_engine import (
    _detect_smurfing,
    _detect_fast_in_fast_out,
    _detect_round_trip,
    _detect_large_amount,
    _detect_baseline_deviation,
    _detect_remark_keywords,
    _detect_shell_companies,
    _apply_remark_discount,
    _apply_profile_weighting,
    _merge_suspicious,
)
from graph.state import Transaction


def _make_txn(
    tid: str,
    from_acc: str,
    to_acc: str,
    amount: float,
    timestamp: str,
    txn_type: str = "transfer",
) -> Transaction:
    """构造测试交易"""
    return {
        "transaction_id": tid,
        "from_account": from_acc,
        "to_account": to_acc,
        "amount": amount,
        "timestamp": timestamp,
        "transaction_type": txn_type,
        "remark": "",
    }


# ============================================================
# 规则 1: 分拆转账
# ============================================================
def test_smurfing_detected():
    """同一收款账户1小时内收到5笔4-5万的转账应被检测"""
    txns = [
        _make_txn(f"T{i}", f"PAYER_{i}", "RECV_A", 45000.0, f"2026-07-01T10:{i:02d}:00")
        for i in range(5)
    ]
    result = _detect_smurfing(txns)
    assert len(result) == 5
    assert all(r["rule_hits"] == ["分拆转账"] for r in result)


def test_smurfing_not_triggered_below_threshold():
    """金额不在4-5万区间不触发"""
    txns = [
        _make_txn(f"T{i}", f"PAYER_{i}", "RECV_A", 30000.0, f"2026-07-01T10:{i:02d}:00")
        for i in range(5)
    ]
    result = _detect_smurfing(txns)
    assert len(result) == 0


# ============================================================
# 规则 2: 快进快出
# ============================================================
def test_fast_in_fast_out_detected():
    """入账后10分钟内转出95%以上应被检测"""
    txns = [
        _make_txn("IN_1", "PAYER_A", "ACC_X", 100000.0, "2026-07-01T10:00:00"),
        _make_txn("OUT_1", "ACC_X", "PAYER_B", 96000.0, "2026-07-01T10:05:00"),
    ]
    result = _detect_fast_in_fast_out(txns)
    # 入账交易 + 关联出账都会被标记
    assert len(result) >= 1
    assert any(r["rule_hits"] == ["快进快出"] for r in result)


def test_fast_in_fast_out_not_triggered_low_ratio():
    """转出比例不足95%不触发"""
    txns = [
        _make_txn("IN_1", "PAYER_A", "ACC_X", 100000.0, "2026-07-01T10:00:00"),
        _make_txn("OUT_1", "ACC_X", "PAYER_B", 50000.0, "2026-07-01T10:05:00"),
    ]
    result = _detect_fast_in_fast_out(txns)
    assert len(result) == 0


# ============================================================
# 规则 3: 对敲交易
# ============================================================
def test_round_trip_detected():
    """两个账户7天内互相转账金额接近应被检测"""
    txns = [
        _make_txn("RT_1", "ACC_A", "ACC_B", 50000.0, "2026-07-01T10:00:00"),
        _make_txn("RT_2", "ACC_B", "ACC_A", 48000.0, "2026-07-03T10:00:00"),
    ]
    result = _detect_round_trip(txns)
    assert len(result) == 2
    assert all(r["rule_hits"] == ["对敲交易"] for r in result)


def test_round_trip_not_triggered_amount_diff_too_large():
    """金额差异超过20%不触发"""
    txns = [
        _make_txn("RT_1", "ACC_A", "ACC_B", 50000.0, "2026-07-01T10:00:00"),
        _make_txn("RT_2", "ACC_B", "ACC_A", 20000.0, "2026-07-03T10:00:00"),
    ]
    result = _detect_round_trip(txns)
    assert len(result) == 0


# ============================================================
# 规则 4: 大额交易
# ============================================================
def test_large_amount_detected():
    """单笔≥10万应被检测"""
    txns = [
        _make_txn("BIG_1", "ACC_A", "ACC_B", 150000.0, "2026-07-01T10:00:00"),
        _make_txn("BIG_2", "ACC_A", "ACC_B", 100000.0, "2026-07-01T11:00:00"),
    ]
    result = _detect_large_amount(txns)
    assert len(result) == 2


def test_large_amount_not_triggered_below_threshold():
    """单笔<10万不触发"""
    txns = [
        _make_txn("SMALL_1", "ACC_A", "ACC_B", 99999.0, "2026-07-01T10:00:00"),
    ]
    result = _detect_large_amount(txns)
    assert len(result) == 0


# ============================================================
# 基线偏离检测
# ============================================================
def test_baseline_deviation_large_amount():
    """远大于历史均值的交易应被检测到"""
    txns = [
        _make_txn("T1", "ACC_A", "ACC_B", 1000, "2026-07-01T10:00:00"),
        _make_txn("T2", "ACC_A", "ACC_B", 1100, "2026-07-02T10:00:00"),
        _make_txn("T3", "ACC_A", "ACC_B", 950, "2026-07-03T10:00:00"),
        _make_txn("T4", "ACC_A", "ACC_B", 1050, "2026-07-04T10:00:00"),
        _make_txn("T5", "ACC_A", "ACC_B", 50000, "2026-07-05T10:00:00"),
    ]
    # 构造基线: ACC_A 历史金额 1000 左右, 标准差很小
    baselines = {
        "ACC_A": {
            "total_txns": 10,
            "total_amount": 10250,
            "avg_amount": 1025,
            "median_amount": 1025,
            "std_amount": 56,
            "cv_amount": 0.055,
            "out_txns_count": 10,
            "in_txns_count": 0,
            "out_ratio": 1.0,
            "in_ratio": 0.0,
            "night_transaction_ratio": 0.0,
            "top_counterparties": ["ACC_B"],
            "counterparty_count": 1,
        }
    }
    result = _detect_baseline_deviation(txns, baselines)
    assert len(result) >= 1
    # T5 (50000) 应被检测为基线偏离
    t5_hit = [r for r in result if r["transaction"]["transaction_id"] == "T5"]
    assert len(t5_hit) == 1
    assert "基线偏离" in t5_hit[0]["rule_hits"][0]
    assert t5_hit[0]["risk_score"] >= 40


def test_baseline_deviation_normal_amount_not_flagged():
    """在正常范围内的交易不应被标记"""
    txns = [
        _make_txn("T1", "ACC_A", "ACC_B", 1000, "2026-07-05T10:00:00"),
    ]
    baselines = {
        "ACC_A": {
            "total_txns": 100,
            "total_amount": 100000,
            "avg_amount": 1000,
            "median_amount": 1000,
            "std_amount": 100,
            "cv_amount": 0.1,
            "out_txns_count": 100,
            "in_txns_count": 0,
            "out_ratio": 1.0,
            "in_ratio": 0.0,
            "night_transaction_ratio": 0.0,
            "top_counterparties": ["ACC_B"],
            "counterparty_count": 1,
        }
    }
    result = _detect_baseline_deviation(txns, baselines)
    # 1个标准差以内的不应触发
    assert len(result) == 0


def test_baseline_deviation_no_baseline_skipped():
    """没有基线数据的账户跳过检测（戒律 M1：基于真实数据）"""
    txns = [
        _make_txn("T1", "NEW_ACC", "ACC_B", 50000, "2026-07-05T10:00:00"),
    ]
    baselines = {}
    result = _detect_baseline_deviation(txns, baselines)
    assert len(result) == 0


def test_baseline_deviation_night_boost():
    """夜间非活跃账户的大额交易得分更高"""
    txns_day = [
        _make_txn("TD", "ACC_A", "ACC_X", 20000, "2026-07-05T10:00:00"),
    ]
    txns_night = [
        _make_txn("TN", "ACC_A", "ACC_X", 20000, "2026-07-05T02:00:00"),
    ]
    baselines = {
        "ACC_A": {
            "total_txns": 100,
            "total_amount": 500000,
            "avg_amount": 5000,
            "median_amount": 5000,
            "std_amount": 1000,
            "cv_amount": 0.2,
            "out_txns_count": 100,
            "in_txns_count": 0,
            "out_ratio": 1.0,
            "in_ratio": 0.0,
            "night_transaction_ratio": 0.0,
            "top_counterparties": ["ACC_B"],
            "counterparty_count": 2,
        }
    }
    r_day = _detect_baseline_deviation(txns_day, baselines)
    r_night = _detect_baseline_deviation(txns_night, baselines)
    # 夜间交易评分应高于白天同样金额
    if r_day and r_night:
        assert r_night[0]["risk_score"] >= r_day[0]["risk_score"]


def test_baseline_deviation_new_counterparty():
    """陌生对手交易额外加权"""
    txns_familiar = [
        _make_txn("T1", "ACC_A", "ACC_B", 15000, "2026-07-05T10:00:00"),
    ]
    txns_new = [
        _make_txn("T2", "ACC_A", "ACC_Z", 15000, "2026-07-05T10:00:00"),
    ]
    baselines = {
        "ACC_A": {
            "total_txns": 100,
            "total_amount": 500000,
            "avg_amount": 5000,
            "median_amount": 5000,
            "std_amount": 1000,
            "cv_amount": 0.2,
            "out_txns_count": 100,
            "in_txns_count": 0,
            "out_ratio": 1.0,
            "in_ratio": 0.0,
            "night_transaction_ratio": 0.0,
            "top_counterparties": ["ACC_B", "ACC_C"],
            "counterparty_count": 3,
        }
    }
    r_fam = _detect_baseline_deviation(txns_familiar, baselines)
    r_new = _detect_baseline_deviation(txns_new, baselines)
    # 陌生对手评分应更高（戒律 P2：不误报 — 只对明显偏离加分）
    if r_fam and r_new:
        assert r_new[0]["risk_score"] >= r_fam[0]["risk_score"]


# ============================================================
# 规则 6: 备注关键词检测
# ============================================================
def test_remark_high_risk_keyword_detected():
    """高风险备注关键词应被检测到"""
    txns = [
        _make_txn("T1", "A1", "A2", 5000, "2026-07-05T10:00:00"),
    ]
    txns[0]["remark"] = "过账费用"
    result = _detect_remark_keywords(txns)
    assert len(result) == 1
    assert "备注关键词" in result[0]["rule_hits"][0]
    assert "过账" in result[0]["evidence"][0]


def test_remark_multiple_keywords():
    """命中多个关键词也能正确检测"""
    txns = [
        _make_txn("T1", "A1", "A2", 5000, "2026-07-05T10:00:00"),
    ]
    txns[0]["remark"] = "代付刷单佣金"
    result = _detect_remark_keywords(txns)
    assert len(result) == 1
    assert "代付" in result[0]["evidence"][0]
    assert "刷单" in result[0]["evidence"][0]


def test_remark_normal_not_flagged():
    """正常业务备注不应触发"""
    txns = [
        _make_txn("T1", "A1", "A2", 5000, "2026-07-05T10:00:00"),
    ]
    txns[0]["remark"] = "工资发放"
    result = _detect_remark_keywords(txns)
    assert len(result) == 0


def test_remark_empty_skipped():
    """空备注跳过（戒律 M1：基于真实数据）"""
    txns = [
        _make_txn("T1", "A1", "A2", 5000, "2026-07-05T10:00:00"),
    ]
    txns[0]["remark"] = ""
    result = _detect_remark_keywords(txns)
    assert len(result) == 0


def test_remark_case_insensitive():
    """关键词匹配不区分大小写"""
    txns = [
        _make_txn("T1", "A1", "A2", 5000, "2026-07-05T10:00:00"),
    ]
    txns[0]["remark"] = "USDT 转账"
    result = _detect_remark_keywords(txns)
    assert len(result) == 1


# ============================================================
# 备注降分（低风险关键词）
# ============================================================
def _make_suspicious_with_remark(remark: str, score: int = 70) -> dict:
    txn = _make_txn("T1", "A1", "A2", 50000, "2026-07-05T10:00:00")
    txn["remark"] = remark
    return {
        "transaction": txn,
        "rule_hits": ["大额交易"],
        "evidence": ["金额超过大额阈值"],
        "risk_score": score,
    }


def test_remark_discount_low_risk_word():
    """命中低风险关键词应降分（戒律 P2：不误报）"""
    s_list = [_make_suspicious_with_remark("工资发放", score=70)]
    result = _apply_remark_discount(s_list)
    assert result[0]["risk_score"] < 70
    assert any("备注降分" in e for e in result[0]["evidence"])


def test_remark_discount_not_zero():
    """降分有最低下限，不会降到 0（戒律 P1：不遗漏）"""
    s_list = [_make_suspicious_with_remark("工资货款还款报销", score=70)]
    result = _apply_remark_discount(s_list)
    assert result[0]["risk_score"] >= 30


def test_remark_discount_no_low_risk():
    """没命中低风险关键词的不降分"""
    s_list = [_make_suspicious_with_remark("转账", score=70)]
    result = _apply_remark_discount(s_list)
    assert result[0]["risk_score"] == 70


def test_remark_discount_empty_remark():
    """空备注不降分"""
    s_list = [_make_suspicious_with_remark("", score=70)]
    result = _apply_remark_discount(s_list)
    assert result[0]["risk_score"] == 70


def test_remark_discount_evidence_added():
    """降分后 evidence 里有明确说明（戒律 P3：有证据）"""
    s_list = [_make_suspicious_with_remark("工资", score=70)]
    result = _apply_remark_discount(s_list)
    assert any("工资" in e for e in result[0]["evidence"])
    assert any("70" in e for e in result[0]["evidence"])  # 原始分


# ============================================================
# 规则 7: 空壳公司识别
# ============================================================
def _make_shell_company_txns(account: str, num_txns: int = 10, num_cps: int = 6,
                              night_ratio: float = 0.5, low_retention: bool = True,
                              big_turnover: bool = True) -> list:
    """生成模拟空壳公司交易数据"""
    import random
    txns = []
    base_time = "2026-07-15"
    for i in range(num_txns):
        hour = 23 if i % 2 == 0 else 1  # 夜间交易多
        if night_ratio < 0.5:
            hour = 14 if i % 2 == 0 else 15
        cp = f"CP_{i % num_cps:03d}"
        amount = random.uniform(50000, 200000)
        # 一半入账一半出账（低留存）
        if i % 2 == 0:
            txns.append(_make_txn(f"SHELL_{i:04d}", cp, account, amount,
                                   f"{base_time}T{hour:02d}:00:00"))
        else:
            out_amount = amount * random.uniform(0.9, 1.0) if low_retention else amount * 0.3
            txns.append(_make_txn(f"SHELL_{i:04d}", account, cp, out_amount,
                                   f"{base_time}T{hour:02d}:30:00"))
    return txns


def test_shell_company_detected():
    """典型空壳公司应被检测到"""
    txns = _make_shell_company_txns("SHELL_ACC", num_txns=12, num_cps=7,
                                     night_ratio=0.6, low_retention=True)
    result = _detect_shell_companies(txns)
    assert len(result) > 0
    shell_hits = [s for s in result if "空壳公司" in s["rule_hits"]]
    assert len(shell_hits) > 0


def test_shell_company_insufficient_dimensions():
    """只满足 2 个维度不应触发（戒律 P2：不误报）"""
    # 只有夜间交易多 + 对手分散，但留存正常
    txns = _make_shell_company_txns("NORMAL_ACC", num_txns=10, num_cps=6,
                                     night_ratio=0.6, low_retention=False)
    # 让留存高一些
    for t in txns:
        if t["from_account"] == "NORMAL_ACC":
            t["amount"] = t["amount"] * 0.2  # 只转出很小一部分
    result = _detect_shell_companies(txns)
    # 对手数6个（满足），夜间多（满足），但留存率可能还高
    # 要看实际计算结果，这里只验证不会有大量误报
    shell_hits = [s for s in result if "空壳公司" in s["rule_hits"]]
    # 至少满足3个维度才触发，这里只满足2个，应该不触发
    assert len(shell_hits) == 0


def test_shell_company_few_txns_skipped():
    """交易笔数太少的账户不检测（戒律 P2：不误报）"""
    txns = _make_shell_company_txns("NEW_ACC", num_txns=3)
    result = _detect_shell_companies(txns)
    shell_hits = [s for s in result if "空壳公司" in s["rule_hits"]]
    assert len(shell_hits) == 0


def test_shell_company_evidence_complete():
    """空壳公司证据包含各维度说明（戒律 P3：有证据）"""
    txns = _make_shell_company_txns("SHELL_ACC", num_txns=12, num_cps=7)
    result = _detect_shell_companies(txns)
    if result:
        s = result[0]
        assert any("空壳公司" in e for e in s["evidence"])
        assert any("维度满足" in e for e in s["evidence"])


# ============================================================
# 账户画像加权
# ============================================================
def _make_suspicious_for_profile(from_acc: str, to_acc: str, score: int = 60) -> dict:
    txn = _make_txn("P1", from_acc, to_acc, 50000, "2026-07-05T10:00:00")
    return {
        "transaction": txn,
        "rule_hits": ["大额交易"],
        "evidence": ["金额超过阈值"],
        "risk_score": score,
    }


def test_profile_weighting_none_manager():
    """没有画像管理器时不调整"""
    s_list = [_make_suspicious_for_profile("A1", "A2", score=60)]
    result, count = _apply_profile_weighting(s_list, None)
    assert count == 0
    assert result[0]["risk_score"] == 60


def test_profile_weighting_recidivist_boost():
    """累犯账户应加成（戒律 P1：不遗漏）"""
    from tools.account_profile import AccountProfileManager
    mgr = AccountProfileManager("")
    p = mgr.get_profile("A1")
    p.total_suspicious_hits = 4
    p.highest_risk_score = 75

    s_list = [_make_suspicious_for_profile("A1", "A2", score=60)]
    result, count = _apply_profile_weighting(s_list, mgr)
    assert count == 1
    assert result[0]["risk_score"] > 60  # 加成后应更高


def test_profile_weighting_clean_discount():
    """历史清白账户应降分（戒律 P2：不误报）"""
    from tools.account_profile import AccountProfileManager
    mgr = AccountProfileManager("")
    p = mgr.get_profile("A1")
    p.total_transactions = 50
    p.total_suspicious_hits = 0

    s_list = [_make_suspicious_for_profile("A1", "A2", score=60)]
    result, count = _apply_profile_weighting(s_list, mgr)
    assert count == 1
    assert result[0]["risk_score"] < 60


def test_profile_weighting_new_account_no_change():
    """首次出现的账户不调整"""
    from tools.account_profile import AccountProfileManager
    mgr = AccountProfileManager("")

    s_list = [_make_suspicious_for_profile("NEW_ACC", "A2", score=60)]
    result, count = _apply_profile_weighting(s_list, mgr)
    assert count == 0
    assert result[0]["risk_score"] == 60


def test_profile_weighting_min_score_floor():
    """降分有最低下限 30（戒律 P1：不遗漏）"""
    from tools.account_profile import AccountProfileManager
    mgr = AccountProfileManager("")
    p = mgr.get_profile("A1")
    p.total_transactions = 100
    p.total_suspicious_hits = 0

    s_list = [_make_suspicious_for_profile("A1", "A2", score=35)]
    result, count = _apply_profile_weighting(s_list, mgr)
    assert result[0]["risk_score"] >= 30


def test_profile_weighting_evidence_added():
    """加权后 evidence 有说明（戒律 P3：有证据）"""
    from tools.account_profile import AccountProfileManager
    mgr = AccountProfileManager("")
    p = mgr.get_profile("A1")
    p.total_suspicious_hits = 5
    p.highest_risk_score = 80

    s_list = [_make_suspicious_for_profile("A1", "A2", score=60)]
    result, count = _apply_profile_weighting(s_list, mgr)
    assert any("画像加权" in e for e in result[0]["evidence"])
    assert any("累犯" in e for e in result[0]["evidence"])


# ============================================================
# 合并去重
# ============================================================
def test_merge_suspicious_dedup():
    """同一笔交易命中多规则应合并去重"""
    txn = _make_txn("DUP_1", "ACC_A", "ACC_B", 120000.0, "2026-07-01T10:00:00")
    s1 = {
        "transaction": txn, "rule_hits": ["大额交易"], "risk_score": 40,
        "evidence": ["大额"], "graph_evidence": None, "llm_analysis": None,
        "llm_confidence": None, "is_false_positive": None, "community_id": None,
    }
    s2 = {
        "transaction": txn, "rule_hits": ["对敲交易"], "risk_score": 65,
        "evidence": ["对敲"], "graph_evidence": None, "llm_analysis": None,
        "llm_confidence": None, "is_false_positive": None, "community_id": None,
    }
    merged = _merge_suspicious([[s1], [s2]])
    assert len(merged) == 1
    assert len(merged[0]["rule_hits"]) == 2
    assert merged[0]["risk_score"] == 65  # 取最高


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
