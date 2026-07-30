"""Phase 0 验证脚本：验证精简后的核心工作流能否正常运行"""
import sys
sys.path.insert(0, ".")

from graph.workflow import AMLAgentsGraph
from tools.data_generator import generate_test_data

w = AMLAgentsGraph(llm=None)
t = generate_test_data(normal_count=20)
r = w.run(t)

print(f"\n{'='*50}")
print(f"Phase 0 验证结果")
print(f"{'='*50}")
print(f"  STR 报告: {len(r.get('str_reports', []))} 份")
print(f"  规则命中: {len(r.get('rule_hits', []))} 笔")
print(f"  总耗时: {r.get('total_processing_time', 0):.1f} 秒")
print(f"  指标: {r.get('value_metrics', {})}")
print(f"{'='*50}")
print("  ✅ 核心工作流正常运行")
