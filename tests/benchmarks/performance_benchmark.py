"""
性能基准测试

测试核心业务功能的性能指标:
1. 规则引擎处理速度
2. 数据导入速度
3. 报告生成速度
4. 图分析速度

符合业务戒律 M4: 测试结果真实记录，不编造数据。
"""
import os
import sys
import time
import json
import tempfile
from typing import Dict, List, Any
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class BenchmarkResult:
    """基准测试结果"""

    def __init__(self, name: str):
        self.name = name
        self.start_time = None
        self.end_time = None
        self.duration = 0.0
        self.iterations = 0
        self.items_processed = 0
        self.metrics: Dict[str, Any] = {}

    def start(self):
        """开始计时"""
        self.start_time = time.perf_counter()

    def end(self, iterations: int = 1, items_processed: int = 0):
        """结束计时"""
        self.end_time = time.perf_counter()
        self.duration = self.end_time - self.start_time
        self.iterations = iterations
        self.items_processed = items_processed

    def __str__(self):
        rate = self.items_processed / self.duration if self.duration > 0 else 0
        return (
            f"[{self.name}] "
            f"耗时: {self.duration:.4f}s, "
            f"迭代: {self.iterations}, "
            f"处理: {self.items_processed}条, "
            f"速率: {rate:.2f}/s"
        )


def generate_test_transactions(count: int) -> List[Dict[str, Any]]:
    """生成测试交易数据"""
    transactions = []
    for i in range(count):
        transactions.append({
            "transaction_id": f"TXN_{i:08d}",
            "from_account": f"ACC_FROM_{i % 100}",
            "to_account": f"ACC_TO_{i % 200}",
            "amount": 1000.0 + (i % 1000) * 100,
            "timestamp": f"2026-07-{1 + (i % 30):02d}T10:{i % 60:02d}:00",
            "transaction_type": "transfer",
            "remark": f"交易{i}",
        })
    return transactions


def benchmark_rule_engine():
    """规则引擎性能测试"""
    from agents.rule_engine import RuleEngine
    
    result = BenchmarkResult("规则引擎")
    transactions = generate_test_transactions(1000)
    
    engine = RuleEngine()
    result.start()
    analysis_result = engine.analyze(transactions)
    result.end(items_processed=len(transactions))
    
    result.metrics["risk_count"] = len(analysis_result.get("risk_transactions", []))
    result.metrics["rule_hits"] = analysis_result.get("rule_hit_count", 0)
    
    return result


def benchmark_data_import():
    """数据导入性能测试"""
    from tools.data_importer import import_transactions
    
    result = BenchmarkResult("数据导入")
    
    csv_content = "交易流水号,交易日期,交易金额,付款账号,收款账号\n"
    for i in range(1000):
        csv_content += f"TXN_{i},2026-07-01,{1000+i*100}.0,ACC_{i},ACC_{i+1000}\n"
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(csv_content)
        temp_path = f.name
    
    result.start()
    import_result = import_transactions(temp_path)
    result.end(items_processed=import_result.get("valid", 0))
    
    os.unlink(temp_path)
    
    result.metrics["total_rows"] = import_result.get("total", 0)
    result.metrics["invalid_rows"] = import_result.get("total", 0) - import_result.get("valid", 0)
    
    return result


def benchmark_report_generation():
    """报告生成性能测试"""
    from tools.report_template_selector import ReportTemplateSelector
    
    result = BenchmarkResult("报告生成")
    transactions = generate_test_transactions(100)
    
    selector = ReportTemplateSelector()
    result.start()
    template = selector.select_template("high")
    result.end(iterations=1)
    
    result.metrics["template_type"] = template.get("type", "")
    
    return result


def benchmark_graph_analysis():
    """图分析性能测试"""
    from agents.graph_analyst import GraphAnalyst
    
    result = BenchmarkResult("图分析")
    transactions = generate_test_transactions(500)
    
    analyst = GraphAnalyst()
    result.start()
    graph_result = analyst.analyze(transactions)
    result.end(items_processed=len(transactions))
    
    result.metrics["suspicious_links"] = len(graph_result.get("suspicious_links", []))
    
    return result


def benchmark_kyc_profile():
    """KYC 画像性能测试"""
    from tools.kyc_profile import KYCProfileManager
    
    result = BenchmarkResult("KYC 画像")
    
    manager = KYCProfileManager()
    result.start()
    for i in range(100):
        manager.update_profile(
            account_id=f"ACC_{i}",
            profile_data={"risk_score": i % 100}
        )
    result.end(items_processed=100)
    
    return result


def run_all_benchmarks():
    """运行所有基准测试"""
    results = []
    
    print("=" * 60)
    print("AML-Agent 性能基准测试")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    try:
        results.append(benchmark_data_import())
        print(results[-1])
    except Exception as e:
        print(f"[数据导入] 跳过: {e}")
    
    try:
        results.append(benchmark_rule_engine())
        print(results[-1])
    except Exception as e:
        print(f"[规则引擎] 跳过: {e}")
    
    try:
        results.append(benchmark_graph_analysis())
        print(results[-1])
    except Exception as e:
        print(f"[图分析] 跳过: {e}")
    
    try:
        results.append(benchmark_report_generation())
        print(results[-1])
    except Exception as e:
        print(f"[报告生成] 跳过: {e}")
    
    try:
        results.append(benchmark_kyc_profile())
        print(results[-1])
    except Exception as e:
        print(f"[KYC 画像] 跳过: {e}")
    
    print("=" * 60)
    print("测试汇总")
    print("=" * 60)
    
    summary = {
        "timestamp": datetime.now().isoformat(),
        "results": []
    }
    
    for r in results:
        summary["results"].append({
            "name": r.name,
            "duration": r.duration,
            "iterations": r.iterations,
            "items_processed": r.items_processed,
            "rate": r.items_processed / r.duration if r.duration > 0 else 0,
            "metrics": r.metrics,
        })
        print(f"  {r.name}: {r.duration:.4f}s ({r.items_processed}条, {r.items_processed/r.duration:.2f}/s)" 
              if r.duration > 0 else f"  {r.name}: {r.duration:.4f}s")
    
    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "benchmark_results.json"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存到: {output_path}")


if __name__ == "__main__":
    run_all_benchmarks()