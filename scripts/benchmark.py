"""性能 benchmark — 测量关键路径延迟"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import load_data
from rule_engine import run_engine, summary as rule_summary

results = {}

# 1. 数据加载
t0 = time.perf_counter()
df = load_data(5000)
t1 = time.perf_counter()
results["数据加载 (5000条)"] = f"{t1 - t0:.3f}s"

# 2. 规则引擎
records = df.to_dict("records")
t0 = time.perf_counter()
hits = run_engine(records)
info = rule_summary(hits)
t1 = time.perf_counter()
results["20条规则检测 (5000条)"] = f"{t1 - t0:.3f}s (命中 {info['total_hits']} 笔)"

# 3. GNN 图分析
try:
    from gnn_model import build_graph, train_and_eval, is_available
    if is_available():
        t0 = time.perf_counter()
        data = build_graph(df)
        t_build = time.perf_counter()
        result = train_and_eval(data, epochs=100)
        t1 = time.perf_counter()
        results["GNN 图构建 (5000条)"] = f"{t_build - t0:.2f}s"
        results["GNN 训练 100 epochs (5000节点)"] = f"{t1 - t_build:.1f}s (F1={result['node_f1']:.3f})"
except Exception as e:
    results["GNN"] = f"跳过 ({e})"

# 4. 全链路 Demo
from graph.workflow import run_sequential
t0 = time.perf_counter()
state = run_sequential({"n_samples": 200, "demo_mode": True, "errors": []})
t1 = time.perf_counter()
comp = state.get("compliance", {})
results["全链路 Demo 模式 (200条)"] = f"{t1 - t0:.1f}s (合规 {comp.get('score', 0)}/100)"

# 5. 全链路 5000条
t0 = time.perf_counter()
state = run_sequential({"n_samples": 5000, "demo_mode": False, "errors": []})
t1 = time.perf_counter()
comp = state.get("compliance", {})
results["全链路 标准模式 (5000条)"] = f"{t1 - t0:.1f}s (合规 {comp.get('score', 0)}/100)"

# 输出
print("\n" + "=" * 55)
print("  AML 系统 — 性能 Benchmark")
print("=" * 55)
for name, val in results.items():
    print(f"  {name:<30s} {val}")
print("=" * 55)
