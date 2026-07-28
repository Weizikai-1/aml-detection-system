"""
交易数据导入器

支持 CSV/Excel/JSON 格式的银行交易流水导入，自动字段映射和数据校验。

严格遵守戒律:
- M1: 使用真实数据，导入后原样保留原始字段
- P2: 不误报 - 数据有缺失或格式错误时明确报错，不臆测
"""
import os
import json
import re
from typing import List, Dict, Tuple, Optional
from datetime import datetime


# 默认字段映射：常见银行流水列名 → 系统内部字段名
# 覆盖国内主要银行格式（工商银行、建设银行、农业银行、中国银行、招商银行等）
DEFAULT_FIELD_MAPPING = {
    "transaction_id": [
        "交易流水号", "流水号", "交易ID", "transaction_id", "id", "交易编号",
        "业务流水号", "流水号/笔号", "凭证号", "交易序号", "序号", "业务编号",
        "TXN_ID", "txn_id", "trans_id", "trade_id", "流水ID", "业务ID",
    ],
    "from_account": [
        "付款账号", "付款账户", "转出账号", "from_account", "付款方账号", "借方账号",
        "付款人账号", "付款账户名", "转出账户", "发起账号", "来源账号", "付款卡号",
        "FROM_ACC", "from_acc", "source_account", "sender_account", "payer_account",
        "转出账号/卡号", "付款账号/卡号",
    ],
    "to_account": [
        "收款账号", "收款账户", "转入账号", "to_account", "收款方账号", "贷方账号",
        "收款人账号", "收款账户名", "转入账户", "接收账号", "目标账号", "收款卡号",
        "TO_ACC", "to_acc", "target_account", "receiver_account", "payee_account",
        "转入账号/卡号", "收款账号/卡号",
    ],
    "amount": [
        "交易金额", "金额", "amount", "发生额", "交易发生额", "转账金额", "付款金额",
        "收付款金额", "借贷方发生额", "借方金额", "贷方金额", "交易金额(元)",
        "AMOUNT", "amount_cny", "transaction_amount", "trade_amount", "tran_amount",
        "RMB金额", "金额(元)",
    ],
    "timestamp": [
        "交易时间", "交易日期", "时间", "timestamp", "date", "交易时点", "记账日期",
        "入账时间", "出账时间", "交易发生时间", "交易日期时间", "日期时间",
        "TXN_DATE", "txn_date", "transaction_date", "trade_date", "tran_date",
        "DATE_TIME", "date_time", "datetime", "交易日期/时间",
    ],
    "transaction_type": [
        "交易类型", "类型", "transaction_type", "业务类型", "交易性质", "业务种类",
        "收付类型", "资金流向", "TYPE", "txn_type", "transaction_type_code",
        "交易种类", "业务类型描述", "业务品种",
    ],
    "remark": [
        "摘要", "备注", "用途", "remark", "交易摘要", "附言", "用途说明", "用途附言",
        "摘要说明", "交易说明", "备注信息", "用途摘要", "REMARK", "memo", "description",
        "purpose", "交易用途", "用途备注",
    ],
    # 扩展字段（非必填，但如果有则保留）
    "from_account_name": [
        "付款人名称", "付款账户名", "付款账户名称", "转出账户名称", "付款方名称",
        "payer_name", "from_account_name", "付款人", "付款名称", "转出户名",
    ],
    "to_account_name": [
        "收款人名称", "收款账户名", "收款账户名称", "转入账户名称", "收款方名称",
        "payee_name", "to_account_name", "收款人", "收款名称", "转入户名",
    ],
    "currency": [
        "币种", "货币", "currency", "CURRENCY", "currency_code", "交易币种", "币别",
    ],
    "channel": [
        "渠道", "交易渠道", "channel", "CHANNEL", "渠道代码", "交易渠道描述",
        "操作渠道", "发起渠道",
    ],
    "status": [
        "状态", "交易状态", "status", "STATUS", "交易状态码", "状态描述",
        "处理状态", "业务状态",
    ],
}

# 必填字段
REQUIRED_FIELDS = ["from_account", "to_account", "amount", "timestamp"]

# 银行标准格式模板（国内主要银行）
BANK_FORMAT_TEMPLATES = {
    "icbc": {
        "name": "中国工商银行",
        "fields": [
            "交易日期", "交易时间", "业务种类", "交易金额",
            "借方账号", "贷方账号", "借方户名", "贷方户名",
            "摘要", "币种", "交易状态",
        ],
    },
    "ccb": {
        "name": "中国建设银行",
        "fields": [
            "交易日期", "交易时间", "交易类型", "金额",
            "付款账号", "收款账号", "付款户名", "收款户名",
            "摘要", "币种", "渠道",
        ],
    },
    "abc": {
        "name": "中国农业银行",
        "fields": [
            "日期", "时间", "交易类型", "发生额",
            "付款账号", "收款账号", "付款人", "收款人",
            "用途", "币种", "渠道",
        ],
    },
    "boc": {
        "name": "中国银行",
        "fields": [
            "交易日期", "交易时间", "业务类型", "交易金额",
            "转出账号", "转入账号", "转出账户名", "转入账户名",
            "摘要", "币种", "状态",
        ],
    },
    "cmb": {
        "name": "招商银行",
        "fields": [
            "交易日期", "交易时间", "交易类型", "金额",
            "付款账号", "收款账号", "付款人名称", "收款人名称",
            "用途", "币种", "渠道",
        ],
    },
    "psbc": {
        "name": "中国邮政储蓄银行",
        "fields": [
            "交易日期", "交易时间", "交易类型", "金额",
            "付款账号", "收款账号", "付款人名称", "收款人名称",
            "摘要", "币种", "渠道",
        ],
    },
    "cib": {
        "name": "兴业银行",
        "fields": [
            "交易日期", "交易时间", "交易类型", "金额",
            "付款账号", "收款账号", "付款人", "收款人",
            "用途", "币种", "渠道",
        ],
    },
    "spdb": {
        "name": "浦发银行",
        "fields": [
            "交易日期", "交易时间", "交易类型", "金额",
            "付款账号", "收款账号", "付款人名称", "收款人名称",
            "摘要", "币种", "渠道",
        ],
    },
}


def detect_file_format(file_path: str) -> str:
    """
    检测文件格式

    Returns:
        "csv" | "excel" | "json" | "unknown"
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".csv",):
        return "csv"
    elif ext in (".xlsx", ".xls"):
        return "excel"
    elif ext in (".json",):
        return "json"
    else:
        return "unknown"


def detect_bank_format(columns: List[str]) -> str:
    """
    根据列名自动检测银行格式

    Args:
        columns: 实际文件的列名列表

    Returns:
        银行代码（icbc/ccb/abc/boc/cmb/psbc/cib/spdb），未知返回 "unknown"
    """
    column_set = set(str(c).strip().lower() for c in columns if c)

    for bank_code, template in BANK_FORMAT_TEMPLATES.items():
        template_fields = [f.lower() for f in template["fields"]]
        matched = sum(1 for f in template_fields if f in column_set)
        # 匹配度超过 60% 认为是该银行格式
        if matched >= len(template_fields) * 0.6:
            return bank_code

    return "unknown"


def _auto_detect_mapping(columns: List[str]) -> Dict[str, str]:
    """
    根据列名自动检测字段映射

    Args:
        columns: 实际文件的列名列表

    Returns:
        {内部字段名: 实际列名} 的映射
    """
    mapping = {}
    for internal_field, candidates in DEFAULT_FIELD_MAPPING.items():
        for col in columns:
            col_clean = str(col).strip().lower()
            for candidate in candidates:
                if col_clean == candidate.lower():
                    mapping[internal_field] = col
                    break
            if internal_field in mapping:
                break
    return mapping


def _parse_amount(value) -> Optional[float]:
    """解析金额，支持多种格式"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    # 去掉逗号、货币符号、空格
    s = s.replace(",", "").replace("¥", "").replace("￥", "").replace(" ", "")
    # 处理负数（红字/借方）
    try:
        return float(s)
    except ValueError:
        return None


def _parse_timestamp(value) -> Optional[str]:
    """
    解析时间戳，支持多种格式

    Returns:
        ISO 格式字符串，解析失败返回 None
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    s = str(value).strip()
    if not s:
        return None

    # 尝试常见格式
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%Y年%m月%d日 %H:%M:%S",
        "%Y年%m月%d日",
        "%Y-%m-%d %H时%M分%S秒",
        "%Y-%m-%d %H时%M分",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.isoformat()
        except ValueError:
            continue
    return None


def _normalize_account(account: str) -> str:
    """规范化账号格式：去除空格、短横线等分隔符"""
    if not account:
        return ""
    account = str(account).strip()
    # 去除空格、短横线、下划线、星号等
    account = re.sub(r"[\s\-\_\*]+", "", account)
    # 如果账号被部分掩码，保留可见部分
    return account


def _validate_transaction(txn: Dict, index: int) -> Tuple[bool, List[str]]:
    """
    校验单条交易数据

    Args:
        txn: 交易数据
        index: 行号（用于错误提示）

    Returns:
        (是否有效, 错误列表)
    """
    errors = []

    # 必填字段检查
    for field in REQUIRED_FIELDS:
        if not txn.get(field):
            errors.append(f"第{index + 1}行: 缺少必填字段 '{field}'")

    # 金额校验
    amount = txn.get("amount")
    if amount is not None:
        if not isinstance(amount, (int, float)):
            parsed = _parse_amount(amount)
            if parsed is None:
                errors.append(f"第{index + 1}行: 金额格式无效 '{amount}'")
            else:
                txn["amount"] = parsed
        # 戒律 P4: 只有金额是数值类型时才校验范围（避免 TypeError 崩溃）
        if isinstance(txn.get("amount"), (int, float)) and txn["amount"] <= 0:
            errors.append(f"第{index + 1}行: 金额必须大于0")

    # 时间校验
    ts = txn.get("timestamp")
    if ts:
        parsed_ts = _parse_timestamp(ts)
        if parsed_ts is None:
            errors.append(f"第{index + 1}行: 时间格式无效 '{ts}'")
        else:
            txn["timestamp"] = parsed_ts

    # 账号规范化
    for field in ["from_account", "to_account"]:
        if txn.get(field):
            txn[field] = _normalize_account(txn[field])

    return len(errors) == 0, errors


def _read_csv(file_path: str) -> List[Dict]:
    """读取 CSV 文件"""
    import csv
    rows = []
    with open(file_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def _read_excel(file_path: str) -> List[Dict]:
    """读取 Excel 文件"""
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("读取 Excel 需要安装 openpyxl: pip install openpyxl")

    wb = openpyxl.load_workbook(file_path, data_only=True)
    ws = wb.active
    rows = []
    headers = None
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            headers = [str(cell) if cell is not None else f"col_{i}" for i, cell in enumerate(row)]
        else:
            row_dict = {}
            for j, cell in enumerate(row):
                if j < len(headers):
                    row_dict[headers[j]] = cell
            rows.append(row_dict)
    wb.close()
    return rows


def _read_json(file_path: str) -> List[Dict]:
    """读取 JSON 文件"""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    elif isinstance(data, dict) and "transactions" in data:
        return data["transactions"]
    else:
        raise ValueError("JSON 文件格式错误：应为列表或包含 transactions 字段的对象")


def import_transactions(
    file_path: str,
    field_mapping: Optional[Dict[str, str]] = None,
    strict: bool = True,
) -> Dict:
    """
    导入交易数据

    严格遵守戒律 M1: 所有数据来自文件，不臆测缺失字段
    严格遵守戒律 P2: 数据有问题明确报错，不把正常数据当可疑

    Args:
        file_path: 文件路径
        field_mapping: 字段映射 {内部字段名: 实际列名}，为 None 时自动检测
        strict: 严格模式，有任何错误就抛异常；False 时跳过无效行

    Returns:
        {
            "transactions": [...],      # 标准化后的交易列表
            "total": 100,               # 总行数
            "valid": 98,                # 有效行数
            "errors": [...],            # 错误列表
            "mapping": {...},           # 实际使用的字段映射
            "source_format": "csv",     # 文件格式
            "bank_format": "icbc",      # 检测到的银行格式
            "data_quality": {...},      # 数据质量评估
        }
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    file_format = detect_file_format(file_path)
    if file_format == "unknown":
        raise ValueError(f"不支持的文件格式: {os.path.splitext(file_path)[1]}")

    # 读取原始数据
    if file_format == "csv":
        raw_rows = _read_csv(file_path)
    elif file_format == "excel":
        raw_rows = _read_excel(file_path)
    elif file_format == "json":
        raw_rows = _read_json(file_path)
    else:
        raise ValueError(f"不支持的格式: {file_format}")

    if not raw_rows:
        return {
            "transactions": [],
            "total": 0,
            "valid": 0,
            "errors": ["文件为空"],
            "mapping": {},
            "source_format": file_format,
            "bank_format": "unknown",
            "data_quality": _assess_quality([], [], 0, 0),
        }

    # 自动检测银行格式
    columns = list(raw_rows[0].keys())
    bank_format = detect_bank_format(columns)
    bank_name = BANK_FORMAT_TEMPLATES.get(bank_format, {}).get("name", "未知银行")

    # 自动检测或使用指定字段映射
    if field_mapping is None:
        mapping = _auto_detect_mapping(columns)
    else:
        mapping = field_mapping

    # 检查必填字段是否都映射到了
    missing_required = []
    for field in REQUIRED_FIELDS:
        if field not in mapping:
            missing_required.append(field)
    if missing_required and strict:
        raise ValueError(
            f"缺少必填字段映射: {', '.join(missing_required)}。"
            f"请通过 field_mapping 参数指定这些字段的列名。"
        )

    # 标准化数据
    transactions = []
    all_errors = []
    valid_count = 0

    for i, row in enumerate(raw_rows):
        txn = {}
        # 应用字段映射
        for internal_field, source_col in mapping.items():
            txn[internal_field] = row.get(source_col)

        # 补充默认值
        if "transaction_id" not in txn or not txn["transaction_id"]:
            txn["transaction_id"] = f"IMP_{i + 1:06d}"
        if "transaction_type" not in txn or not txn["transaction_type"]:
            txn["transaction_type"] = "transfer"
        if "remark" not in txn:
            txn["remark"] = ""

        # 校验
        is_valid, errors = _validate_transaction(txn, i)
        if is_valid:
            transactions.append(txn)
            valid_count += 1
        else:
            all_errors.extend(errors)
            if not strict:
                continue
            else:
                raise ValueError("; ".join(errors))

    # 数据质量评估
    data_quality = _assess_quality(transactions, all_errors, len(raw_rows), valid_count)

    return {
        "transactions": transactions,
        "total": len(raw_rows),
        "valid": valid_count,
        "errors": all_errors,
        "mapping": mapping,
        "source_format": file_format,
        "bank_format": bank_format,
        "bank_name": bank_name,
        "data_quality": data_quality,
    }


def _assess_quality(transactions: List[Dict], errors: List[str], total: int, valid: int) -> Dict:
    """
    数据质量评估

    Returns:
        {
            "score": 0.95,              # 质量分数 0-1
            "grade": "A",               # 质量等级 A/B/C/D
            "completeness": 0.98,       # 完整性（必填字段完整率）
            "accuracy": 0.95,           # 准确性（格式正确率）
            "timeliness": 0.85,         # 时效性（近期交易占比）
            "summary": "...",           # 质量摘要
            "issues": [...],            # 问题列表
        }
    """
    issues = []
    score = 1.0

    # 完整性评估（必填字段完整率）
    if total > 0:
        completeness = valid / total
        score *= completeness
        if completeness < 0.9:
            issues.append(f"数据完整性不足 ({completeness:.1%})")
        elif completeness < 0.95:
            issues.append(f"数据完整性一般 ({completeness:.1%})")
    else:
        completeness = 0.0
        score = 0.0
        issues.append("无数据")

    # 准确性评估（基于错误数量）
    error_ratio = len(errors) / max(total, 1)
    accuracy = 1.0 - error_ratio
    score *= 0.5 + accuracy * 0.5  # 准确性权重0.5
    if error_ratio > 0.1:
        issues.append(f"数据准确性较差（错误率 {error_ratio:.1%}）")

    # 时效性评估（最近30天交易占比）
    if transactions:
        recent_count = 0
        now = datetime.now()
        for txn in transactions:
            ts = txn.get("timestamp")
            if ts:
                try:
                    txn_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    days_diff = (now - txn_dt).days
                    if days_diff <= 30:
                        recent_count += 1
                except (ValueError, TypeError):
                    pass
        timeliness = recent_count / len(transactions) if transactions else 0.0
        score *= 0.3 + timeliness * 0.7  # 时效性权重0.3
    else:
        timeliness = 0.0

    # 异常值检测（金额异常大或小）
    if transactions:
        amounts = [t["amount"] for t in transactions if isinstance(t.get("amount"), (int, float))]
        if amounts:
            avg_amount = sum(amounts) / len(amounts)
            std_amount = (sum((a - avg_amount) ** 2 for a in amounts) / len(amounts)) ** 0.5
            if std_amount > 0:
                outliers = [a for a in amounts if abs(a - avg_amount) > 3 * std_amount]
                if len(outliers) > len(amounts) * 0.1:
                    issues.append(f"检测到较多金额异常值（占比 {len(outliers)/len(amounts):.1%}）")

    # 质量等级
    if score >= 0.9:
        grade = "A"
    elif score >= 0.75:
        grade = "B"
    elif score >= 0.6:
        grade = "C"
    else:
        grade = "D"

    # 质量摘要
    if grade == "A":
        summary = "数据质量优秀，可直接用于分析"
    elif grade == "B":
        summary = "数据质量良好，建议检查少量问题"
    elif grade == "C":
        summary = "数据质量一般，建议清洗后再使用"
    else:
        summary = "数据质量较差，需进行数据清洗"

    return {
        "score": round(score, 4),
        "grade": grade,
        "completeness": round(completeness, 4),
        "accuracy": round(accuracy, 4),
        "timeliness": round(timeliness, 4),
        "summary": summary,
        "issues": issues,
    }
