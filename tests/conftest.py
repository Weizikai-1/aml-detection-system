"""共享测试 fixtures"""
import sys
import os
import pytest
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@pytest.fixture
def sample_transactions():
    """5笔测试交易（2笔大额欺诈，3笔正常）"""
    return [
        {"transaction_id": "TXN_00000001", "from_account": "A1", "to_account": "A2",
         "amount": 500000.0, "transaction_type": "TRANSFER", "remark": "", "timestamp": "2024-01-01 10:00:00"},
        {"transaction_id": "TXN_00000002", "from_account": "A3", "to_account": "A4",
         "amount": 300000.0, "transaction_type": "CASH-OUT", "remark": "urgent", "timestamp": "2024-01-01 11:00:00"},
        {"transaction_id": "TXN_00000003", "from_account": "A1", "to_account": "A3",
         "amount": 5000.0, "transaction_type": "PAYMENT", "remark": "salary", "timestamp": "2024-01-01 12:00:00"},
        {"transaction_id": "TXN_00000004", "from_account": "A2", "to_account": "A1",
         "amount": 480000.0, "transaction_type": "TRANSFER", "remark": "", "timestamp": "2024-01-02 10:00:00"},
        {"transaction_id": "TXN_00000005", "from_account": "A4", "to_account": "A5",
         "amount": 10000.0, "transaction_type": "CASH-IN", "remark": "", "timestamp": "2024-01-02 11:00:00"},
    ]


@pytest.fixture
def smurfing_transactions():
    """分拆转账测试数据：同收款方1小时内≥5笔4-5万的转账"""
    txns = []
    for i in range(6):
        txns.append({
            "transaction_id": f"TXN_S{i:04d}", "from_account": f"P{i}",
            "to_account": "TARGET", "amount": 45000.0, "transaction_type": "TRANSFER",
            "remark": "", "timestamp": f"2024-01-01 10:{i:02d}:00",
        })
    return txns


@pytest.fixture
def fast_in_fast_out_transactions():
    """快进快出测试数据：入账后10分钟内95%转出"""
    return [
        {"transaction_id": "TXN_F0", "from_account": "X", "to_account": "HUB",
         "amount": 100000.0, "transaction_type": "TRANSFER", "timestamp": "2024-01-01 10:00:00"},
        {"transaction_id": "TXN_F1", "from_account": "HUB", "to_account": "Y",
         "amount": 96000.0, "transaction_type": "TRANSFER", "timestamp": "2024-01-01 10:05:00"},
    ]
