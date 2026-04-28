-- Schema for Multicoders Tracker
-- Using SQLite for deterministic state tracking

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL, -- 'pending', 'in_progress', 'completed', 'failed', 'needs_human'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    payload TEXT -- JSON with task description and requirements
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    author TEXT NOT NULL, -- 'claude', 'gemini', 'codex'
    content TEXT NOT NULL, -- The actual code or output
    passed_filter BOOLEAN DEFAULT 0, -- Result of objective filter (linter/compile)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    judge TEXT NOT NULL, -- 'claude', 'gemini', 'codex'
    artifact_id INTEGER NOT NULL,
    vote TEXT NOT NULL, -- 'approve', 'reject'
    reasoning TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
);
