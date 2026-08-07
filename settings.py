"""
项目配置 - 单文件集中管理
原则: 无冗余，每个配置有注释说明用途
"""
import os

# 自动加载 .env 文件
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

# ---- 路径 ----
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
REPORTS_DIR = os.path.join(ROOT, "reports")

# ---- 数据 ----
PAYSIM_CSV = os.path.join(DATA_DIR, "data_table.csv")
PAYSIM_SAMPLE = os.path.join(DATA_DIR, "paysim_sample.csv")  # 快速加载用
RANDOM_SEED = 42

# ---- GNN ----
GNN = {
    "hidden_dim": 64,
    "num_layers": 2,
    "dropout": 0.5,
    "epochs": 100,
    "lr": 0.01,
    "model": "gat",          # gcn / gat / graphsage
    "heads": 4,              # GAT 注意力头数
}

# ---- 风险评分 ----
RISK = {
    "levels": {"critical": 85, "high": 70, "medium": 50, "low": 0},
}

# ---- LLM (可选) ----
LLM = {
    "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com",
    "enabled": bool(os.getenv("DEEPSEEK_API_KEY")),
}

# ---- 数据来源标注（诚实文档要求） ----
DATA_SOURCE = {
    "primary": "Kaggle PaySim (ntnu-testimon/paysim1)",
    "fallback": "PaySim 格式模拟数据（带 Ground Truth 标签）",
    "note": "模拟数据仅验证代码逻辑，非生产级评估。需接入真实 PaySim 获得可信结果。",
}
