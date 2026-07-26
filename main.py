"""
AML-Agent 反洗钱多智能体分析系统
入口文件

使用方式:
    python main.py
"""
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import check_config, DATA_DIR, REPORTS_DIR
from data.data_generator import generate_test_data
from graph.workflow import AMLAgentsGraph


def main():
    """主函数"""
    # 确保目录存在
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)

    # 检查配置(DeepSeek API Key为可选，无Key时降级运行)
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
    has_llm = bool(deepseek_key and deepseek_key != "your-deepseek-api-key-here")

    print("=" * 70)
    print("  AML-Agent 反洗钱多智能体分析系统")
    print("  Anti-Money Laundering Multi-Agent System")
    print("=" * 70)
    print(f"  LLM 模式: {'启用 (DeepSeek)' if has_llm else '降级 (无LLM)'}")
    print(f"  分析日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 生成测试数据
    print("\n[数据准备] 生成模拟交易数据...")
    transactions = generate_test_data(
        normal_count=120,
        suspicious_modes=["smurfing", "fast_in_fast_out", "round_trip", "large_amount"],
    )
    print(f"  生成交易总数: {len(transactions)} 笔")

    # 初始化LLM(可选)
    llm = None
    if has_llm:
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

        print(f"\n结果已保存: {output_file}")

    except Exception as e:
        print(f"\n运行失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


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
