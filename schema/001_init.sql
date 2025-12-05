PRAGMA foreign_keys = ON;

-- Prompt pool
CREATE TABLE IF NOT EXISTS prompt_pool (
    prompt_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    text         TEXT NOT NULL,
    source       TEXT,
    label_schema_version TEXT,
    is_gold      INTEGER DEFAULT 0,
    tags         TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now'))
);

-- Prompt usage mapping
CREATE TABLE IF NOT EXISTS prompt_requests (
    request_id TEXT PRIMARY KEY,
    prompt_id  INTEGER,
    source     TEXT,
    used_at    TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (prompt_id) REFERENCES prompt_pool(prompt_id)
);

-- MLC detection log
CREATE TABLE IF NOT EXISTS mlc_events (
    request_id        TEXT PRIMARY KEY,
    prompt_id         INTEGER,

    source            TEXT,
    text              TEXT NOT NULL,

    detector_version  TEXT,
    label_schema_version TEXT,

    risk_score        REAL NOT NULL,
    clean_prob        REAL NOT NULL,
    best_risk_label   TEXT,
    decision          TEXT NOT NULL,

    label_probs       TEXT NOT NULL,
    risk_labels       TEXT NOT NULL,
    risk_threshold    REAL NOT NULL,

    is_baseline       INTEGER DEFAULT 0,
    is_gold           INTEGER DEFAULT 0,
    tee_to_baseline   INTEGER DEFAULT 0,

    error_json        TEXT,
    created_at        TEXT DEFAULT (datetime('now')),

    FOREIGN KEY (prompt_id) REFERENCES prompt_pool(prompt_id)
);

CREATE INDEX IF NOT EXISTS idx_mlc_events_decision
    ON mlc_events(decision);

CREATE INDEX IF NOT EXISTS idx_mlc_events_risk_score
    ON mlc_events(risk_score);

-- LLM outputs
CREATE TABLE IF NOT EXISTS llm_outputs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id        TEXT NOT NULL,
    prompt_id         INTEGER,

    pipeline          TEXT NOT NULL,
    model_role        TEXT NOT NULL,
    model_name        TEXT NOT NULL,

    prompt_text       TEXT NOT NULL,
    llm_output_text   TEXT NOT NULL,
    llm_output_json   TEXT,

    safety_decision   TEXT,
    safety_risk_score REAL,
    safety_best_label TEXT,

    latency_ms        INTEGER,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,

    created_at        TEXT DEFAULT (datetime('now')),

    FOREIGN KEY (prompt_id) REFERENCES prompt_pool(prompt_id)
);

CREATE INDEX IF NOT EXISTS idx_llm_outputs_request
    ON llm_outputs(request_id);

CREATE INDEX IF NOT EXISTS idx_llm_outputs_pipeline_role
    ON llm_outputs(pipeline, model_role);
