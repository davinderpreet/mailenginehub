-- Self-Learning Email Intelligence Layer — Schema Migration
-- Run on VPS: sqlite3 /var/www/mailengine/email_platform.db < this_file.sql

-- New tables
CREATE TABLE IF NOT EXISTS outcome_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email_type      VARCHAR(20) NOT NULL,
    email_id        INTEGER NOT NULL,
    contact_id      INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
    template_id     INTEGER DEFAULT 0,
    action_type     VARCHAR(30) DEFAULT '',
    segment         VARCHAR(30) DEFAULT '',
    opened          BOOLEAN DEFAULT 0,
    clicked         BOOLEAN DEFAULT 0,
    purchased       BOOLEAN DEFAULT 0,
    unsubscribed    BOOLEAN DEFAULT 0,
    revenue         REAL DEFAULT 0.0,
    hours_to_open   REAL,
    hours_to_purchase REAL,
    sent_at         DATETIME,
    subject_line    VARCHAR(200) DEFAULT '',
    send_gap_hours  REAL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(email_type, email_id)
);
CREATE INDEX IF NOT EXISTS idx_outcome_log_contact ON outcome_log(contact_id);
CREATE INDEX IF NOT EXISTS idx_outcome_log_template ON outcome_log(template_id);
CREATE INDEX IF NOT EXISTS idx_outcome_log_sent_at ON outcome_log(sent_at);
CREATE INDEX IF NOT EXISTS idx_outcome_log_email_type ON outcome_log(email_type);

CREATE TABLE IF NOT EXISTS action_performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type     VARCHAR(30) NOT NULL,
    segment         VARCHAR(30) NOT NULL,
    sample_size     INTEGER DEFAULT 0,
    open_rate       REAL DEFAULT 0.0,
    click_rate      REAL DEFAULT 0.0,
    conversion_rate REAL DEFAULT 0.0,
    revenue_per_send REAL DEFAULT 0.0,
    avg_score       REAL DEFAULT 0.0,
    last_computed   DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(action_type, segment)
);

CREATE TABLE IF NOT EXISTS template_segment_performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id     INTEGER REFERENCES email_templates(id),
    segment         VARCHAR(30) NOT NULL,
    sample_size     INTEGER DEFAULT 0,
    open_rate       REAL DEFAULT 0.0,
    click_rate      REAL DEFAULT 0.0,
    conversion_rate REAL DEFAULT 0.0,
    revenue_per_send REAL DEFAULT 0.0,
    last_computed   DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(template_id, segment)
);

CREATE TABLE IF NOT EXISTS model_weights (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recency_weight  REAL NOT NULL,
    frequency_weight REAL NOT NULL,
    monetary_weight REAL NOT NULL,
    evaluation_score REAL,
    sample_size     INTEGER,
    phase           VARCHAR(20) DEFAULT '',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS learning_config (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    key             VARCHAR(100) UNIQUE NOT NULL,
    value           VARCHAR(500) DEFAULT '',
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Seed baseline model weights (idempotent — skips if any row exists)
INSERT OR IGNORE INTO model_weights (id, recency_weight, frequency_weight, monetary_weight, phase)
VALUES (1, 0.40, 0.40, 0.20, 'baseline');

-- Seed learning config defaults
INSERT OR IGNORE INTO learning_config (key, value) VALUES ('learning_enabled', 'true');
INSERT OR IGNORE INTO learning_config (key, value) VALUES ('learning_start_date', datetime('now'));

-- Extend existing tables (ALTER TABLE is safe — adds columns with defaults)
-- TemplatePerformance extensions
ALTER TABLE template_performance ADD COLUMN revenue_total REAL DEFAULT 0.0;
ALTER TABLE template_performance ADD COLUMN revenue_per_send REAL DEFAULT 0.0;
ALTER TABLE template_performance ADD COLUMN conversion_rate REAL DEFAULT 0.0;
ALTER TABLE template_performance ADD COLUMN sample_size INTEGER DEFAULT 0;
ALTER TABLE template_performance ADD COLUMN learning_flag BOOLEAN DEFAULT 1;

-- ContactScore extensions
ALTER TABLE contact_scores ADD COLUMN optimal_gap_hours REAL DEFAULT 48.0;
ALTER TABLE contact_scores ADD COLUMN sunset_score INTEGER DEFAULT 0;
ALTER TABLE contact_scores ADD COLUMN sunset_executed BOOLEAN DEFAULT 0;
ALTER TABLE contact_scores ADD COLUMN sunset_executed_at DATETIME;

-- FlowEmail extensions (click tracking prerequisite)
ALTER TABLE flow_emails ADD COLUMN clicked BOOLEAN DEFAULT 0;
ALTER TABLE flow_emails ADD COLUMN clicked_at DATETIME;
