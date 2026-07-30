"""utils 模块单元测试"""
import pytest
import numpy as np
from datetime import datetime
from utils import parse_timestamp, timestamp_to_iso, clamp_score, merge_scores, calc_binary_metrics, safe_amount, is_self_transfer


class TestTimestamp:
    def test_parse_iso_string(self):
        assert parse_timestamp("2024-01-15 10:30:00") == datetime(2024, 1, 15, 10, 30)

    def test_parse_none(self):
        assert parse_timestamp(None) is None

    def test_parse_empty(self):
        assert parse_timestamp("") is None

    def test_parse_datetime_object(self):
        dt = datetime(2024, 1, 1)
        assert parse_timestamp(dt) == dt

    def test_parse_invalid(self):
        assert parse_timestamp("not-a-date") is None

    def test_timestamp_to_iso(self):
        dt = datetime(2024, 6, 15, 14, 30)
        assert timestamp_to_iso(dt) == "2024-06-15T14:30:00"

    def test_timestamp_to_iso_none(self):
        assert timestamp_to_iso(None) == ""


class TestRiskScore:
    def test_clamp_normal(self):
        assert clamp_score(75) == 75

    def test_clamp_above_max(self):
        assert clamp_score(150) == 100

    def test_clamp_below_min(self):
        assert clamp_score(-10) == 0

    def test_clamp_boundary(self):
        assert clamp_score(0) == 0
        assert clamp_score(100) == 100

    def test_merge_scores_equal_weights(self):
        s = merge_scores([60, 80, 100])
        assert s == 80.0

    def test_merge_scores_weighted(self):
        s = merge_scores([60, 80], [0.5, 1.5])
        assert s == 75.0

    def test_merge_scores_empty(self):
        assert merge_scores([]) == 50.0


class TestBinaryMetrics:
    def test_perfect(self):
        pred = np.array([1, 1, 0, 0])
        true = np.array([1, 1, 0, 0])
        m = calc_binary_metrics(pred, true)
        assert m["precision"] == 1.0
        assert m["recall"] == 1.0
        assert m["f1"] == 1.0

    def test_all_wrong(self):
        pred = np.array([0, 0, 0])
        true = np.array([1, 1, 1])
        m = calc_binary_metrics(pred, true)
        assert m["precision"] == 0.0
        assert m["recall"] == 0.0

    def test_mixed(self):
        pred = np.array([1, 0, 1, 0])
        true = np.array([1, 1, 0, 0])
        m = calc_binary_metrics(pred, true)
        assert m["tp"] == 1
        assert m["fp"] == 1
        assert m["tn"] == 1
        assert m["fn"] == 1


class TestTransactionHelpers:
    def test_safe_amount_normal(self):
        assert safe_amount({"amount": 50000.0}) == 50000.0

    def test_safe_amount_none(self):
        assert safe_amount({"amount": None}) == 0.0

    def test_safe_amount_missing(self):
        assert safe_amount({}) == 0.0

    def test_is_self_transfer_true(self):
        assert is_self_transfer({"from_account": "A", "to_account": "A"})

    def test_is_self_transfer_false(self):
        assert not is_self_transfer({"from_account": "A", "to_account": "B"})

    def test_is_self_transfer_missing(self):
        assert not is_self_transfer({"from_account": "A"})
