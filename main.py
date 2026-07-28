"""
AML-Agent 反洗钱多智能体分析系统
入口文件

使用方式:
    python main.py                    # 使用模拟数据
    python main.py --file data.csv    # 从 CSV/Excel/JSON 文件导入
    python main.py --file data.xlsx
"""
import os
import sys
import argparse
from datetime import datetime
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import check_config, DATA_DIR, REPORTS_DIR, has_llm as config_has_llm
from tools.data_generator import generate_test_data
from graph.workflow import AMLAgentsGraph


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="AML-Agent 反洗钱多智能体分析系统")
    parser.add_argument("--file", "-f", type=str, help="交易数据文件路径 (CSV/Excel/JSON)")
    parser.add_argument("--no-llm", action="store_true", help="禁用 LLM，使用降级模式")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)

    llm_enabled = config_has_llm() and not args.no_llm

    # 启用 LLM 时校验配置完整性（降级模式下跳过，避免误报未配置告警）
    if llm_enabled:
        if not check_config():
            print("配置校验未通过，切换到降级模式")
            llm_enabled = False

    print("=" * 70)
    print("  AML-Agent 反洗钱多智能体分析系统")
    print("  Anti-Money Laundering Multi-Agent System")
    print("=" * 70)
    print(f"  LLM 模式: {'启用 (DeepSeek)' if llm_enabled else '降级 (无LLM)'}")
    print(f"  分析日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 加载交易数据
    transactions = []
    if args.file:
        print(f"\n[数据准备] 从文件导入: {args.file}")
        try:
            from tools.data_importer import import_transactions
            import_result = import_transactions(args.file, strict=False)
            transactions = import_result["transactions"]
            print(f"  文件格式: {import_result['source_format']}")
            print(f"  总行数: {import_result['total']}")
            print(f"  有效交易: {import_result['valid']} 笔")
            if import_result["errors"]:
                print(f"  错误数: {len(import_result['errors'])}")
                for err in import_result["errors"][:5]:
                    print(f"    - {err}")
                if len(import_result["errors"]) > 5:
                    print(f"    ... 还有 {len(import_result['errors']) - 5} 条错误")
            if not transactions:
                print("  错误: 没有有效交易数据")
                sys.exit(1)
        except Exception as e:
            print(f"  导入失败: {e}")
            sys.exit(1)
    else:
        # 生成测试数据
        print("\n[数据准备] 生成模拟交易数据...")
        transactions = generate_test_data(
            normal_count=120,
            suspicious_modes=["smurfing", "fast_in_fast_out", "round_trip", "large_amount"],
        )
        print(f"  生成交易总数: {len(transactions)} 笔")

    # 初始化LLM(可选)
    llm = None
    if llm_enabled:
        try:
            from tools.llm_client import get_llm
            llm = get_llm()
            print("[LLM] DeepSeek LLM 初始化成功")
        except Exception as e:
            print(f"[LLM] 初始化失败，将使用降级模式: {e}")
            llm = None

    # 创建反洗钱分析系统
    aml_system = AMLAgentsGraph(llm=llm)

    # 执行分析
    try:
        result = aml_system.run(
            transactions=transactions,
            debug=False,
        )

        # 保存结果到文件
        output_file = os.path.join(REPORTS_DIR, f"aml_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        _save_result(result, output_file)

        # 打印摘要
        _print_summary(result)

        print(f"\n结果已保存: {output_file}")

    except Exception as e:
        print(f"\n运行失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def _print_summary(result: dict):
    """打印 Markdown 格式分析摘要"""
    str_reports = result.get("str_reports", [])
    rule_hits = result.get("rule_hits", [])
    llm_confirmed = result.get("llm_confirmed", [])
    false_positives = result.get("false_positives", [])
    compliance_score = result.get("compliance_score", None)

    print("\n" + "=" * 70)
    print("  分析结果摘要")
    print("=" * 70)

    # 统计概览表格
    print("\n## 一、检测统计概览")
    print()
    print("| 指标 | 数值 |")
    print("|------|------|")
    print(f"| 规则引擎命中 | {len(rule_hits)} 笔 |")
    print(f"| LLM 确认可疑 | {len(llm_confirmed)} 笔 |")
    print(f"| LLM 排除误报 | {len(false_positives)} 笔 |")
    print(f"| 生成可疑报告 | {len(str_reports)} 份 |")
    if compliance_score is not None:
        print(f"| 合规审核评分 | {compliance_score:.1f} / 100 |")

    # 可疑报告列表表格
    if str_reports:
        print("\n## 二、可疑交易报告清单（按风险排序）")
        print()
        print("| 序号 | 主账户 | 风险等级 | 风险评分 | 可疑笔数 | 涉案金额 | 主要模式 |")
        print("|------|--------|----------|----------|----------|----------|----------|")
        for i, rpt in enumerate(str_reports, 1):
            primary = rpt.get("primary_account", "-")
            level = rpt.get("risk_level", "-")
            txns = rpt.get("suspicious_transactions", [])
            score = max((t.get("risk_score", 0) for t in txns), default=0) if txns else 0
            count = len(txns)
            total_amt = sum(t.get("transaction", {}).get("amount", 0) for t in txns)
            patterns = rpt.get("suspicious_patterns", [])
            if isinstance(patterns, list):
                pattern_str = "、".join(p[:8] for p in patterns[:3])
            elif isinstance(patterns, str):
                pattern_str = patterns[:16]
            else:
                pattern_str = "-"
            level_tag = {"critical": "🔴 极高", "high": "🟠 高", "medium": "🟡 中", "low": "🟢 低"}.get(level, level)
            print(f"| {i} | {primary} | {level_tag} | {score:.0f} | {count} | {total_amt:,.0f} | {pattern_str} |")

    print("\n" + "=" * 70)
    print("  分析完成")
    print("=" * 70)


def _save_result(result: dict, output_path: str):
    """保存分析结果到JSON文件"""
    import json

    # 转换不可序列化的类型
    def _make_serializable(obj):
        if isinstance(obj, (list, tuple)):
            return [_make_serializable(x) for x in obj]
        elif isinstance(obj, dict):
            return {k: _make_serializable(v) for k, v in obj.items()}
        elif hasattr(obj, "model_dump"):
            return _make_serializable(obj.model_dump())
        elif hasattr(obj, "__dict__"):
            return _make_serializable(obj.__dict__)
        else:
            try:
                json.dumps(obj)
                return obj
            except (TypeError, ValueError):
                return str(obj)

    serializable_result = _make_serializable(result)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(serializable_result, f, ensure_ascii=False, indent=2, default=str)


if __name__ == "__main__":
    main()
