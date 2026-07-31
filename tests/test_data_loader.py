"""数据加载器测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import pandas as pd
from data_loader import load_data, stats, get_source_label


class TestDataLoader:
    def test_load_returns_dataframe(self):
        df = load_data(100)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 100

    def test_has_required_columns(self):
        df = load_data(50)
        cols = {"step", "type", "amount", "nameOrig", "nameDest", "isFraud"}
        assert cols.issubset(set(df.columns))

    def test_fraud_rate_approx_1_3_pct(self):
        df = load_data(2000)
        rate = df["isFraud"].mean()
        # 真实 PaySim ~0.13%，模拟数据 ~1.3%
        assert 0.0005 < rate < 0.05, f"欺诈率偏离预期: {rate:.4f}"

    def test_stats_returns_dict(self):
        df = load_data(50)
        s = stats(df)
        assert "total" in s
        assert "fraud" in s
        assert s["total"] == 50

    def test_source_label(self):
        label = get_source_label()
        assert "PaySim" in label or "模拟数据" in label


class TestDataIntegrity:
    def test_amounts_positive(self):
        df = load_data(500)
        assert (df["amount"] > 0).all()

    def test_types_valid(self):
        df = load_data(100)
        valid = {"CASH_IN", "CASH_OUT", "DEBIT", "PAYMENT", "TRANSFER"}
        assert set(df["type"].unique()).issubset(valid)

    def test_reproducible(self):
        import numpy as np
        np.random.seed(42)
        from data_loader import generate_synthetic
        np.random.seed(42)
        df1 = generate_synthetic(50)
        np.random.seed(42)
        df2 = generate_synthetic(50)
        pd.testing.assert_frame_equal(df1, df2)
