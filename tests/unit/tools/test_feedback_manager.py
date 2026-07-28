"""
误反馈管理器测试 (Task 8-3)

覆盖:
- 记录反馈（合法/非法类型、空理由拒绝、空账户拒绝）
- 列表查询（按账户/类型/分析师/执行ID过滤）
- 获取单条/账户全部反馈
- 统计（总数、误报率、漏报率、准确率）
- 规则级统计
- 应用到画像（误报降权、漏报加权）
- 删除/清空
- 戒律 M1/P3: 真实数据、理由必填
- 戒律 P1/P2: 漏报加权、误报降权
"""
import os
import json
import time

import pytest

from tools.feedback_manager import FeedbackManager, VALID_FEEDBACK_TYPES
from tools.account_profile import AccountRiskProfile, AccountProfileManager


# ============================================================
# 夹具
# ============================================================
@pytest.fixture()
def fb_dir(tmp_path):
    d = tmp_path / "feedback"
    d.mkdir()
    return str(d)


@pytest.fixture()
def fm(fb_dir):
    return FeedbackManager(feedback_dir=fb_dir)


@pytest.fixture()
def profile_mgr(tmp_path):
    """临时画像管理器"""
    path = tmp_path / "profiles.json"
    return AccountProfileManager(str(path))


# ============================================================
# 记录反馈测试
# ============================================================
@pytest.mark.unit
def test_record_feedback_returns_id(fm):
    """记录反馈应返回 feedback_id"""
    fb_id = fm.record_feedback(
        transaction_id="T001",
        account="ACC_A",
        feedback_type="false_positive",
        reason="工资发放，正常业务",
        reviewer="analyst_zhang",
    )
    assert fb_id.startswith("FB-")
    assert len(fb_id) == 11  # FB- + 8 hex


@pytest.mark.unit
def test_record_feedback_creates_files(fm, fb_dir):
    """记录反馈应创建 JSON 文件和索引"""
    fb_id = fm.record_feedback(
        transaction_id="T001",
        account="ACC_A",
        feedback_type="false_positive",
        reason="正常交易",
        reviewer="analyst_zhang",
    )
    assert os.path.exists(os.path.join(fb_dir, f"{fb_id}.json"))
    assert os.path.exists(fm.index_path)


@pytest.mark.unit
def test_record_feedback_stores_all_fields(fm):
    """记录应保存所有字段"""
    fb_id = fm.record_feedback(
        transaction_id="T001",
        account="ACC_A",
        feedback_type="false_positive",
        reason="工资发放",
        reviewer="analyst_li",
        execution_id="exec1234",
        original_risk_score=75.0,
        suggested_risk_score=20.0,
        rule_hits=["大额交易", "分拆转账"],
    )
    record = fm.get_feedback(fb_id)
    assert record["transaction_id"] == "T001"
    assert record["account"] == "ACC_A"
    assert record["feedback_type"] == "false_positive"
    assert record["reason"] == "工资发放"
    assert record["reviewer"] == "analyst_li"
    assert record["execution_id"] == "exec1234"
    assert record["original_risk_score"] == 75.0
    assert record["suggested_risk_score"] == 20.0
    assert record["rule_hits"] == ["大额交易", "分拆转账"]


@pytest.mark.unit
def test_record_feedback_empty_reason_rejected(fm):
    """空理由应被拒绝（戒律 P3: 有证据）"""
    with pytest.raises(ValueError, match="理由"):
        fm.record_feedback(
            transaction_id="T001",
            account="ACC_A",
            feedback_type="false_positive",
            reason="",
            reviewer="analyst",
        )


@pytest.mark.unit
def test_record_feedback_whitespace_reason_rejected(fm):
    """纯空白理由应被拒绝"""
    with pytest.raises(ValueError, match="理由"):
        fm.record_feedback(
            transaction_id="T001",
            account="ACC_A",
            feedback_type="false_positive",
            reason="   ",
            reviewer="analyst",
        )


@pytest.mark.unit
def test_record_feedback_invalid_type_rejected(fm):
    """非法反馈类型应被拒绝"""
    with pytest.raises(ValueError, match="非法反馈类型"):
        fm.record_feedback(
            transaction_id="T001",
            account="ACC_A",
            feedback_type="invalid_type",
            reason="测试",
            reviewer="analyst",
        )


@pytest.mark.unit
def test_record_feedback_empty_account_rejected(fm):
    """空账户应被拒绝"""
    with pytest.raises(ValueError, match="账户"):
        fm.record_feedback(
            transaction_id="T001",
            account="",
            feedback_type="false_positive",
            reason="测试",
            reviewer="analyst",
        )


@pytest.mark.unit
def test_record_feedback_all_valid_types(fm):
    """所有合法类型都应能记录"""
    for fb_type in VALID_FEEDBACK_TYPES:
        fb_id = fm.record_feedback(
            transaction_id=f"T_{fb_type}",
            account="ACC_A",
            feedback_type=fb_type,
            reason=f"测试{fb_type}",
            reviewer="analyst",
        )
        assert fb_id is not None


# ============================================================
# 列表查询测试
# ============================================================
@pytest.mark.unit
def test_list_feedback_empty(fm):
    """空反馈应返回空列表"""
    assert fm.list_feedback() == []


@pytest.mark.unit
def test_list_feedback_filter_by_account(fm):
    """按账户过滤"""
    fm.record_feedback("T1", "ACC_A", "false_positive", "理由1", "r1")
    fm.record_feedback("T2", "ACC_B", "false_negative", "理由2", "r1")
    fm.record_feedback("T3", "ACC_A", "confirmed", "理由3", "r1")

    results = fm.list_feedback(account="ACC_A")
    assert len(results) == 2
    assert all(r["account"] == "ACC_A" for r in results)


@pytest.mark.unit
def test_list_feedback_filter_by_type(fm):
    """按类型过滤"""
    fm.record_feedback("T1", "ACC_A", "false_positive", "理由1", "r1")
    fm.record_feedback("T2", "ACC_B", "false_negative", "理由2", "r1")
    fm.record_feedback("T3", "ACC_C", "false_positive", "理由3", "r1")

    results = fm.list_feedback(feedback_type="false_positive")
    assert len(results) == 2
    assert all(r["feedback_type"] == "false_positive" for r in results)


@pytest.mark.unit
def test_list_feedback_filter_by_reviewer(fm):
    """按分析师过滤"""
    fm.record_feedback("T1", "ACC_A", "false_positive", "理由1", "alice")
    fm.record_feedback("T2", "ACC_B", "false_negative", "理由2", "bob")

    results = fm.list_feedback(reviewer="alice")
    assert len(results) == 1
    assert results[0]["reviewer"] == "alice"


@pytest.mark.unit
def test_list_feedback_filter_by_execution_id(fm):
    """按执行ID过滤"""
    fm.record_feedback("T1", "ACC_A", "false_positive", "理由1", "r1", execution_id="exec1")
    fm.record_feedback("T2", "ACC_B", "false_negative", "理由2", "r1", execution_id="exec2")

    results = fm.list_feedback(execution_id="exec1")
    assert len(results) == 1
    assert results[0]["execution_id"] == "exec1"


@pytest.mark.unit
def test_list_feedback_respects_limit(fm):
    """limit 应限制返回数"""
    for i in range(5):
        fm.record_feedback(f"T{i}", "ACC_A", "false_positive", f"理由{i}", "r1")
    results = fm.list_feedback(limit=3)
    assert len(results) == 3


@pytest.mark.unit
def test_list_feedback_ordered_desc_by_time(fm):
    """列表应按时间倒序（最新在前）"""
    ids = []
    for i in range(3):
        fb_id = fm.record_feedback(f"T{i}", "ACC_A", "false_positive", f"理由{i}", "r1")
        ids.append(fb_id)
        time.sleep(0.01)

    results = fm.list_feedback()
    # 最新的（最后记录的）应在前面
    assert results[0]["feedback_id"] == ids[-1]


# ============================================================
# 获取单条/账户反馈测试
# ============================================================
@pytest.mark.unit
def test_get_feedback_returns_full_record(fm):
    """get_feedback 应返回完整记录"""
    fb_id = fm.record_feedback(
        "T001", "ACC_A", "false_positive", "正常业务", "r1",
        rule_hits=["大额交易"],
    )
    record = fm.get_feedback(fb_id)
    assert record is not None
    assert record["feedback_id"] == fb_id
    assert record["rule_hits"] == ["大额交易"]
    assert "reason" in record


@pytest.mark.unit
def test_get_feedback_nonexistent_returns_none(fm):
    """不存在的ID返回None"""
    assert fm.get_feedback("FB-NONEXIST") is None


@pytest.mark.unit
def test_get_feedback_for_account(fm):
    """获取账户全部反馈"""
    fm.record_feedback("T1", "ACC_A", "false_positive", "理由1", "r1")
    fm.record_feedback("T2", "ACC_A", "false_negative", "理由2", "r1")
    fm.record_feedback("T3", "ACC_B", "confirmed", "理由3", "r1")

    records = fm.get_feedback_for_account("ACC_A")
    assert len(records) == 2
    assert all(r["account"] == "ACC_A" for r in records)


# ============================================================
# 统计测试
# ============================================================
@pytest.mark.unit
def test_stats_empty(fm):
    """空反馈统计"""
    stats = fm.get_stats()
    assert stats["total"] == 0
    assert stats["false_positive"] == 0
    assert stats["accuracy_rate"] == 0.0


@pytest.mark.unit
def test_stats_with_records(fm):
    """有记录的统计"""
    fm.record_feedback("T1", "ACC_A", "false_positive", "理由1", "r1")
    fm.record_feedback("T2", "ACC_B", "false_negative", "理由2", "r1")
    fm.record_feedback("T3", "ACC_C", "confirmed", "理由3", "r1")
    fm.record_feedback("T4", "ACC_A", "confirmed", "理由4", "r1")

    stats = fm.get_stats()
    assert stats["total"] == 4
    assert stats["false_positive"] == 1
    assert stats["false_negative"] == 1
    assert stats["confirmed"] == 2
    assert stats["affected_accounts"] == 3  # ACC_A, ACC_B, ACC_C
    # 准确率 = 2 / (2+1+1) = 0.5
    assert stats["accuracy_rate"] == 0.5


@pytest.mark.unit
def test_stats_false_positive_rate(fm):
    """误报率 = fp / (fp + confirmed)"""
    fm.record_feedback("T1", "ACC_A", "false_positive", "理由1", "r1")
    fm.record_feedback("T2", "ACC_B", "false_positive", "理由2", "r1")
    fm.record_feedback("T3", "ACC_C", "confirmed", "理由3", "r1")
    # fp_rate = 2 / (2+1) = 0.6667
    stats = fm.get_stats()
    assert abs(stats["false_positive_rate"] - 2 / 3) < 0.01


@pytest.mark.unit
def test_get_account_feedback_summary(fm):
    """账户反馈汇总"""
    fm.record_feedback("T1", "ACC_A", "false_positive", "理由1", "r1")
    fm.record_feedback("T2", "ACC_A", "false_positive", "理由2", "r1")
    fm.record_feedback("T3", "ACC_A", "false_negative", "理由3", "r1")
    fm.record_feedback("T4", "ACC_A", "confirmed", "理由4", "r1")

    summary = fm.get_account_feedback_summary("ACC_A")
    assert summary["false_positive_count"] == 2
    assert summary["false_negative_count"] == 1
    assert summary["confirmed_count"] == 1


@pytest.mark.unit
def test_get_rule_stats(fm):
    """规则级统计"""
    fm.record_feedback("T1", "ACC_A", "false_positive", "理由1", "r1",
                       rule_hits=["大额交易", "分拆转账"])
    fm.record_feedback("T2", "ACC_B", "false_positive", "理由2", "r1",
                       rule_hits=["大额交易"])
    fm.record_feedback("T3", "ACC_C", "confirmed", "理由3", "r1",
                       rule_hits=["分拆转账"])

    rule_stats = fm.get_rule_stats()
    assert "大额交易" in rule_stats
    assert rule_stats["大额交易"]["false_positive"] == 2
    assert rule_stats["大额交易"]["confirmed"] == 0
    assert "分拆转账" in rule_stats
    assert rule_stats["分拆转账"]["false_positive"] == 1
    assert rule_stats["分拆转账"]["confirmed"] == 1


# ============================================================
# 应用到画像测试（戒律 P1/P2）
# ============================================================
@pytest.mark.unit
def test_apply_to_profile_updates_false_positive(fm, profile_mgr):
    """误报反馈应更新画像 false_positive_count"""
    fm.record_feedback("T1", "ACC_A", "false_positive", "误报", "r1")
    fm.record_feedback("T2", "ACC_A", "false_positive", "再次误报", "r1")

    result = fm.apply_to_profile(profile_mgr)
    assert result["accounts_updated"] == 1
    assert result["fp_total"] == 2

    profile = profile_mgr.get_profile("ACC_A")
    assert profile.false_positive_count == 2
    assert profile.false_negative_count == 0


@pytest.mark.unit
def test_apply_to_profile_updates_false_negative(fm, profile_mgr):
    """漏报反馈应更新画像 false_negative_count"""
    fm.record_feedback("T1", "ACC_A", "false_negative", "漏报", "r1")

    result = fm.apply_to_profile(profile_mgr)
    assert result["accounts_updated"] == 1
    assert result["fn_total"] == 1

    profile = profile_mgr.get_profile("ACC_A")
    assert profile.false_negative_count == 1


@pytest.mark.unit
def test_false_positive_reduces_multiplier(fm, profile_mgr):
    """误报反馈应降低风险乘数（戒律 P2: 不误报）"""
    fm.record_feedback("T1", "ACC_A", "false_positive", "误报", "r1")
    fm.record_feedback("T2", "ACC_A", "false_positive", "再次误报", "r1")
    fm.apply_to_profile(profile_mgr)

    profile = profile_mgr.get_profile("ACC_A")
    # base=1.0 (hits=0, txns<10), fp_adjust=-0.05*2=-0.10 → 0.90
    assert profile.get_risk_multiplier() < 1.0
    assert abs(profile.get_risk_multiplier() - 0.90) < 0.001


@pytest.mark.unit
def test_false_negative_increases_multiplier(fm, profile_mgr):
    """漏报反馈应提高风险乘数（戒律 P1: 不遗漏）"""
    fm.record_feedback("T1", "ACC_A", "false_negative", "漏报", "r1")
    fm.apply_to_profile(profile_mgr)

    profile = profile_mgr.get_profile("ACC_A")
    # base=1.0, fn_adjust=+0.10*1=+0.10 → 1.10
    assert profile.get_risk_multiplier() > 1.0
    assert abs(profile.get_risk_multiplier() - 1.10) < 0.001


@pytest.mark.unit
def test_multiplier_clamped_at_minimum(fm, profile_mgr):
    """误报很多时乘数有下限 0.7（戒律 P1: 不遗漏）"""
    for i in range(10):
        fm.record_feedback(f"T{i}", "ACC_A", "false_positive", f"误报{i}", "r1")
    fm.apply_to_profile(profile_mgr)

    profile = profile_mgr.get_profile("ACC_A")
    # base=1.0, fp_adjust=-0.05*5(min(10,5))=-0.25 → 0.75
    assert profile.get_risk_multiplier() >= 0.7


@pytest.mark.unit
def test_multiplier_clamped_at_maximum(fm, profile_mgr):
    """漏报很多时乘数有上限 1.5（戒律 P2: 不误报）"""
    for i in range(10):
        fm.record_feedback(f"T{i}", "ACC_A", "false_negative", f"漏报{i}", "r1")
    fm.apply_to_profile(profile_mgr)

    profile = profile_mgr.get_profile("ACC_A")
    # base=1.0, fn_adjust=+0.10*3(min(10,3))=+0.30 → 1.30
    assert profile.get_risk_multiplier() <= 1.5


@pytest.mark.unit
def test_apply_to_profile_idempotent(fm, profile_mgr):
    """重复应用不应导致重复更新"""
    fm.record_feedback("T1", "ACC_A", "false_positive", "误报", "r1")
    fm.apply_to_profile(profile_mgr)
    # 再次应用
    result = fm.apply_to_profile(profile_mgr)
    assert result["accounts_updated"] == 0  # 数值未变化


@pytest.mark.unit
def test_apply_to_profile_multiple_accounts(fm, profile_mgr):
    """多账户反馈应分别更新"""
    fm.record_feedback("T1", "ACC_A", "false_positive", "误报", "r1")
    fm.record_feedback("T2", "ACC_B", "false_negative", "漏报", "r1")
    fm.record_feedback("T3", "ACC_C", "confirmed", "确认", "r1")

    result = fm.apply_to_profile(profile_mgr)
    assert result["accounts_updated"] == 2  # ACC_A 和 ACC_B（confirmed不计入）

    pa = profile_mgr.get_profile("ACC_A")
    pb = profile_mgr.get_profile("ACC_B")
    assert pa.false_positive_count == 1
    assert pb.false_negative_count == 1


# ============================================================
# 删除测试
# ============================================================
@pytest.mark.unit
def test_delete_feedback(fm):
    """删除反馈应移除记录和索引"""
    fb_id = fm.record_feedback("T1", "ACC_A", "false_positive", "理由", "r1")
    assert fm.delete_feedback(fb_id) is True
    assert fm.get_feedback(fb_id) is None
    assert fm.list_feedback() == []


@pytest.mark.unit
def test_delete_nonexistent_returns_false(fm):
    """删除不存在的返回False"""
    assert fm.delete_feedback("FB-NONEXIST") is False


@pytest.mark.unit
def test_clear_all(fm):
    """清空所有反馈"""
    for i in range(3):
        fm.record_feedback(f"T{i}", "ACC_A", "false_positive", f"理由{i}", "r1")
    deleted = fm.clear_all()
    assert deleted >= 4  # 3记录 + 1索引
    assert fm.list_feedback() == []


# ============================================================
# 戒律验证测试
# ============================================================
@pytest.mark.unit
def test_feedback_record_contains_reason(fm):
    """每条反馈必须包含理由（戒律 P3: 有证据）"""
    fb_id = fm.record_feedback("T1", "ACC_A", "false_positive", "工资发放正常", "r1")
    record = fm.get_feedback(fb_id)
    assert record["reason"] == "工资发放正常"


@pytest.mark.unit
def test_feedback_record_traceable(fm):
    """反馈记录应可追溯（戒律 M4: reviewer + timestamp + execution_id）"""
    fb_id = fm.record_feedback(
        "T1", "ACC_A", "false_positive", "理由", "analyst_zhang",
        execution_id="exec5678",
    )
    record = fm.get_feedback(fb_id)
    assert record["reviewer"] == "analyst_zhang"
    assert record["timestamp"] != ""
    assert record["execution_id"] == "exec5678"


@pytest.mark.unit
def test_no_fabricated_data(fm):
    """反馈数据不应有编造标记（戒律 M1）"""
    fb_id = fm.record_feedback("T1", "ACC_A", "false_positive", "真实理由", "r1")
    record = fm.get_feedback(fb_id)
    record_str = json.dumps(record, ensure_ascii=False)
    assert "编造" not in record_str
    assert "假数据" not in record_str


@pytest.mark.unit
def test_profile_serialization_preserves_feedback_fields():
    """画像序列化应保留反馈字段"""
    p = AccountRiskProfile("ACC_A")
    p.false_positive_count = 3
    p.false_negative_count = 1

    data = p.to_dict()
    assert data["false_positive_count"] == 3
    assert data["false_negative_count"] == 1

    p2 = AccountRiskProfile.from_dict(data)
    assert p2.false_positive_count == 3
    assert p2.false_negative_count == 1


@pytest.mark.unit
def test_profile_backward_compatibility():
    """旧数据（无反馈字段）应兼容加载"""
    old_data = {
        "account_id": "ACC_A",
        "total_suspicious_hits": 2,
        "highest_risk_score": 60,
    }
    p = AccountRiskProfile.from_dict(old_data)
    assert p.false_positive_count == 0
    assert p.false_negative_count == 0
    # 无反馈时乘数应与原逻辑一致
    assert p.get_risk_multiplier() == 1.0


# ============================================================
# 反馈质量三层校验测试（阶段二-2.1）
# ============================================================

# ---- 层2: 内容质量评估 ----
@pytest.mark.unit
def test_quality_reason_too_short(fm):
    """理由过短应产生警告（戒律 P3: 有证据）"""
    fb_id = fm.record_feedback(
        "T1", "ACC_A", "false_positive", "误报", "analyst_zhang",
    )
    record = fm.get_feedback(fb_id)
    # "误报" 长度2 < MIN_REASON_LENGTH(5)，且属于通用理由，应至少有1条警告
    assert len(record["quality_warnings"]) >= 1
    assert any("过短" in w for w in record["quality_warnings"])


@pytest.mark.unit
def test_quality_generic_reason(fm):
    """通用理由应产生警告（戒律 P3: 有证据）"""
    fb_id = fm.record_feedback(
        "T1", "ACC_A", "false_positive", "正常", "analyst_zhang",
    )
    record = fm.get_feedback(fb_id)
    # "正常" 属于 GENERIC_REASONS，应至少有1条警告
    assert len(record["quality_warnings"]) >= 1
    assert any("通用" in w for w in record["quality_warnings"])


@pytest.mark.unit
def test_quality_false_positive_score_contradiction(fm):
    """误报反馈但建议分≥原始分 → 逻辑矛盾警告"""
    fb_id = fm.record_feedback(
        "T1", "ACC_A", "false_positive",
        "工资发放，正常业务流程", "analyst_zhang",
        original_risk_score=70.0,
        suggested_risk_score=85.0,  # 误报但建议分升高，矛盾
    )
    record = fm.get_feedback(fb_id)
    contradiction_warnings = [w for w in record["quality_warnings"] if "矛盾" in w]
    assert len(contradiction_warnings) >= 1


@pytest.mark.unit
def test_quality_false_negative_score_contradiction(fm):
    """漏报反馈但建议分≤原始分 → 逻辑矛盾警告"""
    fb_id = fm.record_feedback(
        "T1", "ACC_A", "false_negative",
        "系统漏判，实际可疑交易", "analyst_zhang",
        original_risk_score=30.0,
        suggested_risk_score=20.0,  # 漏报但建议分降低，矛盾
    )
    record = fm.get_feedback(fb_id)
    contradiction_warnings = [w for w in record["quality_warnings"] if "矛盾" in w]
    assert len(contradiction_warnings) >= 1


@pytest.mark.unit
def test_quality_extreme_score_diff(fm):
    """建议分与原始分差异>80 → 极端分差警告"""
    fb_id = fm.record_feedback(
        "T1", "ACC_A", "false_positive",
        "正常工资发放，非可疑交易", "analyst_zhang",
        original_risk_score=90.0,
        suggested_risk_score=5.0,  # 差异85 > 80
    )
    record = fm.get_feedback(fb_id)
    diff_warnings = [w for w in record["quality_warnings"] if "差异过大" in w]
    assert len(diff_warnings) >= 1


@pytest.mark.unit
def test_quality_no_warnings_for_good_feedback(fm):
    """高质量反馈不应产生质量警告"""
    fb_id = fm.record_feedback(
        "T1", "ACC_A", "false_positive",
        "工资发放，正常业务流程，非可疑交易", "analyst_zhang",
        original_risk_score=70.0,
        suggested_risk_score=20.0,  # 误报且建议分降低，合理
    )
    record = fm.get_feedback(fb_id)
    assert record["quality_warnings"] == []


# ---- 层3: 一致性校验 ----
@pytest.mark.unit
def test_consistency_missing_reviewer(fm):
    """reviewer为空应产生可追溯性警告（戒律 M4）"""
    fb_id = fm.record_feedback(
        "T1", "ACC_A", "false_positive",
        "工资发放，正常业务流程", "",
    )
    record = fm.get_feedback(fb_id)
    trace_warnings = [w for w in record["consistency_warnings"] if "可追溯" in w]
    assert len(trace_warnings) >= 1


@pytest.mark.unit
def test_consistency_unknown_reviewer(fm):
    """reviewer为unknown应产生可追溯性警告"""
    fb_id = fm.record_feedback(
        "T1", "ACC_A", "false_positive",
        "工资发放，正常业务流程", "unknown",
    )
    record = fm.get_feedback(fb_id)
    trace_warnings = [w for w in record["consistency_warnings"] if "可追溯" in w]
    assert len(trace_warnings) >= 1


@pytest.mark.unit
def test_consistency_transaction_conflict(fm):
    """同交易已存在不同类型反馈 → 冲突警告"""
    # 第一条反馈
    fm.record_feedback(
        "T001", "ACC_A", "false_positive",
        "正常工资发放", "analyst_zhang",
    )
    # 第二条不同类型的反馈
    fb_id2 = fm.record_feedback(
        "T001", "ACC_A", "false_negative",
        "实际可疑交易", "analyst_li",
    )
    record = fm.get_feedback(fb_id2)
    conflict_warnings = [w for w in record["consistency_warnings"] if "冲突" in w]
    assert len(conflict_warnings) >= 1


@pytest.mark.unit
def test_consistency_account_burst(fm):
    """同账户24小时内>5条反馈 → 滥用警告"""
    # 先记录5条（达到阈值，下一条触发警告）
    for i in range(5):
        fm.record_feedback(
            f"T{i}", "ACC_A", "false_positive",
            f"正常业务交易{i}", "analyst_zhang",
        )
    # 第6条触发滥用警告
    fb_id = fm.record_feedback(
        "T5", "ACC_A", "false_positive",
        "正常业务交易5", "analyst_zhang",
    )
    record = fm.get_feedback(fb_id)
    burst_warnings = [w for w in record["consistency_warnings"] if "滥用" in w]
    assert len(burst_warnings) >= 1


@pytest.mark.unit
def test_consistency_no_warnings_for_normal_feedback(fm):
    """正常单条反馈不应产生一致性警告"""
    fb_id = fm.record_feedback(
        "T1", "ACC_A", "false_positive",
        "工资发放，正常业务流程", "analyst_zhang",
    )
    record = fm.get_feedback(fb_id)
    assert record["consistency_warnings"] == []


# ---- 集成: 警告记录到反馈 ----
@pytest.mark.unit
def test_warnings_recorded_in_feedback(fm):
    """警告应保存到反馈记录中（戒律 M4: 可追溯）"""
    fb_id = fm.record_feedback(
        "T1", "ACC_A", "false_positive",
        "误报", "",  # 过短理由 + 空reviewer，触发层2和层3警告
    )
    record = fm.get_feedback(fb_id)
    assert "quality_warnings" in record
    assert "consistency_warnings" in record
    assert len(record["quality_warnings"]) >= 1
    assert len(record["consistency_warnings"]) >= 1


@pytest.mark.unit
def test_warnings_do_not_block_recording(fm):
    """警告不应阻止反馈记录创建"""
    fb_id = fm.record_feedback(
        "T1", "ACC_A", "false_positive",
        "误报", "",  # 触发多条警告
    )
    # 反馈仍应成功创建
    assert fb_id.startswith("FB-")
    record = fm.get_feedback(fb_id)
    assert record is not None


# ============================================================
# 反馈权重时间衰减测试（阶段二-2.2）
# ============================================================
import time as _time
from datetime import datetime as _dt


def _make_record_with_age(fb_type: str, age_days: float) -> dict:
    """构造指定年龄的反馈记录（用于时间衰减测试）"""
    created_ts = _dt.now().timestamp() - age_days * 86400
    return {
        "feedback_id": "FB-TEST",
        "feedback_type": fb_type,
        "created_at": created_ts,
        "account": "ACC_A",
    }


@pytest.mark.unit
def test_feedback_weight_latest_is_one(fm):
    """最新反馈权重应为1.0"""
    record = _make_record_with_age("false_positive", 0)
    weight = fm.get_feedback_weight(record)
    assert weight == pytest.approx(1.0, abs=0.001)


@pytest.mark.unit
def test_feedback_weight_false_positive_half_life(fm):
    """误报半衰期90天：90天时权重≈0.5"""
    record = _make_record_with_age("false_positive", 90)
    weight = fm.get_feedback_weight(record)
    assert weight == pytest.approx(0.5, abs=0.01)


@pytest.mark.unit
def test_feedback_weight_false_negative_half_life(fm):
    """漏报半衰期365天：365天时权重≈0.5（戒律 P1: 慢衰减）"""
    record = _make_record_with_age("false_negative", 365)
    weight = fm.get_feedback_weight(record)
    assert weight == pytest.approx(0.5, abs=0.01)


@pytest.mark.unit
def test_feedback_weight_confirmed_half_life(fm):
    """确认半衰期180天：180天时权重≈0.5"""
    record = _make_record_with_age("confirmed", 180)
    weight = fm.get_feedback_weight(record)
    assert weight == pytest.approx(0.5, abs=0.01)


@pytest.mark.unit
def test_feedback_weight_false_negative_decays_slower(fm):
    """漏报衰减比误报慢（戒律 P1: 不遗漏）

    同样100天后，漏报权重 > 误报权重
    """
    fp_record = _make_record_with_age("false_positive", 100)
    fn_record = _make_record_with_age("false_negative", 100)
    fp_weight = fm.get_feedback_weight(fp_record)
    fn_weight = fm.get_feedback_weight(fn_record)
    assert fn_weight > fp_weight


@pytest.mark.unit
def test_feedback_weight_min_floor(fm):
    """很老的反馈权重不低于 MIN_FEEDBACK_WEIGHT"""
    # 100年前的反馈
    record = _make_record_with_age("false_positive", 36500)
    weight = fm.get_feedback_weight(record)
    assert weight >= fm.MIN_FEEDBACK_WEIGHT
    assert weight == pytest.approx(fm.MIN_FEEDBACK_WEIGHT, abs=0.001)


@pytest.mark.unit
def test_feedback_weight_future_timestamp_defense(fm):
    """未来时间戳防御：返回1.0"""
    future_ts = _dt.now().timestamp() + 86400  # 1天后
    record = {
        "feedback_type": "false_positive",
        "created_at": future_ts,
    }
    weight = fm.get_feedback_weight(record)
    assert weight == 1.0


@pytest.mark.unit
def test_feedback_weight_invalid_created_at(fm):
    """异常 created_at 防御：视为很老，返回最小权重"""
    record = {
        "feedback_type": "false_positive",
        "created_at": "invalid",
    }
    weight = fm.get_feedback_weight(record)
    assert weight >= fm.MIN_FEEDBACK_WEIGHT


@pytest.mark.unit
def test_weighted_account_summary(fm):
    """加权账户汇总：混合多类型反馈"""
    # 记录3条反馈：1条误报（新）、1条漏报（新）、1条误报（旧）
    fm.record_feedback("T1", "ACC_A", "false_positive",
                       "正常工资发放", "analyst_zhang")
    fm.record_feedback("T2", "ACC_A", "false_negative",
                       "系统漏判可疑", "analyst_zhang")

    # 构造一条很旧的误报反馈（直接操作索引模拟）
    import os
    old_ts = _dt.now().timestamp() - 365 * 86400  # 1年前
    old_record = {
        "feedback_id": "FB-OLD0001",
        "transaction_id": "T_OLD",
        "account": "ACC_A",
        "feedback_type": "false_positive",
        "reason": "历史误报记录",
        "reviewer": "analyst_old",
        "timestamp": "2025-01-01 00:00:00",
        "created_at": old_ts,
        "execution_id": "",
        "original_risk_score": 60.0,
        "suggested_risk_score": 10.0,
        "rule_hits": [],
        "quality_warnings": [],
        "consistency_warnings": [],
    }
    old_path = os.path.join(fm.feedback_dir, "FB-OLD0001.json")
    with open(old_path, "w", encoding="utf-8") as f:
        json.dump(old_record, f, ensure_ascii=False, indent=2)
    fm._update_index(old_record)

    summary = fm.get_weighted_account_summary("ACC_A")
    # 原始计数：2条误报，1条漏报
    assert summary["raw_count"]["false_positive"] == 2
    assert summary["raw_count"]["false_negative"] == 1
    # 加权：新误报≈1.0 + 旧误报(1年/90天半衰期)≈0.055 + 新漏报≈1.0
    # 旧误报权重: 0.5^(365/90) ≈ 0.0558
    assert summary["false_positive_weight"] < 2.0  # 旧反馈已衰减
    assert summary["false_positive_weight"] > 1.0  # 新反馈满权重
    assert summary["false_negative_weight"] == pytest.approx(1.0, abs=0.01)
    assert summary["total_weight"] > 2.0


@pytest.mark.unit
def test_weighted_account_summary_empty(fm):
    """空账户加权汇总应为0"""
    summary = fm.get_weighted_account_summary("ACC_EMPTY")
    assert summary["false_positive_weight"] == 0.0
    assert summary["false_negative_weight"] == 0.0
    assert summary["confirmed_weight"] == 0.0
    assert summary["total_weight"] == 0.0
    assert summary["raw_count"]["false_positive"] == 0
