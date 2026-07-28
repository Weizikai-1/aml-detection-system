"""
LLM 深审 Agent 单元测试

测试覆盖:
1. 上下文构造(_build_txn_context)
2. 无LLM降级分析(_fallback_analysis)
3. LLM调用与异常降级(_analyze_with_llm)
4. Agent节点函数(端到端 + Mock LLM)
5. 风险评分权重(0.6规则 + 0.4LLM)
6. 状态隔离(不污染原state)
"""
import pytest
from agents.llm_reviewer import (
    _build_txn_context,
    _fallback_analysis,
    _analyze_with_llm,
    create_llm_reviewer_agent,
)
from graph.state import AMLState, SuspiciousTransaction, Transaction


def _make_txn(
    tid="T1", frm="ACC_A", to="ACC_B", amount=50000.0,
    ts="2026-07-01T10:00:00", txn_type="transfer", remark="货款",
):
    """构造测试交易"""
    return {
        "transaction_id": tid,
        "from_account": frm,
        "to_account": to,
        "amount": amount,
        "timestamp": ts,
        "transaction_type": txn_type,
        "remark": remark,
        "is_night": False,
        "is_weekend": False,
        "amount_level": "high",
    }


def _make_suspicious(
    risk_score=70, rule_hits=None, evidence=None, community_id=None,
    txn=None,
):
    """构造可疑交易对象"""
    return {
        "transaction": txn or _make_txn(),
        "rule_hits": rule_hits if rule_hits is not None else ["大额交易"],
        "risk_score": risk_score,
        "evidence": evidence if evidence is not None else ["单笔金额超10万"],
        "graph_evidence": None,
        "llm_analysis": None,
        "llm_confidence": None,
        "is_false_positive": None,
        "community_id": community_id,
    }


# ============================================================
# 1. 上下文构造
# ============================================================
@pytest.mark.unit
class TestBuildContext:
    def test_basic_fields_in_context(self):
        """交易基本字段(交易ID/金额/账户)都进入上下文"""
        s = _make_suspicious()
        ctx = _build_txn_context(s)

        assert "T1" in ctx
        assert "ACC_A" in ctx
        assert "ACC_B" in ctx
        assert "50,000.00" in ctx

    def test_rules_and_evidence_in_context(self):
        """命中规则和证据链都进入上下文"""
        s = _make_suspicious(
            rule_hits=["大额交易", "对敲交易"],
            evidence=["单笔金额超10万", "双向转账"],
        )
        ctx = _build_txn_context(s)

        assert "大额交易" in ctx
        assert "对敲交易" in ctx
        assert "单笔金额超10万" in ctx
        assert "双向转账" in ctx

    def test_community_id_in_context(self):
        """所属团伙ID进入上下文"""
        s = _make_suspicious(community_id="COMM_001")
        ctx = _build_txn_context(s)

        assert "COMM_001" in ctx
        assert "所属团伙" in ctx


# ============================================================
# 2. 无LLM降级分析(_fallback_analysis)
# ============================================================
@pytest.mark.unit
class TestFallbackAnalysis:
    def test_high_risk_confirmed(self):
        """规则≥2且评分≥60 → 可疑(high)"""
        s = _make_suspicious(risk_score=80, rule_hits=["大额交易", "对敲交易"])
        result = _fallback_analysis(s)

        assert result["is_suspicious"] is True
        assert result["risk_level"] == "high"
        assert result["confidence"] == 0.7

    def test_medium_risk_confirmed(self):
        """规则≥1且评分≥50 → 可疑(medium)"""
        s = _make_suspicious(risk_score=55, rule_hits=["大额交易"])
        result = _fallback_analysis(s)

        assert result["is_suspicious"] is True
        assert result["risk_level"] == "medium"

    def test_low_risk_false_positive(self):
        """评分<50 → 误报"""
        s = _make_suspicious(risk_score=30, rule_hits=["大额交易"])
        result = _fallback_analysis(s)

        assert result["is_suspicious"] is False
        assert result["risk_level"] == "low"
        assert result["false_positive_reason"] != ""


# ============================================================
# 3. LLM调用与异常降级(_analyze_with_llm)
# ============================================================
@pytest.mark.unit
class TestAnalyzeWithLLM:
    def test_normal_json_response(self, mock_llm_suspicious):
        """正常JSON响应能被正确解析"""
        s = _make_suspicious()
        result = _analyze_with_llm(mock_llm_suspicious, s)

        assert result["is_suspicious"] is True
        assert result["risk_level"] == "high"
        assert result["confidence"] == 0.9

    def test_json_in_codeblock(self, mock_llm_json_in_codeblock):
        """处理 ```json 包裹的响应"""
        s = _make_suspicious()
        result = _analyze_with_llm(mock_llm_json_in_codeblock, s)

        assert result["is_suspicious"] is True
        assert result["risk_level"] == "critical"
        assert result["confidence"] == 0.95

    def test_llm_failure_fallback(self, mock_llm_failure):
        """LLM抛异常 → 基于规则评分降级判断"""
        s = _make_suspicious(risk_score=80, rule_hits=["大额交易"])
        result = _analyze_with_llm(mock_llm_failure, s)

        # 降级路径标记
        assert result["_degraded"] is True
        # 原始分≥70 → high
        assert result["risk_level"] == "high"
        assert result["is_suspicious"] is True

    def test_llm_failure_low_score_false_positive(self, mock_llm_failure):
        """LLM失败且规则评分<60 → 误报"""
        s = _make_suspicious(risk_score=40, rule_hits=["大额交易"])
        result = _analyze_with_llm(mock_llm_failure, s)

        assert result["_degraded"] is True
        assert result["is_suspicious"] is False
        assert result["risk_level"] == "low"


# ============================================================
# 4. Agent节点函数
# ============================================================
@pytest.mark.unit
class TestLLMReviewerAgent:
    def test_empty_input(self):
        """空可疑交易 → 空结果"""
        agent = create_llm_reviewer_agent()
        state: AMLState = {"graph_suspicious": [], "rule_hits": []}
        result = agent(state)

        assert result["llm_reviewed"] == []
        assert result["llm_confirmed"] == []
        assert result["false_positives"] == []
        assert result["llm_analysis_count"] == 0

    def test_with_mock_llm_confirmed(self, mock_llm_suspicious):
        """Mock LLM 返回可疑 → 进入 confirmed"""
        s = _make_suspicious(risk_score=70)
        agent = create_llm_reviewer_agent(mock_llm_suspicious)
        state: AMLState = {"graph_suspicious": [s]}
        result = agent(state)

        assert len(result["llm_confirmed"]) == 1
        assert len(result["false_positives"]) == 0
        assert result["llm_stats"]["confirmed"] == 1

    def test_false_positive_split(self, mock_llm_false_positive):
        """Mock LLM 返回误报 → 正确分流到 false_positives"""
        s = _make_suspicious(risk_score=70)
        agent = create_llm_reviewer_agent(mock_llm_false_positive)
        state: AMLState = {"graph_suspicious": [s]}
        result = agent(state)

        assert len(result["llm_confirmed"]) == 0
        assert len(result["false_positives"]) == 1
        assert result["false_positives"][0]["is_false_positive"] is True

    def test_risk_score_weighted(self, mock_llm_suspicious):
        """验证 0.6*规则 + 0.4*LLM 权重合并（百分制）"""
        # 规则分 70，LLM 返回 high(80)
        # 期望: 0.6*70 + 0.4*80 = 42 + 32 = 74
        s = _make_suspicious(risk_score=70)
        agent = create_llm_reviewer_agent(mock_llm_suspicious)
        state: AMLState = {"graph_suspicious": [s]}
        result = agent(state)

        confirmed = result["llm_confirmed"][0]
        assert confirmed["risk_score"] == pytest.approx(74, abs=1)

    def test_degraded_keeps_original_score(self, mock_llm_failure):
        """Regression: 降级路径使用虚拟LLM评分参与权重合并，保证口径一致

        单规则命中+评分≥50时 virtual_llm = base_score，合并后值不变
        """
        s = _make_suspicious(risk_score=65)
        agent = create_llm_reviewer_agent(mock_llm_failure)
        state: AMLState = {"graph_suspicious": [s]}
        result = agent(state)

        confirmed = result["llm_confirmed"][0]
        # 单规则命中时 virtual_llm = 65，合并后 0.6*65 + 0.4*65 = 65
        assert confirmed["risk_score"] == 65
        assert confirmed["is_false_positive"] is False

    def test_degraded_multi_rule_weighted(self, mock_llm_failure):
        """降级路径多规则命中时，虚拟LLM评分参与权重合并

        多规则命中+评分≥60时 virtual_llm = min(base+10, 100)
        combined = 0.6*base + 0.4*virtual
        """
        s = _make_suspicious(
            risk_score=70,
            rule_hits=["大额交易", "快进快出"],  # 多规则命中
        )
        agent = create_llm_reviewer_agent(mock_llm_failure)
        state: AMLState = {"graph_suspicious": [s]}
        result = agent(state)

        confirmed = result["llm_confirmed"][0]
        # virtual_llm = min(70+10, 100) = 80
        # combined = round(0.6*70 + 0.4*80) = round(42+32) = 74
        assert confirmed["risk_score"] == 74
        assert confirmed["is_false_positive"] is False

    def test_state_isolation(self, mock_llm_suspicious):
        """原state中的可疑交易对象不被修改"""
        original_s = _make_suspicious(risk_score=70)
        original_score = original_s["risk_score"]
        original_llm_analysis = original_s["llm_analysis"]

        agent = create_llm_reviewer_agent(mock_llm_suspicious)
        state: AMLState = {"graph_suspicious": [original_s]}
        agent(state)

        # 原对象未被修改
        assert original_s["risk_score"] == original_score
        assert original_s["llm_analysis"] == original_llm_analysis

    def test_fallback_to_rule_hits(self, mock_llm_suspicious):
        """graph_suspicious为空时回退到 rule_hits"""
        s = _make_suspicious(risk_score=70)
        agent = create_llm_reviewer_agent(mock_llm_suspicious)
        state: AMLState = {"graph_suspicious": [], "rule_hits": [s]}
        result = agent(state)

        assert len(result["llm_reviewed"]) == 1

    def test_confirmed_sorted_by_risk(self, mock_llm_suspicious):
        """确认可疑列表按风险评分倒序排列"""
        s1 = _make_suspicious(risk_score=50, txn=_make_txn(tid="T1"))
        s2 = _make_suspicious(risk_score=90, txn=_make_txn(tid="T2"))
        agent = create_llm_reviewer_agent(mock_llm_suspicious)
        state: AMLState = {"graph_suspicious": [s1, s2]}
        result = agent(state)

        confirmed = result["llm_confirmed"]
        assert len(confirmed) == 2
        # s2 原始分更高，合并后仍更高
        assert confirmed[0]["risk_score"] >= confirmed[1]["risk_score"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
