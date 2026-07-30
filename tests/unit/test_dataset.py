"""数据集加载单元测试"""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.dataset_builder import PaySimDataset


class TestPaySimDataset:
    def test_load_mock_data(self):
        ds = PaySimDataset()
        df = ds.load(n_rows=1000)
        assert len(df) == 1000
        assert "isFraud" in df.columns
        assert "amount" in df.columns
        assert "type" in df.columns
        assert "nameOrig" in df.columns
        assert "nameDest" in df.columns
        assert "step" in df.columns

    def test_fraud_ratio(self):
        ds = PaySimDataset()
        df = ds.load(n_rows=2000)
        fraud_ratio = df["isFraud"].mean()
        assert 0.005 <= fraud_ratio <= 0.03, f"Fraud ratio {fraud_ratio} outside expected range"

    def test_fraud_amounts_are_large(self):
        ds = PaySimDataset()
        df = ds.load(n_rows=5000)
        fraud = df[df["isFraud"] == 1]
        normal = df[df["isFraud"] == 0]
        # 欺诈金额中位数应显著大于正常交易
        assert fraud["amount"].median() > normal["amount"].median() * 10

    def test_transaction_types(self):
        ds = PaySimDataset()
        df = ds.load(n_rows=1000)
        valid_types = {"CASH-IN", "CASH-OUT", "DEBIT", "PAYMENT", "TRANSFER"}
        assert set(df["type"].unique()) <= valid_types

    def test_preprocessing_output(self):
        ds = PaySimDataset()
        df = ds.load(n_rows=500)
        # Check preprocessed columns exist
        expected_cols = ["type_encoded", "log_amount", "hour", "day", "flow_out", "flow_in"]
        for col in expected_cols:
            assert col in df.columns, f"Missing preprocessed column: {col}"

    def test_get_feature_matrix(self):
        ds = PaySimDataset()
        ds.load(n_rows=1000)
        features, labels = ds.get_feature_matrix()
        assert features.shape[1] > 0
        assert features.shape[0] == len(labels)
        assert set(labels) <= {0, 1}

    def test_get_account_features(self):
        ds = PaySimDataset()
        ds.load(n_rows=1000)
        af = ds.get_account_features()
        assert len(af) > 0
        assert "account" in af.columns
        assert "avg_amount" in af.columns
        assert "txn_count" in af.columns

    def test_deterministic(self):
        """同一seed生成的数据应一致"""
        ds1 = PaySimDataset()
        df1 = ds1.load(n_rows=500)
        ds2 = PaySimDataset()
        df2 = ds2.load(n_rows=500)
        assert (df1["amount"].values == df2["amount"].values).all()
        assert (df1["isFraud"].values == df2["isFraud"].values).all()
