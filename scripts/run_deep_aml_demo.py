"""
集成演示脚本 — AML 深耕版

演示内容:
1. PaySim 数据集加载 & 特征工程
2. 异配图构建 (Account + Transaction 节点)
3. 边特征增强 GNN 模型训练与推理
4. YAML 规则动态加载 & 自适应调优
5. LLM 语义分析 & 混合裁决
6. 完整风控报告生成

运行方式:
    python scripts/run_deep_aml_demo.py

设计准则:
- M1: 所有数据真实加载/生成
- M2: 每个步骤有日志输出
- P1: 无 LLM 时使用降级模式
"""
import os
import sys
import time
import json
import random
import warnings

# 确保项目根目录在 sys.path 中
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

warnings.filterwarnings("ignore")


def step_header(title: str, step_num: int = 0):
    """打印步骤标题"""
    print("\n" + "=" * 70)
    if step_num:
        print(f"  步骤 {step_num}: {title}")
    else:
        print(f"  {title}")
    print("=" * 70)


def step_ok(msg: str):
    print(f"  ✅ {msg}")


def step_info(msg: str):
    print(f"  ℹ️  {msg}")


def step_warn(msg: str):
    print(f"  ⚠️  {msg}")


def main():
    print("\n" + "█" * 70)
    print("█  反洗钱系统 — 深耕版集成演示")
    print("█  AML Agent System Deep Demo")
    print("█" * 70)

    total_start = time.time()

    # ================================================================
    # 步骤 1: 数据层 — PaySim 数据集
    # ================================================================
    step_header("数据层: PaySim 数据集加载与特征工程", 1)

    from tools.dataset_builder import PaySimDataset, AMLGraphBuilder, load_and_build_graph

    # 加载数据 (使用模拟数据，因为真实 PaySim 需从 Kaggle 下载)
    step_info("加载 PaySim 格式数据...")
    dataset = PaySimDataset(data_path=None)
    df = dataset.load(n_rows=5000)

    step_ok(f"加载 {len(df)} 条交易")
    step_info(f"  欺诈交易: {df['isFraud'].sum()} 条 ({df['isFraud'].mean()*100:.2f}%)")
    step_info(f"  正常交易: {(df['isFraud'] == 0).sum()} 条")

    # 获取特征矩阵
    features, labels = dataset.get_feature_matrix()
    step_info(f"  特征矩阵: {features.shape}")
    step_info(f"  标签分布: 正样本 {labels.sum()}, 负样本 {len(labels) - labels.sum()}")

    # 获取账户特征
    account_features = dataset.get_account_features()
    step_info(f"  账户特征: {account_features.shape[0]} 个账户")

    # ================================================================
    # 步骤 2: 图构建 — 异配图
    # ================================================================
    step_header("图构建: 异配图 (Account + Transaction)", 2)

    # 构建同构图
    step_info("构建同构图 (Account-2-Account)...")
    builder_homo = AMLGraphBuilder()
    builder_homo.build_from_transactions(df, account_features, use_transaction_nodes=False)
    stats_homo = builder_homo.get_statistics()
    step_info(f"  节点: {stats_homo['num_nodes']}, 边: {stats_homo['num_edges']}")

    # 构建异配图
    step_info("构建异构图 (Account + Transaction)...")
    builder_hetero = AMLGraphBuilder()
    builder_hetero.build_from_transactions(df, account_features, use_transaction_nodes=True)
    stats_hetero = builder_hetero.get_statistics()
    step_info(f"  节点: {stats_hetero['num_nodes']}, 边: {stats_hetero['num_edges']}")
    step_info(f"  欺诈节点: {stats_hetero['fraud_nodes']}")

    # 转换为 PyG Data
    try:
        pyg_data = builder_homo.to_pyg_data()
        step_ok(f"PyG Data 对象创建: x={pyg_data.x.shape}, edge={pyg_data.edge_index.shape}")
        has_pyg = True
    except ImportError:
        step_warn("PyTorch Geometric 未安装，跳过 GNN 演示")
        has_pyg = False

    # ================================================================
    # 步骤 3: GNN 模型 — 边特征增强
    # ================================================================
    step_header("GNN 模型: 边特征增强 (Edge-Aware GNN)", 3)

    if has_pyg:
        import torch
        from tools.gnn_edge_model import create_edge_gnn, is_edge_gnn_available

        if is_edge_gnn_available():
            step_info("初始化 Edge-Aware GNN 模型...")

            # 创建 EdgeAwareGAT 模型
            model = create_edge_gnn(
                model_type="edge_aware_gat",
                in_channels=pyg_data.x.shape[1],
                hidden_channels=64,
                edge_dim=pyg_data.edge_attr.shape[1],
                num_classes=2,
                heads=4,
                dropout=0.5,
            )

            step_info(f"  模型: EdgeAwareGAT")
            step_info(f"  参数量: {sum(p.numel() for p in model.parameters()):,}")

            # 模拟训练几步 (仅演示，非完整训练)
            step_info("模拟训练 (3 步)...")
            optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
            criterion = torch.nn.CrossEntropyLoss()

            model.train()
            for step in range(3):
                logits = model(pyg_data.x, pyg_data.edge_index, pyg_data.edge_attr)
                loss = criterion(logits, pyg_data.y)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                step_info(f"  Step {step+1}: Loss = {loss.item():.4f}")

            # 推理 + 可解释性
            step_info("推理分析...")
            model.eval()
            with torch.no_grad():
                result = model.predict_with_attention(
                    pyg_data.x, pyg_data.edge_index, pyg_data.edge_attr
                )

            probs = result["probabilities"]
            step_info(f"  可疑概率分布: [{probs.min():.4f}, {probs.max():.4f}]")
            step_info(f"  高风险节点数 (>0.7): {(probs > 0.7).sum().item()}")

            # 注意力可解释性
            attn_l1 = result["attention"]["layer1"]
            step_info(f"  注意力权重 (Layer1): 均值={attn_l1.mean():.4f}")
            step_ok("Edge-Aware GNN 推理完成")
    else:
        step_warn("跳过 GNN 演示 (PyG 不可用)")

    # ================================================================
    # 步骤 4: YAML 规则系统
    # ================================================================
    step_header("规则引擎: YAML 动态配置 & 自适应调优", 4)

    from config.rules_yaml import create_rule_management_system
    from config import AML_CONFIG

    # 戒律 M1: 传入 AML_CONFIG["rules"] 作为真实默认值，避免 YAML 生成编造阈值
    yaml_mgr, tuner, ab_tester = create_rule_management_system(defaults=AML_CONFIG["rules"])
    step_ok("规则管理系统初始化完成")

    # 查看规则
    rules = yaml_mgr.load()
    step_info(f"加载规则: {len(rules)} 条")
    for name, rule in rules.items():
        if isinstance(rule, dict) and rule.get("enabled", True):
            step_info(f"  ✔ {name}: {rule.get('description', 'N/A')[:60]}...")

    # 模拟规则反馈
    step_info("模拟规则反馈 (200 条)...")
    random.seed(42)
    for i in range(200):
        tuner.record_feedback(
            rule_name="smurfing",
            transaction_id=f"TXN-{i:04d}",
            is_correct=random.random() > 0.25,
            was_flagged=random.random() > 0.4,
            actual_fraud=random.random() > 0.65,
        )

    # 获取统计
    stats = tuner.get_rule_stats("smurfing")
    step_info(f"Smurfing 规则统计:")
    step_info(f"  精确率: {stats['precision']:.3f}")
    step_info(f"  召回率: {stats['recall']:.3f}")
    step_info(f"  F1 分数: {stats['f1_score']:.3f}")

    # 获取优化建议
    suggestions = tuner.suggest_optimizations(min_feedback=50)
    if suggestions:
        step_info(f"优化建议: {len(suggestions)} 条")
        for s in suggestions:
            step_info(f"  - [{s.get('severity', '?').upper()}] {s['rule_name']}: {s['suggestion']}")
    else:
        step_ok("规则表现良好，无需优化")

    # A/B 测试演示
    step_info("创建 A/B 测试...")
    exp = ab_tester.create_experiment(
        "demo_exp_001", "smurfing",
        new_params={"hour_window": 2, "min_count": 4},
        traffic_split=0.3,
    )

    for i in range(100):
        group = ab_tester.assign_group("demo_exp_001", f"TXN-AB-{i:04d}")
        ab_tester.record_result(
            "demo_exp_001", group,
            was_flagged=random.random() > 0.5,
            is_fraud=random.random() > 0.6,
        )

    results = ab_tester.get_results("demo_exp_001")
    step_info(f"A/B 测试结果:")
    step_info(f"  对照组 F1: {results['control']['metrics']['f1']:.3f}")
    step_info(f"  实验组 F1: {results['treatment']['metrics']['f1']:.3f}")
    step_info(f"  建议: {results['recommendation']}")

    # ================================================================
    # 步骤 5: LLM 语义分析 (降级模式)
    # ================================================================
    step_header("LLM 语义分析: 异常检测 & 混合裁决", 5)

    from agents.llm_semantic_analyzer import (
        _detect_semantic_anomaly,
        hybrid_adjudication,
        generate_risk_report,
        _fallback_semantic_check,
    )

    # 模拟可疑交易
    step_info("模拟可疑交易分析...")
    test_transactions = [
        {
            "transaction_id": "TXN-DEMO-001",
            "from_account": "622202123456780001",
            "to_account": "622202123456780002",
            "amount": 48000.0,
            "remark": "工资",
            "timestamp": "2025-01-15 03:22:00",  # 凌晨 3 点
            "channel": "手机银行",
        },
        {
            "transaction_id": "TXN-DEMO-002",
            "from_account": "622202123456780003",
            "to_account": "622202123456780004",
            "amount": 500000.0,
            "remark": "借款",
            "timestamp": "2025-01-15 14:30:00",
            "channel": "网银",
        },
        {
            "transaction_id": "TXN-DEMO-003",
            "from_account": "622202123456780005",
            "to_account": "622202123456780006",
            "amount": 3500.0,
            "remark": "餐费",
            "timestamp": "2025-01-15 12:00:00",
            "channel": "信用卡",
        },
    ]

    for txn in test_transactions:
        tid = txn["transaction_id"]
        step_info(f"\n  分析 {tid}: {txn['remark']} {txn['amount']:,.0f}元")

        # 语义检测
        semantic = _fallback_semantic_check(txn)
        anomaly = "⚠️ 异常" if semantic["anomaly_detected"] else "✅ 正常"
        step_info(f"    语义: {anomaly} ({semantic['explanation']})")

        # 混合裁决 (降级模式)
        adjudication = hybrid_adjudication(
            llm=None,
            transaction=txn,
            rule_score=random.uniform(40, 90),
            gnn_score=random.uniform(30, 85),
            semantic_result=semantic,
            rule_hits=["smurfing"] if txn["amount"] > 100000 else [],
        )

        verdict_map = {
            "suspicious": "🔴 可疑",
            "needs_review": "🟡 待审核",
            "normal": "🟢 正常",
        }
        verdict = verdict_map.get(adjudication["final_verdict"], "❓ 未知")
        step_info(f"    裁决: {verdict} (分数={adjudication['combined_score']})")

    # ================================================================
    # 步骤 6: 生成报告
    # ================================================================
    step_header("报告生成: 反洗钱风险报告", 6)

    # 构建演示数据
    demo_suspicious = [
        {
            "transaction": txn,
            "rule_hits": ["smurfing", "semantic_anomaly"] if i == 0 else ["fast_in_fast_out"],
            "risk_score": 85 if i == 0 else 70,
            "evidence": [f"证据{i+1}: 异常交易模式"],
        }
        for i, txn in enumerate(test_transactions[:2])
    ]

    demo_adjudications = [
        {
            "final_verdict": "suspicious",
            "confidence": 0.85,
            "combined_score": 82.5,
            "reasoning": "规则+GNN+语义三重检测均异常",
            "recommended_actions": ["提交STR", "持续监控"],
        },
        {
            "final_verdict": "needs_review",
            "confidence": 0.55,
            "combined_score": 65.0,
            "reasoning": "规则检测异常但GNN证据不足",
            "recommended_actions": ["人工复核"],
        },
    ]

    report = generate_risk_report(
        llm=None,
        suspicious_transactions=demo_suspicious,
        adjudications=demo_adjudications,
    )

    step_info("生成的反洗钱风险报告:")
    print("\n" + report)

    # ================================================================
    # 总结
    # ================================================================
    elapsed = time.time() - total_start

    print("\n" + "█" * 70)
    print("█  演示完成")
    print("█" * 70)
    print(f"\n  总耗时: {elapsed:.2f} 秒")
    print(f"\n  已演示模块:")
    print(f"    ✅ PaySim 数据集加载 & 特征工程")
    print(f"    ✅ 异配图构建 (Account + Transaction)")
    if has_pyg:
        print(f"    ✅ Edge-Aware GNN 模型 (GAT + 边特征增强)")
    print(f"    ✅ YAML 规则动态配置 & 自适应调优")
    print(f"    ✅ A/B 测试新旧规则")
    print(f"    ✅ LLM 语义异常检测 (降级模式)")
    print(f"    ✅ LLM + 规则 混合裁决")
    print(f"    ✅ 反洗钱风险报告生成")

    print(f"\n  真实部署前需要:")
    print(f"    1. 从 Kaggle 下载 PaySim 数据集")
    print(f"    2. 配置 LLM API Key")
    print(f"    3. 安装 PyTorch Geometric (pip install torch-geometric)")
    print(f"    4. 运行完整训练: python tools/gnn_trainer.py")

    print(f"\n  YAML 规则配置文件: config/rules/aml_rules.yaml")
    print(f"  修改此文件可热更新规则，无需重启服务")


if __name__ == "__main__":
    main()
