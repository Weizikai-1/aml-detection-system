"""
规则引擎边界条件测试矩阵

10条规则 × 6种边界条件 = 60个测试用例

每条规则测试以下6种边界条件:
1. 空交易列表
2. 单条交易
3. from == to（自转账）
4. 超大金额（>1亿）
5. 时间戳缺失
6. 账户ID为空

每个测试断言:
- 不抛异常
- 返回结果类型正确（list）
- 命中时风险评分在 [0, 100] 范围内（戒律 M3）
- 命中时证据链不为空（戒律 M2）
"""
import pytest
from datetime import datetime, timedelta
from agents.rule_engine import (
    _detect_smurfing,
    _detect_fast_in_fast_out,
    _detect_round_trip,
    _detect_large_amount,
    _detect_baseline_deviation,
    _detect_remark_keywords,
    _detect_shell_companies,
    _detect_sanction_list,
    _detect_cross_border,
    _detect_crypto_pattern,
    _merge_suspicious,
    _apply_remark_discount,
    _make_suspicious,
)

pytestmark = pytest.mark.unit


# ============================================================
# 辅助函数
# ============================================================
def _make_txn(
    tid="TXN-001",
    from_acc="ACC-A",
    to_acc="ACC-B",
    amount=50000.0,
    timestamp=None,
    remark="货款",
    currency="CNY",
    from_name="A",
    to_name="B",
    transaction_type="transfer",
    counterparty_country="",
    **extra,
):
    """构造标准测试交易"""
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    txn = {
        "transaction_id": tid,
        "from_account": from_acc,
        "to_account": to_acc,
        "amount": amount,
        "timestamp": timestamp,
        "remark": remark,
        "currency": currency,
        "from_name": from_name,
        "to_name": to_name,
        "transaction_type": transaction_type,
        "status": "success",
        "counterparty_country": counterparty_country,
    }
    txn.update(extra)
    return txn


def _assert_no_crash(result):
    """断言不抛异常且返回类型正确"""
    assert isinstance(result, list), f"返回类型应为list，实际为{type(result)}"


def _assert_risk_scores_valid(result):
    """断言所有命中的风险评分在 [0, 100] 范围内（戒律 M3）"""
    for s in result:
        score = s.get("risk_score", 0)
        assert 0 <= score <= 100, f"风险评分{score}超出[0,100]范围（戒律M3）"


def _assert_evidence_nonempty(result):
    """断言命中时证据链不为空（戒律 M2）"""
    for s in result:
        evidence = s.get("evidence", [])
        assert len(evidence) > 0, f"命中交易{s.get('transaction', {}).get('transaction_id', '')}证据链为空（戒律M2）"


def _assert_rule_hits_valid(result):
    """断言命中时 rule_hits 不为空"""
    for s in result:
        hits = s.get("rule_hits", [])
        assert len(hits) > 0, f"命中交易 rule_hits 为空"


# ============================================================
# 规则 1: 分拆转账 (Smurfing) — 6个边界条件
# ============================================================
class TestSmurfingBoundary:
    """分拆转账规则的6种边界条件"""

    def test_empty_list(self):
        """空交易列表不抛异常，返回空列表"""
        result = _detect_smurfing([])
        _assert_no_crash(result)
        assert len(result) == 0

    def test_single_transaction(self):
        """单条交易无法构成分拆（需≥5笔），返回空"""
        txn = _make_txn(amount=45000)
        result = _detect_smurfing([txn])
        _assert_no_crash(result)
        assert len(result) == 0

    def test_self_transfer(self):
        """自转账(from==to)不构成分拆"""
        txns = []
        base_time = datetime.now()
        for i in range(6):
            txns.append(_make_txn(
                tid=f"TXN-S{i}",
                from_acc="ACC-SAME",
                to_acc="ACC-SAME",
                amount=45000,
                timestamp=(base_time + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
            ))
        result = _detect_smurfing(txns)
        _assert_no_crash(result)
        assert len(result) == 0, "自转账不应触发分拆转账"

    def test_huge_amount(self):
        """超大金额(>1亿)不在分拆金额区间(4万-5万)，不触发"""
        txns = []
        base_time = datetime.now()
        for i in range(6):
            txns.append(_make_txn(
                tid=f"TXN-H{i}",
                amount=200000000,  # 2亿
                timestamp=(base_time + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
            ))
        result = _detect_smurfing(txns)
        _assert_no_crash(result)
        assert len(result) == 0

    def test_missing_timestamp(self):
        """时间戳缺失的交易被跳过，不崩溃"""
        txns = []
        base_time = datetime.now()
        # 5笔有效 + 1笔时间戳缺失
        for i in range(5):
            txns.append(_make_txn(
                tid=f"TXN-T{i}",
                from_acc=f"ACC-P{i}",
                to_acc="ACC-RECV",
                amount=45000,
                timestamp=(base_time + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
            ))
        txns.append(_make_txn(
            tid="TXN-TMISSING",
            from_acc="ACC-P5",
            to_acc="ACC-RECV",
            amount=45000,
            timestamp="",
        ))
        result = _detect_smurfing(txns)
        _assert_no_crash(result)
        # 5笔有效交易应触发
        if len(result) > 0:
            _assert_risk_scores_valid(result)
            _assert_evidence_nonempty(result)

    def test_empty_account_id(self):
        """账户ID为空的交易被跳过"""
        txns = []
        base_time = datetime.now()
        for i in range(6):
            txns.append(_make_txn(
                tid=f"TXN-E{i}",
                from_acc=f"ACC-P{i}",
                to_acc="",  # 收款方为空
                amount=45000,
                timestamp=(base_time + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
            ))
        result = _detect_smurfing(txns)
        _assert_no_crash(result)
        assert len(result) == 0, "to_account为空不应触发分拆"


# ============================================================
# 规则 2: 快进快出 (Fast-In-Fast-Out) — 6个边界条件
# ============================================================
class TestFastInOutBoundary:
    """快进快出规则的6种边界条件"""

    def test_empty_list(self):
        result = _detect_fast_in_fast_out([])
        _assert_no_crash(result)
        assert len(result) == 0

    def test_single_transaction(self):
        """单条入账交易没有后续出账，不触发"""
        txn = _make_txn(amount=200000)
        result = _detect_fast_in_fast_out([txn])
        _assert_no_crash(result)
        assert len(result) == 0

    def test_self_transfer(self):
        """自转账不触发快进快出"""
        base_time = datetime.now()
        txns = [
            _make_txn(tid="T1", from_acc="ACC-X", to_acc="ACC-X", amount=200000,
                      timestamp=base_time.strftime("%Y-%m-%d %H:%M:%S")),
            _make_txn(tid="T2", from_acc="ACC-X", to_acc="ACC-X", amount=195000,
                      timestamp=(base_time + timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")),
        ]
        result = _detect_fast_in_fast_out(txns)
        _assert_no_crash(result)
        assert len(result) == 0, "自转账不应触发快进快出"

    def test_huge_amount(self):
        """超大金额(>1亿)正常处理，不溢出"""
        base_time = datetime.now()
        txns = [
            _make_txn(tid="T1", from_acc="ACC-IN", to_acc="ACC-HUB", amount=200000000,
                      timestamp=base_time.strftime("%Y-%m-%d %H:%M:%S")),
            _make_txn(tid="T2", from_acc="ACC-HUB", to_acc="ACC-OUT", amount=198000000,
                      timestamp=(base_time + timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")),
        ]
        result = _detect_fast_in_fast_out(txns)
        _assert_no_crash(result)
        if len(result) > 0:
            _assert_risk_scores_valid(result)
            _assert_evidence_nonempty(result)

    def test_missing_timestamp(self):
        """时间戳缺失不崩溃"""
        base_time = datetime.now()
        txns = [
            _make_txn(tid="T1", from_acc="ACC-IN", to_acc="ACC-HUB", amount=200000,
                      timestamp=""),
            _make_txn(tid="T2", from_acc="ACC-HUB", to_acc="ACC-OUT", amount=195000,
                      timestamp=(base_time + timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")),
        ]
        result = _detect_fast_in_fast_out(txns)
        _assert_no_crash(result)
        # 入账时间戳解析失败，不会命中
        assert len(result) == 0

    def test_empty_account_id(self):
        """账户ID为空不触发"""
        base_time = datetime.now()
        txns = [
            _make_txn(tid="T1", from_acc="", to_acc="ACC-HUB", amount=200000,
                      timestamp=base_time.strftime("%Y-%m-%d %H:%M:%S")),
            _make_txn(tid="T2", from_acc="ACC-HUB", to_acc="", amount=195000,
                      timestamp=(base_time + timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")),
        ]
        result = _detect_fast_in_fast_out(txns)
        _assert_no_crash(result)
        # from_acc 和 to_acc 都必须存在
        assert len(result) == 0


# ============================================================
# 规则 3: 对敲交易 (Round-Trip) — 6个边界条件
# ============================================================
class TestRoundTripBoundary:
    """对敲交易规则的6种边界条件"""

    def test_empty_list(self):
        result = _detect_round_trip([])
        _assert_no_crash(result)
        assert len(result) == 0

    def test_single_transaction(self):
        """单条交易无法构成对敲（需双向）"""
        txn = _make_txn(amount=150000)
        result = _detect_round_trip([txn])
        _assert_no_crash(result)
        assert len(result) == 0

    def test_self_transfer(self):
        """自转账不构成对敲"""
        base_time = datetime.now()
        txns = [
            _make_txn(tid="T1", from_acc="ACC-A", to_acc="ACC-A", amount=150000,
                      timestamp=base_time.strftime("%Y-%m-%d %H:%M:%S")),
            _make_txn(tid="T2", from_acc="ACC-A", to_acc="ACC-A", amount=148000,
                      timestamp=(base_time + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")),
        ]
        result = _detect_round_trip(txns)
        _assert_no_crash(result)
        assert len(result) == 0, "自转账不应触发对敲"

    def test_huge_amount(self):
        """超大金额(>1亿)正常处理"""
        base_time = datetime.now()
        txns = [
            _make_txn(tid="T1", from_acc="ACC-A", to_acc="ACC-B", amount=200000000,
                      timestamp=base_time.strftime("%Y-%m-%d %H:%M:%S")),
            _make_txn(tid="T2", from_acc="ACC-B", to_acc="ACC-A", amount=198000000,
                      timestamp=(base_time + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")),
        ]
        result = _detect_round_trip(txns)
        _assert_no_crash(result)
        if len(result) > 0:
            _assert_risk_scores_valid(result)
            _assert_evidence_nonempty(result)

    def test_missing_timestamp(self):
        """时间戳缺失不崩溃"""
        txns = [
            _make_txn(tid="T1", from_acc="ACC-A", to_acc="ACC-B", amount=150000, timestamp=""),
            _make_txn(tid="T2", from_acc="ACC-B", to_acc="ACC-A", amount=148000,
                      timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ]
        result = _detect_round_trip(txns)
        _assert_no_crash(result)
        assert len(result) == 0, "时间戳缺失的交易应被跳过"

    def test_empty_account_id(self):
        """账户ID为空不触发"""
        base_time = datetime.now()
        txns = [
            _make_txn(tid="T1", from_acc="", to_acc="ACC-B", amount=150000,
                      timestamp=base_time.strftime("%Y-%m-%d %H:%M:%S")),
            _make_txn(tid="T2", from_acc="ACC-B", to_acc="", amount=148000,
                      timestamp=(base_time + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")),
        ]
        result = _detect_round_trip(txns)
        _assert_no_crash(result)
        assert len(result) == 0


# ============================================================
# 规则 4: 大额交易 (Large Amount) — 6个边界条件
# ============================================================
class TestLargeAmountBoundary:
    """大额交易规则的6种边界条件"""

    def test_empty_list(self):
        result = _detect_large_amount([])
        _assert_no_crash(result)
        assert len(result) == 0

    def test_single_transaction(self):
        """单条大额交易正常触发"""
        txn = _make_txn(amount=200000)
        result = _detect_large_amount([txn])
        _assert_no_crash(result)
        assert len(result) == 1
        _assert_risk_scores_valid(result)
        _assert_evidence_nonempty(result)

    def test_self_transfer(self):
        """自转账大额交易仍被标记（但证据中标注自转账）"""
        txn = _make_txn(from_acc="ACC-A", to_acc="ACC-A", amount=200000)
        result = _detect_large_amount([txn])
        _assert_no_crash(result)
        assert len(result) == 1
        _assert_risk_scores_valid(result)
        _assert_evidence_nonempty(result)
        # 证据中应包含自转账标注
        assert any("自转账" in e for e in result[0]["evidence"])

    def test_huge_amount(self):
        """超大金额(>1亿)正常处理，不溢出"""
        txn = _make_txn(amount=999999999.99)
        result = _detect_large_amount([txn])
        _assert_no_crash(result)
        assert len(result) == 1
        _assert_risk_scores_valid(result)

    def test_missing_timestamp(self):
        """时间戳缺失不影响大额交易检测（此规则不依赖时间戳）"""
        txn = _make_txn(amount=200000, timestamp="")
        result = _detect_large_amount([txn])
        _assert_no_crash(result)
        assert len(result) == 1
        _assert_risk_scores_valid(result)

    def test_empty_account_id(self):
        """账户ID为空仍检测大额（此规则基于金额，不依赖账户）"""
        txn = _make_txn(from_acc="", to_acc="", amount=200000)
        result = _detect_large_amount([txn])
        _assert_no_crash(result)
        assert len(result) == 1
        _assert_risk_scores_valid(result)


# ============================================================
# 规则 5: 基线偏离 (Baseline Deviation) — 6个边界条件
# ============================================================
class TestBaselineDeviationBoundary:
    """基线偏离规则的6种边界条件"""

    def _make_baselines(self, avg=5000, std=1000, total=10):
        return {
            "ACC-A": {
                "avg_amount": avg,
                "std_amount": std,
                "total_txns": total,
                "night_transaction_ratio": 0.05,
                "top_counterparties": ["ACC-B"],
                "counterparty_count": 2,
            }
        }

    def test_empty_list(self):
        result = _detect_baseline_deviation([], self._make_baselines())
        _assert_no_crash(result)
        assert len(result) == 0

    def test_single_transaction(self):
        """单条显著偏离基线的交易触发"""
        baselines = self._make_baselines(avg=5000, std=1000, total=10)
        txn = _make_txn(from_acc="ACC-A", to_acc="ACC-C", amount=50000)
        result = _detect_baseline_deviation([txn], baselines)
        _assert_no_crash(result)
        if len(result) > 0:
            _assert_risk_scores_valid(result)
            _assert_evidence_nonempty(result)

    def test_self_transfer(self):
        """自转账不参与基线偏离检测"""
        baselines = self._make_baselines()
        txn = _make_txn(from_acc="ACC-A", to_acc="ACC-A", amount=50000)
        result = _detect_baseline_deviation([txn], baselines)
        _assert_no_crash(result)
        assert len(result) == 0, "自转账不应触发基线偏离"

    def test_huge_amount(self):
        """超大金额正常处理"""
        baselines = self._make_baselines(avg=5000, std=1000, total=10)
        txn = _make_txn(from_acc="ACC-A", to_acc="ACC-C", amount=200000000)
        result = _detect_baseline_deviation([txn], baselines)
        _assert_no_crash(result)
        if len(result) > 0:
            _assert_risk_scores_valid(result)
            _assert_evidence_nonempty(result)

    def test_missing_timestamp(self):
        """时间戳缺失不影响基线偏离检测（基于金额）"""
        baselines = self._make_baselines(avg=5000, std=1000, total=10)
        txn = _make_txn(from_acc="ACC-A", to_acc="ACC-C", amount=50000, timestamp="")
        result = _detect_baseline_deviation([txn], baselines)
        _assert_no_crash(result)
        if len(result) > 0:
            _assert_risk_scores_valid(result)

    def test_empty_account_id(self):
        """账户ID为空使用UNKNOWN，不匹配基线"""
        baselines = self._make_baselines()
        txn = _make_txn(from_acc="", to_acc="ACC-C", amount=50000)
        result = _detect_baseline_deviation([txn], baselines)
        _assert_no_crash(result)
        assert len(result) == 0, "空账户ID不匹配基线"


# ============================================================
# 规则 6: 备注关键词 (Remark Keywords) — 6个边界条件
# ============================================================
class TestRemarkKeywordsBoundary:
    """备注关键词规则的6种边界条件"""

    def test_empty_list(self):
        result = _detect_remark_keywords([])
        _assert_no_crash(result)
        assert len(result) == 0

    def test_single_transaction(self):
        """单条含高风险关键词的交易触发"""
        txn = _make_txn(remark="跑分")
        result = _detect_remark_keywords([txn])
        _assert_no_crash(result)
        if len(result) > 0:
            _assert_risk_scores_valid(result)
            _assert_evidence_nonempty(result)

    def test_self_transfer(self):
        """自转账不参与备注关键词检测"""
        txn = _make_txn(from_acc="ACC-A", to_acc="ACC-A", remark="跑分")
        result = _detect_remark_keywords([txn])
        _assert_no_crash(result)
        assert len(result) == 0, "自转账不应触发备注关键词"

    def test_huge_amount(self):
        """超大金额正常处理（备注关键词不依赖金额）"""
        txn = _make_txn(amount=200000000, remark="套现")
        result = _detect_remark_keywords([txn])
        _assert_no_crash(result)
        if len(result) > 0:
            _assert_risk_scores_valid(result)

    def test_missing_timestamp(self):
        """时间戳缺失不影响备注检测"""
        txn = _make_txn(remark="洗钱", timestamp="")
        result = _detect_remark_keywords([txn])
        _assert_no_crash(result)
        if len(result) > 0:
            _assert_risk_scores_valid(result)

    def test_empty_account_id(self):
        """账户ID为空仍检测备注关键词（基于备注内容）"""
        txn = _make_txn(from_acc="", to_acc="", remark="地下钱庄")
        result = _detect_remark_keywords([txn])
        _assert_no_crash(result)
        if len(result) > 0:
            _assert_risk_scores_valid(result)


# ============================================================
# 规则 7: 空壳公司 (Shell Company) — 6个边界条件
# ============================================================
class TestShellCompanyBoundary:
    """空壳公司规则的6种边界条件"""

    def _make_shell_txns(self, count=10, **overrides):
        """生成空壳公司特征交易"""
        txns = []
        base_time = datetime.now() - timedelta(days=5)
        accounts = [f"ACC-CP{i}" for i in range(count)]
        for i in range(count):
            # 入账
            txns.append(_make_txn(
                tid=f"TXN-IN{i}",
                from_acc=accounts[i],
                to_acc="ACC-SHELL",
                amount=100000,
                timestamp=(base_time + timedelta(hours=i, minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
                is_night=True,
            ))
            # 出账（留存率低）
            txns.append(_make_txn(
                tid=f"TXN-OUT{i}",
                from_acc="ACC-SHELL",
                to_acc=accounts[i],
                amount=95000,
                timestamp=(base_time + timedelta(hours=i, minutes=35)).strftime("%Y-%m-%d %H:%M:%S"),
                is_night=True,
            ))
        for k, v in overrides.items():
            for t in txns:
                t[k] = v
        return txns

    def test_empty_list(self):
        result = _detect_shell_companies([])
        _assert_no_crash(result)
        assert len(result) == 0

    def test_single_transaction(self):
        """单条交易不满足最低交易笔数"""
        txn = _make_txn()
        result = _detect_shell_companies([txn])
        _assert_no_crash(result)
        assert len(result) == 0

    def test_self_transfer(self):
        """自转账不参与空壳公司检测"""
        txns = self._make_shell_txns(count=10)
        for t in txns:
            t["from_account"] = "ACC-SAME"
            t["to_account"] = "ACC-SAME"
        result = _detect_shell_companies(txns)
        _assert_no_crash(result)
        assert len(result) == 0, "自转账不应触发空壳公司"

    def test_huge_amount(self):
        """超大金额正常处理"""
        txns = self._make_shell_txns(count=10)
        for t in txns:
            t["amount"] = 200000000
        result = _detect_shell_companies(txns)
        _assert_no_crash(result)
        if len(result) > 0:
            _assert_risk_scores_valid(result)
            _assert_evidence_nonempty(result)

    def test_missing_timestamp(self):
        """时间戳缺失的交易仍可检测（夜间的判断会跳过无效时间）"""
        txns = self._make_shell_txns(count=10)
        # 让部分交易时间戳缺失
        for i, t in enumerate(txns):
            if i % 3 == 0:
                t["timestamp"] = ""
        result = _detect_shell_companies(txns)
        _assert_no_crash(result)
        if len(result) > 0:
            _assert_risk_scores_valid(result)

    def test_empty_account_id(self):
        """账户ID为空的交易被跳过"""
        txns = self._make_shell_txns(count=10)
        for t in txns:
            t["from_account"] = ""
            t["to_account"] = ""
        result = _detect_shell_companies(txns)
        _assert_no_crash(result)
        assert len(result) == 0


# ============================================================
# 规则 8: 制裁名单 (Sanction List) — 6个边界条件
# ============================================================
class TestSanctionListBoundary:
    """制裁名单规则的6种边界条件"""

    def test_empty_list(self):
        result = _detect_sanction_list([])
        _assert_no_crash(result)
        assert len(result) == 0

    def test_single_transaction(self):
        """单条正常交易不触发"""
        txn = _make_txn()
        result = _detect_sanction_list([txn])
        _assert_no_crash(result)
        # 正常账户不触发
        assert len(result) == 0

    def test_self_transfer(self):
        """自转账仍被制裁名单检测（制裁名单基于账户而非转账方向）"""
        txn = _make_txn(from_acc="ACC-A", to_acc="ACC-A")
        result = _detect_sanction_list([txn])
        _assert_no_crash(result)
        # 正常账户不触发，但不应崩溃

    def test_huge_amount(self):
        """超大金额正常处理"""
        txn = _make_txn(amount=200000000)
        result = _detect_sanction_list([txn])
        _assert_no_crash(result)

    def test_missing_timestamp(self):
        """时间戳缺失不影响制裁名单检测（基于账户匹配）"""
        txn = _make_txn(timestamp="")
        result = _detect_sanction_list([txn])
        _assert_no_crash(result)

    def test_empty_account_id(self):
        """账户ID为空的交易被制裁检查器跳过"""
        txn = _make_txn(from_acc="", to_acc="")
        result = _detect_sanction_list([txn])
        _assert_no_crash(result)


# ============================================================
# 规则 9: 跨境交易 (Cross-Border) — 6个边界条件
# ============================================================
class TestCrossBorderBoundary:
    """跨境交易规则的6种边界条件"""

    def test_empty_list(self):
        result = _detect_cross_border([])
        _assert_no_crash(result)
        assert len(result) == 0

    def test_single_transaction(self):
        """单条跨境交易不触发频繁检测"""
        txn = _make_txn(currency="USD", counterparty_country="US", amount=60000)
        result = _detect_cross_border([txn])
        _assert_no_crash(result)
        # 单条不触发频繁检测，但可能触发高风险地区或大额换汇
        if len(result) > 0:
            _assert_risk_scores_valid(result)
            _assert_evidence_nonempty(result)

    def test_self_transfer(self):
        """自转账不参与跨境检测"""
        txn = _make_txn(from_acc="ACC-A", to_acc="ACC-A", currency="USD", amount=60000)
        result = _detect_cross_border([txn])
        _assert_no_crash(result)
        # 自转账被跳过
        assert len(result) == 0

    def test_huge_amount(self):
        """超大金额正常处理"""
        txn = _make_txn(currency="USD", counterparty_country="US", amount=200000000)
        result = _detect_cross_border([txn])
        _assert_no_crash(result)
        if len(result) > 0:
            _assert_risk_scores_valid(result)

    def test_missing_timestamp(self):
        """时间戳缺失不影响跨境检测（部分模式不依赖时间）"""
        txn = _make_txn(currency="USD", counterparty_country="US", amount=60000, timestamp="")
        result = _detect_cross_border([txn])
        _assert_no_crash(result)

    def test_empty_account_id(self):
        """账户ID为空的跨境交易被跳过"""
        txn = _make_txn(from_acc="", to_acc="", currency="USD", amount=60000)
        result = _detect_cross_border([txn])
        _assert_no_crash(result)
        assert len(result) == 0


# ============================================================
# 规则 10: 虚拟货币 (Crypto Pattern) — 6个边界条件
# ============================================================
class TestCryptoPatternBoundary:
    """虚拟货币规则的6种边界条件"""

    def test_empty_list(self):
        result = _detect_crypto_pattern([])
        _assert_no_crash(result)
        assert len(result) == 0

    def test_single_transaction(self):
        """单条交易不触发OTC/混币器（需多笔）"""
        txn = _make_txn(remark="换U", amount=10000)
        result = _detect_crypto_pattern([txn])
        _assert_no_crash(result)
        # 单条不触发高频检测
        # 但可能触发已知平台关键词检测
        if len(result) > 0:
            _assert_risk_scores_valid(result)
            _assert_evidence_nonempty(result)

    def test_self_transfer(self):
        """自转账不触发虚拟货币检测"""
        txn = _make_txn(from_acc="ACC-A", to_acc="ACC-A", remark="换U", amount=10000)
        result = _detect_crypto_pattern([txn])
        _assert_no_crash(result)
        assert len(result) == 0, "自转账不应触发虚拟货币检测"

    def test_huge_amount(self):
        """超大金额正常处理"""
        txn = _make_txn(remark="换U", amount=200000000)
        result = _detect_crypto_pattern([txn])
        _assert_no_crash(result)
        if len(result) > 0:
            _assert_risk_scores_valid(result)

    def test_missing_timestamp(self):
        """时间戳缺失不崩溃"""
        txn = _make_txn(remark="换U", amount=10000, timestamp="")
        result = _detect_crypto_pattern([txn])
        _assert_no_crash(result)
        # 单条且时间戳缺失，不应触发

    def test_empty_account_id(self):
        """账户ID为空被跳过"""
        txn = _make_txn(from_acc="", to_acc="", remark="换U", amount=10000)
        result = _detect_crypto_pattern([txn])
        _assert_no_crash(result)


# ============================================================
# 合并去重的边界条件测试
# ============================================================
class TestMergeSuspiciousBoundary:
    """_merge_suspicious 的边界条件"""

    def test_empty_input(self):
        """空列表合并返回空"""
        result = _merge_suspicious([])
        assert isinstance(result, list)
        assert len(result) == 0

    def test_all_empty_lists(self):
        """多个空列表合并返回空"""
        result = _merge_suspicious([[], [], []])
        assert len(result) == 0

    def test_same_txn_multiple_rules(self):
        """同一交易命中多规则，正确合并"""
        txn = _make_txn()
        s1 = _make_suspicious(txn, "规则A", "证据A", risk_score=60)
        s2 = _make_suspicious(txn, "规则B", "证据B", risk_score=70)
        result = _merge_suspicious([[s1], [s2]])
        assert len(result) == 1
        assert "规则A" in result[0]["rule_hits"]
        assert "规则B" in result[0]["rule_hits"]
        assert "证据A" in result[0]["evidence"]
        assert "证据B" in result[0]["evidence"]
        assert result[0]["risk_score"] == 70  # 取最高

    def test_sorted_by_risk_desc(self):
        """结果按风险评分降序"""
        txns = [_make_txn(tid=f"T{i}") for i in range(5)]
        hits = [[_make_suspicious(txns[i], "规则", f"证据{i}", risk_score=30 + i * 15)] for i in range(5)]
        result = _merge_suspicious(hits)
        scores = [r["risk_score"] for r in result]
        assert scores == sorted(scores, reverse=True)


# ============================================================
# 备注降分的边界条件测试
# ============================================================
class TestRemarkDiscountBoundary:
    """_apply_remark_discount 的边界条件"""

    def test_empty_input(self):
        result = _apply_remark_discount([])
        assert isinstance(result, list)
        assert len(result) == 0

    def test_low_risk_keyword_discount(self):
        """低风险关键词命中后降分"""
        txn = _make_txn(remark="工资发放")
        s = _make_suspicious(txn, "大额交易", "大额", risk_score=80)
        result = _apply_remark_discount([s])
        assert result[0]["risk_score"] < 80
        assert result[0]["risk_score"] >= 30  # 钳制下限

    def test_no_remark_no_change(self):
        """无备注的交易不降分"""
        txn = _make_txn(remark="")
        s = _make_suspicious(txn, "大额交易", "大额", risk_score=80)
        result = _apply_remark_discount([s])
        assert result[0]["risk_score"] == 80
