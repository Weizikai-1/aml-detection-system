"""
项目配置文件（兼容层）
集中管理所有配置项 - 已迁移至 config/ 目录，此处保留向后兼容

新代码请直接从 config 子模块导入，例如:
    from config.paths import DATA_DIR
    from config.rules import RULES_CONFIG
"""
from config.paths import *
from config.llm import *
from config.rules import *
from config.risk import *
from config.report import *
from config.notifier import *
from config.cache import *
from config.lineage import *
from config.gnn import *
from config.aml_rules import *
from config.aml_config import AML_CONFIG, check_config
