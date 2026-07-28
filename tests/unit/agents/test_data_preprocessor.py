"""
数据预处理 Agent 单元测试

测试覆盖:
1. 去重
2. 缺失值填充
3. 金额等级分类
4. 时间特征(夜间/周末)
5. 全局统计
6. 空输入
"""
from agents.data_preprocessor import (
    create_data_preprocessor_agent,
    _parse_timestamp,
    _amount_level,
    _is_night_transaction,
    _is_weekend,
)
from graph.state import AMLState, Transaction
from datetime import datetime


def _make_txn(tid, frm, to, amount, ts, txn_type="transfer", remark=""):
    return {
        "transaction_id": tid,
        "from_account": frm,
        "to_account": to,
        "amount": amount,
        "timestamp": ts,
        "transaction_type": txn_type,
        "remark": remark,
    }


class TestDeduplication:
    def test_dedup_removes_duplicates(self):
        """同ID交易只保留一条"""
        txns = [
            _make_txn("T1", "A", "B", 1000, "2026-07-01T10:00:00"),
            _make_txn("T1", "A", "B", 2000, "2026-07-01T11:00:00"),  # 重复
        ]
        agent = create_data_preprocessor_agent()
        state: AMLState = {"transactions": txns}
        result = agent(state)

        assert result["cleaned_transactions"] is not None
        assert len(result["cleaned_transactions"]) == 1
        assert result["preprocessing_stats"]["duplicates_removed"] == 1


class TestMissingValues:
    def test_fill_missing_fields(self):
        """缺失字段自动补默认值"""
        txn: Transaction = {"transaction_id": "T1"}
        agent = create_data_preprocessor_agent()
        state: AMLState = {"transactions": [txn]}
        result = agent(state)

        cleaned = result["cleaned_transactions"][0]
        assert cleaned["from_account"] == "UNKNOWN"
        assert cleaned["to_account"] == "UNKNOWN"
        # 戒律 M1: amount 缺失时保留 None，不编造 0.0
        assert cleaned["amount"] is None
        assert cleaned["transaction_type"] == "unknown"
        assert cleaned["remark"] == ""
        assert result["preprocessing_stats"]["missing_values_filled"] == 1


class TestAmountLevel:
    def test_amount_levels(self):
        """金额正确分级"""
        assert _amount_level(5000) == "low"
        assert _amount_level(30000) == "medium"
        assert _amount_level(80000) == "high"
        assert _amount_level(200000) == "very_high"

    def test_amount_level_applied(self):
        """清洗后交易包含 amount_level"""
        txns = [
            _make_txn("T1", "A", "B", 5000, "2026-07-01T10:00:00"),
            _make_txn("T2", "C", "D", 150000, "2026-07-01T11:00:00"),
        ]
        agent = create_data_preprocessor_agent()
        state: AMLState = {"transactions": txns}
        result = agent(state)

        levels = [t["amount_level"] for t in result["cleaned_transactions"]]
        assert "low" in levels
        assert "very_high" in levels


class TestTimeFeatures:
    def test_night_detection(self):
        """22:00-06:00 识别为夜间"""
        night_ts = datetime.fromisoformat("2026-07-01T03:00:00")
        day_ts = datetime.fromisoformat("2026-07-01T14:00:00")
        assert _is_night_transaction(night_ts) is True
        assert _is_night_transaction(day_ts) is False

    def test_weekend_detection(self):
        """周六周日识别为周末"""
        saturday = datetime.fromisoformat("2026-07-04T10:00:00")  # 周六
        monday = datetime.fromisoformat("2026-07-06T10:00:00")    # 周一
        assert _is_weekend(saturday) is True
        assert _is_weekend(monday) is False


class TestGlobalStats:
    def test_stats_calculation(self):
        """全局统计(总额/均值/中位数)正确"""
        txns = [
            _make_txn("T1", "A", "B", 100, "2026-07-01T10:00:00"),
            _make_txn("T2", "C", "D", 200, "2026-07-01T11:00:00"),
            _make_txn("T3", "E", "F", 300, "2026-07-01T12:00:00"),
        ]
        agent = create_data_preprocessor_agent()
        state: AMLState = {"transactions": txns}
        result = agent(state)

        features = result["transaction_features"]
        assert features["total_amount"] == 600.0
        assert features["avg_amount"] == 200.0
        assert features["median_amount"] == 200.0
        assert features["total_transactions"] == 3


class TestEmptyInput:
    def test_empty_transactions(self):
        """无交易输入返回空"""
        agent = create_data_preprocessor_agent()
        state: AMLState = {"transactions": []}
        result = agent(state)

        assert result["cleaned_transactions"] == []
        assert result["preprocessing_stats"]["total"] == 0
        assert result["current_step"] == "data_preprocessor"
        assert result["account_baselines"] == {}


# ============================================================
# 账户行为基线
# ============================================================
class TestAccountBaselines:
    def test_baselines_computed(self):
        """基线计算正确填充"""
        txns = [
            _make_txn("T1", "ACC_A", "ACC_B", 10000, "2026-07-01T10:00:00"),
            _make_txn("T2", "ACC_A", "ACC_C", 20000, "2026-07-02T14:00:00"),
            _make_txn("T3", "ACC_B", "ACC_A", 30000, "2026-07-03T09:00:00"),
        ]
        agent = create_data_preprocessor_agent()
        state: AMLState = {"transactions": txns}
        result = agent(state)

        baselines = result["account_baselines"]
        assert "ACC_A" in baselines
        assert "ACC_B" in baselines
        assert "ACC_C" in baselines
        assert baselines["ACC_A"]["total_txns"] == 3
        assert baselines["ACC_A"]["total_amount"] == 60000

    def test_baselines_avg_median(self):
        """均值和中位数计算正确"""
        txns = [
            _make_txn("T1", "X", "Y", 10000, "2026-07-01T10:00:00"),
            _make_txn("T2", "X", "Y", 20000, "2026-07-02T10:00:00"),
            _make_txn("T3", "X", "Y", 30000, "2026-07-03T10:00:00"),
        ]
        from agents.data_preprocessor import _compute_account_baselines
        baselines = _compute_account_baselines(txns)

        # X: 出账 3 笔(10k+20k+30k=60k)
        x = baselines["X"]
        assert x["out_txns_count"] == 3
        assert x["total_amount"] == 60000
        assert x["avg_amount"] == 20000
        assert x["median_amount"] == 20000

    def test_baselines_in_out_ratio(self):
        """入账出账比例正确"""
        txns = [
            _make_txn("T1", "A", "B", 80000, "2026-07-01T10:00:00"),
            _make_txn("T2", "B", "A", 20000, "2026-07-02T10:00:00"),
        ]
        from agents.data_preprocessor import _compute_account_baselines
        baselines = _compute_account_baselines(txns)

        # A: 出80k 入20k = 总100k, 出账比例0.8
        a = baselines["A"]
        assert abs(a["out_ratio"] - 0.8) < 0.01
        assert abs(a["in_ratio"] - 0.2) < 0.01

    def test_baselines_std_and_cv(self):
        """标准差和变异系数计算正确"""
        txns = [
            _make_txn("T1", "X", "Y", 10000, "2026-07-01T10:00:00"),
            _make_txn("T2", "X", "Y", 10000, "2026-07-02T10:00:00"),
            _make_txn("T3", "X", "Y", 10000, "2026-07-03T10:00:00"),
        ]
        from agents.data_preprocessor import _compute_account_baselines
        baselines = _compute_account_baselines(txns)

        # 完全相同的金额 → 标准差=0, 变异系数=0
        x = baselines["X"]
        assert x["std_amount"] == 0
        assert x["cv_amount"] == 0

    def test_baselines_top_counterparties(self):
        """Top 交易对手正确"""
        txns = [
            _make_txn("T1", "MAIN", "A1", 1000, "2026-07-01T10:00:00"),
            _make_txn("T2", "MAIN", "A2", 1000, "2026-07-02T10:00:00"),
            _make_txn("T3", "MAIN", "A1", 1000, "2026-07-03T10:00:00"),
            _make_txn("T4", "MAIN", "A1", 1000, "2026-07-04T10:00:00"),
        ]
        from agents.data_preprocessor import _compute_account_baselines
        baselines = _compute_account_baselines(txns)

        main = baselines["MAIN"]
        assert main["top_counterparties"][0] == "A1"
        assert main["counterparty_count"] == 2


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
