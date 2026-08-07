# Changelog

## [v2.1.0] — 2026-08-04

### Added
- **GNN 模块重构**: `FraudGNN` 继承 `torch.nn.Module`，支持标准 PyTorch API (`model.to(device)`, `torch.save`, `model.train()`, `model.eval()`)
- **模型持久化**: `save_model()` / `load_model()` 函数
- **GNN 测试**: 25 个测试覆盖三种架构构造/前向传播/训练/评估/持久化
- **LLM 测试**: 18 个 mock 测试覆盖 API Key/重试/超时/Fallback/JSON 解析
- **合规测试**: 19 个测试覆盖结构检查/内容实质/证据链/评分
- **API 测试**: 15 个 FastAPI TestClient 集成测试
- **Docker 部署**: `Dockerfile` (多阶段构建) + `docker-compose.yml` (三服务)
- **性能数据**: README 新增 6 项 benchmark 指标
- **设计文档**: `docs/DESIGN.md` 8 大架构决策记录
- **覆盖率**: `pytest-cov` 覆盖率报告，核心代码 88%
- **CI**: 双 Python 版本矩阵 + 完整依赖安装 + 覆盖率

### Changed
- **Risk 配置统一**: `rule_engine.summary()` 和 `rule_engine_agent` 的阈值接入 `settings.RISK.levels`
- **README 升级**: 6 枚徽章 + 仪表盘布局预览 + 110+ 测试数
- **evaluate.py**: `np.random.seed` 从模块级移入 `main()`，epochs 接入 settings
- **配置清理**: `settings.RISK` 移除未使用的 `threshold` 字段
- **重命名**: `memory/chroma_store.py` → `memory/file_store.py`（消除命名误导）

### Removed
- **死代码**: `utils.py`（全项目零引用）

---

## [v2.0.0] — 2026-07-30

### Added
- LangGraph StateGraph 6-Agent 并行协同工作流
- 20 条 YAML 驱动反洗钱检测规则
- GAT / GraphSAGE / GCN 三种 GNN 架构
- DeepSeek LLM 深度审核（重试/超时/Fallback）
- 央行格式合规审核（9 项结构检查 + 百分制评分）
- FastAPI 接口 + Streamlit 可视化界面
- Kaggle PaySim 数据加载器
- Demo 模式高风险样本注入

---

## [v1.0.0] — 2026-07-15

### Added
- 10 条基础反洗钱规则 (PaySim 数据集)
- 规则引擎编排器 (合并/去重/排序)
- 基础评估脚本 (规则 vs 随机基线)
- 项目初始化
