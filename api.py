"""
AML 反洗钱检测系统 — FastAPI 接口
启动: uvicorn api:app --host 0.0.0.0 --port 8000
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from graph.workflow import build_workflow, run_sequential

app = FastAPI(
    title="AML 反洗钱多智能体检测系统",
    description="基于 LangGraph 的 6-Agent 并行协同工作流 | DeepSeek LLM + GAT 图神经网络",
    version="2.0.0",
)

_wf = build_workflow()


class DetectRequest(BaseModel):
    n_samples: int = Query(default=2000, ge=50, le=20000, description="分析数据量")
    demo_mode: bool = Query(default=False, description="注入高风险 Demo 样本")


class DetectResponse(BaseModel):
    id: str
    timestamp: str
    data_summary: dict
    rule_summary: dict
    gnn_report: dict
    llm_count: int
    compliance: dict
    report_preview: str


@app.get("/health")
def health():
    """健康检查"""
    from llm.deepseek_client import DeepSeekClient
    from gnn_model import is_available as gnn_ok

    llm = DeepSeekClient()
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "langgraph": _wf is not None,
        "llm_available": llm.is_available(),
        "gnn_available": gnn_ok(),
    }


@app.post("/detect", response_model=DetectResponse)
def detect(req: DetectRequest):
    """
    运行反洗钱检测流水线。

    返回:
      - 数据概览、规则命中摘要、GNN 指标
      - LLM 深审结果数量
      - 合规评分和完整 STR 报告预览
    """
    state = {"n_samples": req.n_samples, "demo_mode": req.demo_mode, "errors": []}

    if _wf is not None:
        final = _wf.invoke(state)
    else:
        final = run_sequential(state)

    ds = final.get("data_summary", {})
    rr = final.get("rule_report", {})
    rs = rr.get("summary", {})
    gn = final.get("gnn_report", {})
    comp = final.get("compliance", {})
    llm_count = len(final.get("llm_reviews", []))

    return DetectResponse(
        id=datetime.now().strftime("%Y%m%d_%H%M%S"),
        timestamp=datetime.now().isoformat(),
        data_summary={
            "total": ds.get("total", 0),
            "fraud": ds.get("fraud", 0),
            "fraud_rate": ds.get("fraud_rate", "N/A"),
            "source": final.get("data_source", "N/A"),
        },
        rule_summary={
            "total_hits": rs.get("total_hits", 0),
            "high_risk": rs.get("high_risk", 0),
            "medium_risk": rs.get("medium_risk", 0),
            "low_risk": rs.get("low_risk", 0),
            "by_rule": rs.get("by_rule", {}),
        },
        gnn_report={
            "f1": gn.get("node_f1", 0),
            "precision": gn.get("node_precision", 0),
            "recall": gn.get("node_recall", 0),
            "enabled": final.get("gnn_enabled", False),
        },
        llm_count=llm_count,
        compliance={
            "passed": comp.get("passed", False),
            "score": comp.get("score", 0),
            "status": comp.get("status", "N/A"),
            "issues": comp.get("issues", []),
            "warnings": comp.get("warnings", []),
        },
        report_preview=(final.get("str_report", "") or "")[:500],
    )


@app.get("/report/{report_id}")
def get_report(report_id: str):
    """获取完整的 STR 报告 (从 reports/ 目录读取)"""
    path = os.path.join(os.path.dirname(__file__), "reports", "aml_report.md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return {"id": report_id, "report": f.read()}
    return JSONResponse({"error": "报告未找到"}, status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
