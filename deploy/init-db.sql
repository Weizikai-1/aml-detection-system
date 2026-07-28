-- AML-Agent 数据库初始化脚本
-- 符合业务戒律 M4: 证据链完整可追溯

-- ===== 创建扩展 =====
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ===== 账户画像表 =====
CREATE TABLE IF NOT EXISTS accounts (
    account_id VARCHAR(50) PRIMARY KEY,
    risk_multiplier DECIMAL(10, 4) DEFAULT 1.0,
    suspicious_count INTEGER DEFAULT 0,
    false_positive_count INTEGER DEFAULT 0,
    false_negative_count INTEGER DEFAULT 0,
    last_suspicious_time TIMESTAMP,
    last_feedback_time TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB
);

COMMENT ON TABLE accounts IS '账户风险画像表（符合 M1: 使用真实数据）';

-- ===== 分析历史表 =====
CREATE TABLE IF NOT EXISTS analysis_history (
    execution_id VARCHAR(20) PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    transactions_count INTEGER,
    rule_hit_count INTEGER,
    str_reports_count INTEGER,
    compliance_score DECIMAL(5, 2),
    total_processing_time_sec DECIMAL(10, 3),
    value_metrics JSONB,
    config_snapshot JSONB,
    _seq INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_history_timestamp ON analysis_history(timestamp DESC);
CREATE INDEX idx_history_seq ON analysis_history(_seq);

COMMENT ON TABLE analysis_history IS '分析历史记录表（符合 M4: 完整追溯）';

-- ===== 评估结果表 =====
CREATE TABLE IF NOT EXISTS evaluation_results (
    eval_id VARCHAR(20) PRIMARY KEY,
    execution_id VARCHAR(20) REFERENCES analysis_history(execution_id),
    ground_truth_name VARCHAR(100),
    precision_score DECIMAL(5, 4),
    recall_score DECIMAL(5, 4),
    f1_score DECIMAL(5, 4),
    tp INTEGER,
    fp INTEGER,
    tn INTEGER,
    fn INTEGER,
    scan_results JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_eval_execution ON evaluation_results(execution_id);

COMMENT ON TABLE evaluation_results IS '评估结果表（符合 P1: 不遗漏高风险交易）';

-- ===== 告警历史表 =====
CREATE TABLE IF NOT EXISTS alert_history (
    alert_id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4(),
    rule_id VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    category VARCHAR(50),
    message TEXT,
    triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    acknowledged_at TIMESTAMP,
    acknowledged_by VARCHAR(50),
    metadata JSONB,
    _seq SERIAL
);

CREATE INDEX idx_alert_triggered ON alert_history(triggered_at DESC);
CREATE INDEX idx_alert_rule ON alert_history(rule_id);
CREATE INDEX idx_alert_seq ON alert_history(_seq);

COMMENT ON TABLE alert_history IS '告警历史表（符合 M4: 可追溯）';

-- ===== 用户认证表 =====
CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4(),
    username VARCHAR(50) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    email VARCHAR(100) UNIQUE,
    role VARCHAR(20) DEFAULT 'analyst',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP
);

CREATE INDEX idx_users_username ON users(username);

COMMENT ON TABLE users IS '用户认证表';

-- ===== 审计日志表（含哈希链完整性保护，M11修复） =====
CREATE TABLE IF NOT EXISTS audit_logs (
    log_id SERIAL PRIMARY KEY,
    user_id VARCHAR(36),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50),
    resource_id VARCHAR(100),
    ip_address VARCHAR(50),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    details JSONB,
    prev_hash VARCHAR(64) NOT NULL DEFAULT 'GENESIS',
    current_hash VARCHAR(64) NOT NULL DEFAULT ''
);

CREATE INDEX idx_audit_timestamp ON audit_logs(timestamp DESC);
CREATE INDEX idx_audit_user ON audit_logs(user_id);
CREATE INDEX idx_audit_action ON audit_logs(action);

COMMENT ON TABLE audit_logs IS '审计日志表（符合 M4: 完整记录，M11: 哈希链防篡改）';

-- ===== 触发器：自动更新 updated_at =====
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_accounts_updated
BEFORE UPDATE ON accounts
FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ===== 初始数据 =====
-- 不预置默认用户，通过环境变量或首次启动脚本创建管理员
-- 避免硬编码密码（符合安全审计 H4 修复要求）