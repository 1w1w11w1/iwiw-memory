from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .config import DB_PATH, ensure_data_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT 'default',
    title TEXT,
    source TEXT NOT NULL DEFAULT 'gui',
    scope TEXT NOT NULL DEFAULT 'project',
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    turn_count INTEGER NOT NULL DEFAULT 0,
    rolling_summary TEXT NOT NULL DEFAULT '',
    last_consolidated_turn INTEGER NOT NULL DEFAULT -1,
    cycle_count INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET DEFAULT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_idx INTEGER NOT NULL,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    private INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_ingest_outbox (
    message_id      INTEGER PRIMARY KEY,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, turn_idx);
CREATE INDEX IF NOT EXISTS idx_sessions_source_updated ON sessions(source, updated_at);

CREATE TABLE IF NOT EXISTS memory_session_tombstones (
    session_id TEXT PRIMARY KEY,
    deleted_at TEXT NOT NULL,
    version_id TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    role UNINDEXED,
    session_id UNINDEXED,
    turn_idx UNINDEXED,
    ts UNINDEXED,
    content='messages',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, role, session_id, turn_idx, ts)
    VALUES (new.id, new.content, new.role, new.session_id, new.turn_idx, new.ts);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, role, session_id, turn_idx, ts)
    VALUES ('delete', old.id, old.content, old.role, old.session_id, old.turn_idx, old.ts);
END;

CREATE TABLE IF NOT EXISTS cycle_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    cycle_no INTEGER NOT NULL,
    idx_from INTEGER NOT NULL,
    idx_to INTEGER NOT NULL,
    summary TEXT NOT NULL,
    memory_result TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_audit (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    target_id TEXT,
    target_type TEXT NOT NULL DEFAULT 'session_event',
    source_session_id TEXT,
    reason TEXT,
    backup_path TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    details TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS session_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_session_versions_session
    ON session_versions(session_id, id);

CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    intent TEXT NOT NULL DEFAULT 'analysis',
    path TEXT NOT NULL DEFAULT 'light',
    reason TEXT NOT NULL DEFAULT '',
    model_role TEXT NOT NULL DEFAULT 'chat',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    context_sections TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    run_id TEXT,
    turn_idx INTEGER,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    risk_level TEXT NOT NULL DEFAULT 'low',
    reversible INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'recorded',
    rollback_ref TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS migration_flags (
    key TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

"""


REQUIRED_SCHEMA_COLUMNS = {
    "memory_ingest_outbox": {
        "message_id", "attempts", "last_error", "created_at", "updated_at",
    },
    "memory_session_tombstones": {
        "session_id", "deleted_at", "version_id",
    },
    "memory_audit": {
        "id", "ts", "action", "target_id", "target_type", "source_session_id",
        "reason", "backup_path", "status", "details",
    },
    "session_versions": {
        "id", "session_id", "snapshot", "saved_at", "reason",
    },
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def default_project() -> tuple[str, str, str]:
    path = str(Path.cwd())
    name = Path.cwd().name or "IwIw"
    return "default", name, path


def _initialize_defaults(conn: sqlite3.Connection) -> None:
    pid, name, path = default_project()
    now = now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO projects (id, name, path, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (pid, name, path, now, now),
    )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_schema(conn: sqlite3.Connection) -> None:
    _initialize_defaults(conn)
    project_id = default_project()[0]

    session_columns = _columns(conn, "sessions")
    if "project_id" not in session_columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE sessions SET project_id = ? WHERE project_id = ''", (project_id,))
    if "pinned" not in session_columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
    if "scope" not in session_columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN scope TEXT NOT NULL DEFAULT 'project'")

    if "metadata" not in _columns(conn, "messages"):
        conn.execute("ALTER TABLE messages ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")

    audit_columns = _columns(conn, "memory_audit")
    if "target_id" not in audit_columns:
        conn.execute("ALTER TABLE memory_audit ADD COLUMN target_id TEXT")
        if "target_slug" in audit_columns:
            conn.execute("UPDATE memory_audit SET target_id = target_slug WHERE target_id IS NULL")
    if "target_type" not in audit_columns:
        conn.execute("ALTER TABLE memory_audit ADD COLUMN target_type TEXT NOT NULL DEFAULT 'session_event'")
    if "status" not in audit_columns:
        conn.execute("ALTER TABLE memory_audit ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'")

    flag = conn.execute(
        "SELECT 1 FROM migration_flags WHERE key = ?",
        ("classify_existing_gui_sessions_as_chat",),
    ).fetchone()
    if not flag:
        conn.execute("UPDATE sessions SET scope = 'chat' WHERE source = 'gui'")
        conn.execute(
            "INSERT INTO migration_flags (key, applied_at) VALUES (?, ?)",
            ("classify_existing_gui_sessions_as_chat", now_iso()),
        )


def _validate_schema(conn: sqlite3.Connection) -> None:
    missing: list[str] = []
    for table, required_columns in REQUIRED_SCHEMA_COLUMNS.items():
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        actual_columns = {row["name"] for row in rows}
        for column in sorted(required_columns - actual_columns):
            missing.append(f"{table}.{column}")
    if missing:
        raise RuntimeError(
            "Session database schema does not match the new memory system. "
            "Recreate selfecho_data/sessions.db for this early-phase refactor; "
            "no legacy compatibility path is available. Missing columns: "
            + ", ".join(missing)
        )


def connect() -> sqlite3.Connection:
    ensure_data_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        _migrate_schema(conn)
        _validate_schema(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project_updated ON sessions(project_id, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_scope_updated ON sessions(scope, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_session_started ON agent_runs(session_id, started_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_events_run_ts ON agent_events(run_id, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_timeline_session_ts ON agent_timeline(session_id, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_timeline_run ON agent_timeline(run_id)")
        conn.commit()
    except Exception:
        conn.close()
        raise
    return conn
