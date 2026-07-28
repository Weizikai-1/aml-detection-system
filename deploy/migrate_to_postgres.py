"""
数据迁移脚本：JSON → PostgreSQL

将现有 JSON 文件中的历史数据迁移到 PostgreSQL 数据库。
符合业务戒律 M4: 迁移过程完整可追溯，数据不丢失。

使用方式:
    python deploy/migrate_to_postgres.py                  # 迁移所有数据
    python deploy/migrate_to_postgres.py --dry-run        # 仅检查，不实际写入
    python deploy/migrate_to_postgres.py --verify         # 迁移后验证数据完整性
    python deploy/migrate_to_postgres.py --only audit     # 仅迁移审计日志

迁移的数据源:
    1. data/history/*.json        → analysis_history 表
    2. data/profiles/*.json       → accounts 表
    3. data/alerts/index.json     → alert_history 表
    4. data/audit/audit_*.jsonl   → audit_logs 表（含哈希链完整性保护，M11修复）

安全措施:
    - 迁移前先备份 JSON 文件
    - 每条记录独立事务，单条失败不影响其他
    - 验证阶段对比源数据与目标数据条数
    - 审计日志保留哈希链字段，迁移后完整性可验证
"""
import os
import sys
import json
import argparse
import shutil
from datetime import datetime
from typing import Dict, List, Any

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATA_DIR, PROFILES_DIR, HISTORY_DIR


def migrate_history(session, history_dir: str, dry_run: bool = False) -> Dict[str, int]:
    """
    迁移分析历史记录

    Returns:
        {"total": 总数, "migrated": 成功数, "failed": 失败数}
    """
    from api.models import AnalysisHistory

    stats = {"total": 0, "migrated": 0, "failed": 0}

    # 读取索引文件
    index_path = os.path.join(history_dir, "index.json")
    if not os.path.exists(index_path):
        print("  [历史] 未找到 index.json，跳过")
        return stats

    # 遍历所有历史记录文件
    for filename in os.listdir(history_dir):
        if not filename.endswith(".json") or filename == "index.json":
            continue

        filepath = os.path.join(history_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [历史] 读取失败 {filename}: {e}")
            stats["failed"] += 1
            stats["total"] += 1
            continue

        execution_id = record.get("execution_id", "")
        if not execution_id:
            print(f"  [历史] 跳过无 execution_id 的记录: {filename}")
            stats["failed"] += 1
            stats["total"] += 1
            continue

        stats["total"] += 1

        if dry_run:
            print(f"  [DRY-RUN] 将迁移历史记录: {execution_id}")
            stats["migrated"] += 1
            continue

        try:
            # 检查是否已存在
            existing = session.query(AnalysisHistory).filter_by(
                execution_id=execution_id
            ).first()
            if existing:
                print(f"  [历史] 已存在，跳过: {execution_id}")
                stats["migrated"] += 1
                continue

            # 创建记录
            row = AnalysisHistory(
                execution_id=execution_id,
                timestamp=datetime.fromisoformat(record["timestamp"]) if record.get("timestamp") else datetime.now(),
                transactions_count=record.get("transactions_count", 0),
                rule_hit_count=record.get("rule_hit_count", 0),
                str_reports_count=record.get("report_count", 0),
                compliance_score=0,  # 旧数据可能没有此字段
                total_processing_time_sec=record.get("duration_seconds", 0),
                value_metrics=record.get("value_metrics", {}),
                config_snapshot=record.get("config_snapshot", {}),
                _seq=record.get("_seq", 0),
            )
            session.add(row)
            session.commit()
            stats["migrated"] += 1
        except Exception as e:
            session.rollback()
            print(f"  [历史] 写入失败 {execution_id}: {e}")
            stats["failed"] += 1

    return stats


def migrate_profiles(session, profiles_dir: str, dry_run: bool = False) -> Dict[str, int]:
    """
    迁移账户画像

    Returns:
        {"total": 总数, "migrated": 成功数, "failed": 失败数}
    """
    from api.models import Account

    stats = {"total": 0, "migrated": 0, "failed": 0}

    profile_path = os.path.join(profiles_dir, "account_profiles.json")
    if not os.path.exists(profile_path):
        print("  [画像] 未找到 account_profiles.json，跳过")
        return stats

    try:
        with open(profile_path, "r", encoding="utf-8") as f:
            profiles_data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [画像] 读取失败: {e}")
        return stats

    for account_id, profile_data in profiles_data.items():
        if not isinstance(profile_data, dict):
            continue

        stats["total"] += 1

        if dry_run:
            print(f"  [DRY-RUN] 将迁移画像: {account_id}")
            stats["migrated"] += 1
            continue

        try:
            existing = session.query(Account).filter_by(
                account_id=account_id
            ).first()
            if existing:
                stats["migrated"] += 1
                continue

            row = Account(
                account_id=account_id,
                risk_multiplier=profile_data.get("risk_multiplier", 1.0),
                suspicious_count=profile_data.get("total_suspicious_hits", 0),
                false_positive_count=profile_data.get("false_positive_count", 0),
                false_negative_count=profile_data.get("false_negative_count", 0),
                last_suspicious_time=datetime.fromisoformat(profile_data["last_analysis_time"]) if profile_data.get("last_analysis_time") else None,
                metadata_json={
                    "first_seen": profile_data.get("first_seen", ""),
                    "last_seen": profile_data.get("last_seen", ""),
                    "total_transactions": profile_data.get("total_transactions", 0),
                    "suspicious_patterns": profile_data.get("suspicious_patterns", {}),
                    "highest_risk_score": profile_data.get("highest_risk_score", 0),
                    "avg_risk_score": profile_data.get("avg_risk_score", 0),
                    "risk_trend": profile_data.get("risk_trend", "stable"),
                    "notes": profile_data.get("notes", []),
                },
            )
            session.add(row)
            session.commit()
            stats["migrated"] += 1
        except Exception as e:
            session.rollback()
            print(f"  [画像] 写入失败 {account_id}: {e}")
            stats["failed"] += 1

    return stats


def migrate_alerts(session, alerts_dir: str, dry_run: bool = False) -> Dict[str, int]:
    """
    迁移告警历史

    Returns:
        {"total": 总数, "migrated": 成功数, "failed": 失败数}
    """
    from api.models import AlertHistoryRecord

    stats = {"total": 0, "migrated": 0, "failed": 0}

    for filename in os.listdir(alerts_dir):
        if not filename.endswith(".json") or filename == "index.json":
            continue

        filepath = os.path.join(alerts_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                alert_data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [告警] 读取失败 {filename}: {e}")
            stats["failed"] += 1
            stats["total"] += 1
            continue

        alert_id = alert_data.get("alert_id", "")
        if not alert_id:
            print(f"  [告警] 跳过无 alert_id 的记录: {filename}")
            stats["failed"] += 1
            stats["total"] += 1
            continue

        stats["total"] += 1

        if dry_run:
            print(f"  [DRY-RUN] 将迁移告警: {alert_id}")
            stats["migrated"] += 1
            continue

        try:
            existing = session.query(AlertHistoryRecord).filter_by(
                alert_id=alert_id
            ).first()
            if existing:
                stats["migrated"] += 1
                continue

            triggered_at = alert_data.get("triggered_at", "")
            row = AlertHistoryRecord(
                alert_id=alert_id,
                rule_id=alert_data.get("rule_id", ""),
                severity=alert_data.get("severity", "info"),
                category=alert_data.get("category", "system_health"),
                message=alert_data.get("message", ""),
                triggered_at=datetime.fromisoformat(triggered_at) if triggered_at else datetime.now(),
                metadata_json=alert_data.get("context", {}),
            )
            session.add(row)
            session.commit()
            stats["migrated"] += 1
        except Exception as e:
            session.rollback()
            print(f"  [告警] 写入失败 {alert_id}: {e}")
            stats["failed"] += 1

    return stats


def migrate_audit_logs(session, audit_dir: str, dry_run: bool = False) -> Dict[str, int]:
    """
    迁移审计日志（含哈希链完整性保护，M11修复）

    Returns:
        {"total": 总数, "migrated": 成功数, "failed": 失败数}
    """
    from api.models import AuditLog

    stats = {"total": 0, "migrated": 0, "failed": 0}

    import glob
    log_pattern = os.path.join(audit_dir, "audit_*.jsonl")

    for filepath in sorted(glob.glob(log_pattern)):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        entry_data = json.loads(line)
                    except json.JSONDecodeError as e:
                        print(f"  [审计] JSON解析失败: {e}")
                        stats["failed"] += 1
                        stats["total"] += 1
                        continue

                    entry_id = entry_data.get("entry_id", "")
                    if not entry_id:
                        stats["failed"] += 1
                        stats["total"] += 1
                        continue

                    stats["total"] += 1

                    if dry_run:
                        print(f"  [DRY-RUN] 将迁移审计日志: {entry_id}")
                        stats["migrated"] += 1
                        continue

                    try:
                        existing = session.query(AuditLog).filter_by(
                            user_id=entry_id
                        ).first()
                        if existing:
                            stats["migrated"] += 1
                            continue

                        entry_time = entry_data.get("timestamp", "")
                        row = AuditLog(
                            user_id=entry_id,
                            action=entry_data.get("action", ""),
                            resource_type=entry_data.get("operation_type", ""),
                            resource_id=entry_data.get("resource_id", ""),
                            ip_address=entry_data.get("ip_address", ""),
                            timestamp=datetime.fromisoformat(entry_time) if entry_time else datetime.now(),
                            details=entry_data.get("details", {}),
                            prev_hash=entry_data.get("prev_hash", "GENESIS"),
                            current_hash=entry_data.get("current_hash", ""),
                        )
                        session.add(row)
                        session.commit()
                        stats["migrated"] += 1
                    except Exception as e:
                        session.rollback()
                        print(f"  [审计] 写入失败 {entry_id}: {e}")
                        stats["failed"] += 1

        except OSError as e:
            print(f"  [审计] 文件读取失败 {filepath}: {e}")

    return stats


def verify_migration(session, history_dir: str, profiles_dir: str, alerts_dir: str, audit_dir: str) -> bool:
    """
    验证迁移数据完整性

    对比 JSON 文件条数与数据库记录条数

    Returns:
        True=验证通过, False=验证失败
    """
    from api.models import AnalysisHistory, Account, AlertHistoryRecord, AuditLog

    all_ok = True

    # 验证历史记录
    json_history_count = len([f for f in os.listdir(history_dir) if f.endswith(".json") and f != "index.json"])
    db_history_count = session.query(AnalysisHistory).count()
    print(f"  [验证] 历史记录: JSON={json_history_count}, DB={db_history_count}", end="")
    if json_history_count == db_history_count:
        print(" ✅")
    else:
        print(" ❌ 不一致")
        all_ok = False

    # 验证画像
    profile_path = os.path.join(profiles_dir, "account_profiles.json")
    json_profile_count = 0
    if os.path.exists(profile_path):
        with open(profile_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        json_profile_count = len(data)
    db_profile_count = session.query(Account).count()
    print(f"  [验证] 账户画像: JSON={json_profile_count}, DB={db_profile_count}", end="")
    if json_profile_count == db_profile_count:
        print(" ✅")
    else:
        print(" ❌ 不一致")
        all_ok = False

    # 验证告警
    json_alert_count = len([f for f in os.listdir(alerts_dir) if f.endswith(".json") and f != "index.json"])
    db_alert_count = session.query(AlertHistoryRecord).count()
    print(f"  [验证] 告警记录: JSON={json_alert_count}, DB={db_alert_count}", end="")
    if json_alert_count == db_alert_count:
        print(" ✅")
    else:
        print(" ❌ 不一致")
        all_ok = False

    # 验证审计日志
    import glob
    json_audit_count = 0
    log_pattern = os.path.join(audit_dir, "audit_*.jsonl")
    for filepath in glob.glob(log_pattern):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        json_audit_count += 1
        except OSError:
            pass
    db_audit_count = session.query(AuditLog).count()
    print(f"  [验证] 审计日志: JSONL={json_audit_count}, DB={db_audit_count}", end="")
    if json_audit_count == db_audit_count:
        print(" ✅")
    else:
        print(" ❌ 不一致")
        all_ok = False

    return all_ok


def main():
    parser = argparse.ArgumentParser(description="AML-Agent 数据迁移工具")
    parser.add_argument("--dry-run", action="store_true", help="仅检查，不实际写入")
    parser.add_argument("--verify", action="store_true", help="验证迁移数据完整性")
    parser.add_argument("--database-url", type=str, help="PostgreSQL 连接字符串")
    parser.add_argument("--only", type=str, choices=["history", "profiles", "alerts", "audit"], help="仅迁移指定类型")
    args = parser.parse_args()

    print("=" * 60)
    print("  AML-Agent 数据迁移工具 (JSON → PostgreSQL)")
    print("=" * 60)

    # 初始化数据库连接
    from api.database import init_db, get_session

    db_url = args.database_url or os.getenv("DATABASE_URL", "")
    if not db_url:
        print("错误: 未指定 DATABASE_URL，无法连接 PostgreSQL")
        print("请设置环境变量或使用 --database-url 参数")
        sys.exit(1)

    connected = init_db(db_url)
    if not connected:
        print("错误: PostgreSQL 连接失败")
        sys.exit(1)

    # 创建表结构
    from api.database import create_tables
    create_tables()

    session = get_session()
    if not session:
        print("错误: 无法创建数据库会话")
        sys.exit(1)

    # 目录路径
    history_dir = HISTORY_DIR
    profiles_dir = PROFILES_DIR
    alerts_dir = os.path.join(DATA_DIR, "alerts")
    audit_dir = os.path.join(DATA_DIR, "audit")

    # 仅验证模式
    if args.verify:
        print("\n[验证模式]")
        ok = verify_migration(session, history_dir, profiles_dir, alerts_dir, audit_dir)
        if ok:
            print("\n✅ 验证通过：数据完整")
        else:
            print("\n❌ 验证失败：数据不一致")
            sys.exit(1)
        return

    # 备份 JSON 数据（戒律 M4: 迁移前备份）
    if not args.dry_run:
        backup_dir = os.path.join(DATA_DIR, "backup_before_migration")
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
            print(f"\n[备份] 备份目录: {backup_dir}")
            for src_dir, name in [
                (history_dir, "history"),
                (profiles_dir, "profiles"),
                (alerts_dir, "alerts"),
                (audit_dir, "audit"),
            ]:
                if os.path.exists(src_dir):
                    dst = os.path.join(backup_dir, name)
                    shutil.copytree(src_dir, dst, dirs_exist_ok=True)
                    print(f"  [备份] {name} → {dst}")

    # 执行迁移
    print(f"\n[迁移] {'DRY-RUN 模式' if args.dry_run else '实际迁移'}")
    print("-" * 40)

    h_stats = {"total": 0, "migrated": 0, "failed": 0}
    p_stats = {"total": 0, "migrated": 0, "failed": 0}
    a_stats = {"total": 0, "migrated": 0, "failed": 0}
    audit_stats = {"total": 0, "migrated": 0, "failed": 0}

    if args.only in (None, "history"):
        h_stats = migrate_history(session, history_dir, dry_run=args.dry_run)
        print(f"  历史记录: 总计={h_stats['total']}, 成功={h_stats['migrated']}, 失败={h_stats['failed']}")

    if args.only in (None, "profiles"):
        p_stats = migrate_profiles(session, profiles_dir, dry_run=args.dry_run)
        print(f"  账户画像: 总计={p_stats['total']}, 成功={p_stats['migrated']}, 失败={p_stats['failed']}")

    if args.only in (None, "alerts"):
        a_stats = migrate_alerts(session, alerts_dir, dry_run=args.dry_run)
        print(f"  告警记录: 总计={a_stats['total']}, 成功={a_stats['migrated']}, 失败={a_stats['failed']}")

    if args.only in (None, "audit"):
        audit_stats = migrate_audit_logs(session, audit_dir, dry_run=args.dry_run)
        print(f"  审计日志: 总计={audit_stats['total']}, 成功={audit_stats['migrated']}, 失败={audit_stats['failed']}")

    total = h_stats["total"] + p_stats["total"] + a_stats["total"] + audit_stats["total"]
    migrated = h_stats["migrated"] + p_stats["migrated"] + a_stats["migrated"] + audit_stats["migrated"]
    failed = h_stats["failed"] + p_stats["failed"] + a_stats["failed"] + audit_stats["failed"]

    print("-" * 40)
    print(f"  总计: {total} 条, 成功: {migrated} 条, 失败: {failed} 条")

    if failed > 0 and not args.dry_run:
        print(f"\n⚠️ 有 {failed} 条记录迁移失败，请检查日志")

    # 迁移后验证
    if not args.dry_run and migrated > 0:
        print("\n[验证] 检查数据完整性...")
        ok = verify_migration(session, history_dir, profiles_dir, alerts_dir, audit_dir)
        if ok:
            print("\n✅ 迁移完成，数据完整性验证通过")
        else:
            print("\n⚠️ 迁移完成，但数据完整性验证发现问题")

    session.close()


if __name__ == "__main__":
    main()