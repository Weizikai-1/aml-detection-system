# 贡献指南

欢迎贡献代码到 AML-Agent 反洗钱多Agent分析系统！

## 代码规范

### 通用规则
- 使用 Python 3.10+
- 遵循 PEP 8 规范
- 使用 `black` 进行代码格式化
- 使用 `isort` 进行导入排序
- 使用 `flake8` 进行代码检查

### 命名规范
- 模块名：小写 + 下划线（`data_preprocessor.py`）
- 类名：大驼峰（`DataPreprocessor`）
- 函数名：小写 + 下划线（`process_transaction`）
- 变量名：小写 + 下划线（`transaction_amount`）
- 常量名：全大写 + 下划线（`MAX_AMOUNT_THRESHOLD`）

### 业务戒律
提交代码前，请确保符合以下业务戒律：
- **M1**: 使用真实数据，不编造
- **M2**: 不遗漏高风险交易
- **M3**: 不误报正常交易
- **M4**: 标注可疑理由，可追溯
- **M5**: 审计日志完整记录

## 开发流程

### 1. 环境设置
```bash
# 克隆仓库
git clone https://github.com/Weizikai-1/aml-detection-system.git
cd aml-detection-system

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
pip install -r requirements-production.txt
```

### 2. 开发分支
- 从 `main` 分支创建特性分支：`feature/xxx`
- 修复 bug 使用：`bugfix/xxx`
- 重构代码使用：`refactor/xxx`

### 3. 提交规范
```
<类型>(<模块>): <描述>

<详细说明>
```

类型：
- `feat`: 新功能
- `fix`: 修复 bug
- `docs`: 文档更新
- `style`: 代码格式化
- `refactor`: 重构
- `test`: 测试
- `chore`: 构建/工具更新

示例：
```
feat(rule_engine): 添加跨境交易检测规则

新增跨境交易检测规则，支持多币种转换计算和地理风险评分。
```

### 4. 测试要求
- 新增功能必须添加单元测试
- 修复 bug 必须添加回归测试
- 确保测试覆盖率 >= 80%
- 运行全量测试：`pytest`

## PR 流程

1. Fork 仓库到自己的账户
2. 创建特性分支
3. 提交代码
4. 创建 Pull Request
5. 等待代码审查
6. 合并到 main 分支

## 代码审查要点

- 业务逻辑正确性（是否符合反洗钱合规要求）
- 安全性（是否存在数据泄露风险）
- 性能（是否有潜在性能瓶颈）
- 可测试性（是否易于编写测试）
- 代码风格（是否符合规范）

## 联系方式

如有问题，请通过以下方式联系：
- GitHub Issues
- 邮件：[项目维护者邮箱]

---

**注意**: 本项目涉及金融合规和反洗钱业务，请确保所有贡献符合相关法律法规要求。