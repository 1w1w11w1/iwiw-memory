"""
SQLite storage for the memory system.

Tables:
- memory_records:         raw MemoryEnvelope (original materials, no pre-judgment)
- memory_chunks:          vector chunks for semantic search
- tendency_observations:  raw tendency signals before compilation
- tendency_profiles:      compiled behavior profiles
- memory_versions:        snapshots for rollback
- memory_audit:           audit log for all mutations

Architecture (ref):
  全量记忆库不对材料做预先价值判断；是否进入上下文由检索得分、作用域和预算决定。
  倾向 profile 承担行为默认值，不承担事实归档、事实检索或召回排序。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .config import MEMORY_DB_PATH
from .scopes import AGENT_GLOBAL_KEY, normalize_tendency_scope


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Schema
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCHEMA = """

CREATE TABLE IF NOT EXISTS memory_records (
    id              TEXT PRIMARY KEY,
    source_type     TEXT NOT NULL CHECK(source_type IN ('message','tool','manual','file','system_event')),
    scope_type      TEXT NOT NULL DEFAULT 'global' CHECK(scope_type IN ('global','project','session')),
    project_id      TEXT,
    session_id      TEXT,
    turn_idx        INTEGER,
    role            TEXT NOT NULL DEFAULT 'user',
    content         TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    privacy         TEXT NOT NULL DEFAULT 'normal',
    tags            TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','deleted')),
    vector_status   TEXT NOT NULL DEFAULT 'dirty' CHECK(vector_status IN ('dirty','ready')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    metadata        TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_records_scope_status
    ON memory_records(scope_type, status);
CREATE INDEX IF NOT EXISTS idx_records_source
    ON memory_records(source_type);
CREATE INDEX IF NOT EXISTS idx_records_session
    ON memory_records(session_id);
CREATE INDEX IF NOT EXISTS idx_records_created
    ON memory_records(created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_message
    ON memory_records(session_id, turn_idx, role, content_hash)
    WHERE session_id IS NOT NULL AND turn_idx IS NOT NULL;

CREATE TABLE IF NOT EXISTS memory_session_tombstones (
    session_id      TEXT PRIMARY KEY,
    deleted_at      TEXT NOT NULL,
    version_id      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_migration_flags (
    key             TEXT PRIMARY KEY,
    applied_at      TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS memory_records_reject_tombstoned_insert
BEFORE INSERT ON memory_records
WHEN NEW.session_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM memory_session_tombstones WHERE session_id = NEW.session_id
)
BEGIN
    SELECT RAISE(ABORT, 'record belongs to deleted session');
END;

CREATE TRIGGER IF NOT EXISTS memory_records_reject_tombstoned_update
BEFORE UPDATE ON memory_records
WHEN NEW.session_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM memory_session_tombstones WHERE session_id = NEW.session_id
)
BEGIN
    SELECT RAISE(ABORT, 'record belongs to deleted session');
END;

CREATE TABLE IF NOT EXISTS memory_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id       TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    chunk_text      TEXT NOT NULL,
    vector          BLOB,
    model_version   TEXT
);

CREATE INDEX IF NOT EXISTS idx_memory_chunks_record
    ON memory_chunks(record_id, chunk_index);

CREATE TABLE IF NOT EXISTS tendency_observations (
    id              TEXT PRIMARY KEY,
    scope_kind      TEXT NOT NULL CHECK(scope_kind IN ('agent_global','workspace','session')),
    scope_key       TEXT NOT NULL,
    workspace_id    TEXT,
    project_id      TEXT,
    content         TEXT NOT NULL,
    source_session_id TEXT,
    source_turn_range TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','merged','deleted')),
    suggested_scope_kind TEXT NOT NULL DEFAULT 'session'
        CHECK(suggested_scope_kind IN ('agent_global','workspace','session'))
);

CREATE INDEX IF NOT EXISTS idx_observations_scope
    ON tendency_observations(scope_kind, scope_key, status);
CREATE INDEX IF NOT EXISTS idx_observations_source_session
    ON tendency_observations(source_session_id, status);

CREATE TRIGGER IF NOT EXISTS tendency_observations_reject_tombstoned_insert
BEFORE INSERT ON tendency_observations
WHEN NEW.source_session_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM memory_session_tombstones WHERE session_id = NEW.source_session_id
)
BEGIN
    SELECT RAISE(ABORT, 'observation belongs to deleted session');
END;

CREATE TRIGGER IF NOT EXISTS tendency_observations_reject_tombstoned_update
BEFORE UPDATE ON tendency_observations
WHEN NEW.source_session_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM memory_session_tombstones WHERE session_id = NEW.source_session_id
)
BEGIN
    SELECT RAISE(ABORT, 'observation belongs to deleted session');
END;

CREATE TABLE IF NOT EXISTS tendency_profiles (
    id              TEXT PRIMARY KEY,
    scope_kind      TEXT NOT NULL CHECK(scope_kind IN ('agent_global','workspace','session')),
    scope_key       TEXT NOT NULL,
    workspace_id    TEXT,
    project_id      TEXT,
    content         TEXT NOT NULL,
    source_observation_ids TEXT NOT NULL DEFAULT '[]',
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_profiles_scope
    ON tendency_profiles(scope_kind, scope_key);
CREATE UNIQUE INDEX IF NOT EXISTS uq_profiles_scope
    ON tendency_profiles(scope_kind, scope_key);

CREATE TABLE IF NOT EXISTS tendency_profile_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      TEXT NOT NULL,
    scope_kind      TEXT NOT NULL,
    scope_key       TEXT NOT NULL,
    workspace_id    TEXT,
    project_id      TEXT,
    content         TEXT NOT NULL,
    source_observation_ids TEXT NOT NULL DEFAULT '[]',
    version         INTEGER NOT NULL,
    saved_at        TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_profile_versions_profile
    ON tendency_profile_versions(profile_id, version);

CREATE TABLE IF NOT EXISTS tendency_mutation_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      TEXT NOT NULL,
    snapshot        TEXT NOT NULL,
    saved_at        TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tendency_mutation_versions_profile
    ON tendency_mutation_versions(profile_id, id);

CREATE TABLE IF NOT EXISTS memory_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id       TEXT,
    content         TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    snapshot        TEXT NOT NULL DEFAULT '{}',
    saved_at        TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_versions_record
    ON memory_versions(record_id);

CREATE TABLE IF NOT EXISTS workspace_file_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_path     TEXT NOT NULL,
    content         BLOB NOT NULL,
    existed         INTEGER NOT NULL DEFAULT 1,
    saved_at        TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_workspace_versions_target
    ON workspace_file_versions(target_path, id);

CREATE TABLE IF NOT EXISTS workspace_directory_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action          TEXT NOT NULL CHECK(action IN ('create','rename','delete')),
    source_path     TEXT NOT NULL,
    target_path     TEXT,
    saved_at        TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS memory_audit (
    id                TEXT PRIMARY KEY,
    ts                TEXT NOT NULL,
    action            TEXT NOT NULL,
    target_id         TEXT,
    target_type       TEXT NOT NULL DEFAULT 'record',
    source_session_id TEXT,
    reason            TEXT,
    backup_path       TEXT,
    status            TEXT NOT NULL DEFAULT 'completed' CHECK(status IN ('pending','completed')),
    details           TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_ts
    ON memory_audit(ts);

"""

# FTS5 virtual table for keyword search (parallel path to vector search)
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    record_id UNINDEXED,
    content,
    content='memory_records',
    content_rowid='rowid'
);
"""

FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS memory_fts_ai AFTER INSERT ON memory_records BEGIN
    INSERT INTO memory_fts(rowid, record_id, content)
    VALUES (new.rowid, new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memory_fts_ad AFTER DELETE ON memory_records BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, record_id, content)
    VALUES ('delete', old.rowid, old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS memory_fts_au AFTER UPDATE ON memory_records BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, record_id, content)
    VALUES ('delete', old.rowid, old.id, old.content);
    INSERT INTO memory_fts(rowid, record_id, content)
    VALUES (new.rowid, new.id, new.content);
END;
"""


REQUIRED_SCHEMA_COLUMNS = {
    "memory_records": {
        "id", "source_type", "scope_type", "project_id", "session_id", "turn_idx",
        "role", "content", "content_hash", "privacy", "tags", "status", "vector_status",
        "created_at", "updated_at", "metadata",
    },
    "memory_chunks": {
        "id", "record_id", "chunk_index", "chunk_text", "vector", "model_version",
    },
    "memory_session_tombstones": {
        "session_id", "deleted_at", "version_id",
    },
    "memory_migration_flags": {
        "key", "applied_at",
    },
    "tendency_observations": {
        "id", "scope_kind", "scope_key", "workspace_id", "project_id", "content", "source_session_id",
        "source_turn_range", "created_at", "updated_at", "status", "suggested_scope_kind",
    },
    "tendency_profiles": {
        "id", "scope_kind", "scope_key", "workspace_id", "project_id", "content", "source_observation_ids",
        "version", "created_at", "updated_at",
    },
    "tendency_profile_versions": {
        "id", "profile_id", "scope_kind", "scope_key", "workspace_id", "project_id", "content",
        "source_observation_ids", "version", "saved_at", "reason",
    },
    "tendency_mutation_versions": {
        "id", "profile_id", "snapshot", "saved_at", "reason",
    },
    "memory_versions": {
        "id", "record_id", "content", "description", "snapshot", "saved_at", "reason",
    },
    "workspace_file_versions": {
        "id", "target_path", "content", "existed", "saved_at", "reason",
    },
    "workspace_directory_versions": {
        "id", "action", "source_path", "target_path", "saved_at", "reason",
    },
    "memory_audit": {
        "id", "ts", "action", "target_id", "target_type", "source_session_id",
        "reason", "backup_path", "status", "details",
    },
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Connection
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_connection: sqlite3.Connection | None = None
_thread_connections = threading.local()
_connection_lock = threading.Lock()
_schema_lock = threading.Lock()
_open_connections: set[sqlite3.Connection] = set()
_connection_generation = 0


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _validate_schema(conn: sqlite3.Connection) -> None:
    """Reject incomplete schemas after applying supported migrations."""
    missing: list[str] = []
    for table, required_columns in REQUIRED_SCHEMA_COLUMNS.items():
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        actual_columns = {row["name"] for row in rows}
        for column in sorted(required_columns - actual_columns):
            missing.append(f"{table}.{column}")
    if missing:
        raise RuntimeError(
            "Memory database schema does not match the new memory system. "
            "Supported migrations could not supply required columns: "
            + ", ".join(missing)
        )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _prepare_legacy_schema(conn: sqlite3.Connection) -> None:
    """Move structurally incompatible HEAD tables aside before creating replacements."""
    conflicts = {
        "memory_chunks": "record_id",
        "memory_versions": "record_id",
    }
    for table, required_column in conflicts.items():
        columns = _table_columns(conn, table)
        if not columns or required_column in columns:
            continue
        suffix = 1
        legacy = f"legacy_{table}"
        while _table_columns(conn, legacy):
            suffix += 1
            legacy = f"legacy_{table}_{suffix}"
        conn.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy}"')


def _import_legacy_memories(conn: sqlite3.Connection) -> None:
    """Deterministically expose legacy saved memories through the new recall path."""
    migration_key = "head_memory_schema_import_v1"
    if conn.execute("SELECT 1 FROM memory_migration_flags WHERE key = ?", (migration_key,)).fetchone():
        return
    legacy_columns = _table_columns(conn, "memories")
    if not {"id", "content"}.issubset(legacy_columns):
        return
    for row in conn.execute("SELECT * FROM memories ORDER BY rowid").fetchall():
        data = dict(row)
        content = str(data.get("content") or "")
        created_at = str(data.get("created_at") or data.get("recorded_date") or _now())
        updated_at = str(data.get("updated_at") or created_at)
        legacy_metadata = {key: value for key, value in data.items() if key not in {"id", "content"}}
        conn.execute(
            """INSERT OR IGNORE INTO memory_records
               (id, source_type, scope_type, role, content, content_hash, privacy, tags,
                status, vector_status, created_at, updated_at, metadata)
               VALUES (?, 'manual', 'global', 'user', ?, ?, 'public', ?,
                       'active', 'dirty', ?, ?, ?)""",
            (
                str(data["id"]),
                content,
                str(data.get("content_hash") or _simple_hash(content)),
                json.dumps(["legacy", str(data.get("mem_type") or "memory")], ensure_ascii=False),
                created_at,
                updated_at,
                json.dumps({"legacy_schema": "head", "legacy": legacy_metadata}, ensure_ascii=False),
            ),
        )

    legacy_chunk_tables = conn.execute(
        """SELECT name FROM sqlite_master
           WHERE type = 'table' AND name GLOB 'legacy_memory_chunks*'
           ORDER BY name"""
    ).fetchall()
    for table_row in legacy_chunk_tables:
        table = str(table_row["name"])
        if not {"id", "memory_id", "chunk_index", "chunk_text"}.issubset(_table_columns(conn, table)):
            continue
        conn.execute(
            f"""INSERT OR IGNORE INTO memory_chunks
                (id, record_id, chunk_index, chunk_text, vector, model_version)
                SELECT id, memory_id, chunk_index, chunk_text, vector, model_version
                FROM "{table}"
                WHERE EXISTS (SELECT 1 FROM memory_records WHERE id = "{table}".memory_id)"""
        )

    legacy_version_tables = conn.execute(
        """SELECT name FROM sqlite_master
           WHERE type = 'table' AND name GLOB 'legacy_memory_versions*'
           ORDER BY name"""
    ).fetchall()
    for table_row in legacy_version_tables:
        table = str(table_row["name"])
        if not {"id", "memory_id", "content", "saved_at"}.issubset(_table_columns(conn, table)):
            continue
        for version in conn.execute(f'SELECT * FROM "{table}" ORDER BY id').fetchall():
            version_data = dict(version)
            record_id = str(version_data.get("memory_id") or "")
            record = conn.execute("SELECT * FROM memory_records WHERE id = ?", (record_id,)).fetchone()
            if not record:
                continue
            snapshot = dict(record)
            snapshot["content"] = str(version_data.get("content") or "")
            snapshot["content_hash"] = _simple_hash(snapshot["content"])
            snapshot["metadata"] = json.dumps(
                {
                    "description": str(version_data.get("description") or ""),
                    "legacy_version": version_data,
                },
                ensure_ascii=False,
            )
            conn.execute(
                """INSERT OR IGNORE INTO memory_versions
                   (id, record_id, content, description, snapshot, saved_at, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    version_data["id"],
                    record_id,
                    snapshot["content"],
                    str(version_data.get("description") or ""),
                    json.dumps(snapshot, ensure_ascii=False),
                    str(version_data["saved_at"]),
                    str(version_data.get("reason") or "legacy schema migration"),
                ),
            )
    conn.execute(
        "INSERT INTO memory_migration_flags (key, applied_at) VALUES (?, ?)",
        (migration_key, _now()),
    )


def _link_legacy_audit_targets(conn: sqlite3.Connection) -> None:
    if "target_slug" not in _table_columns(conn, "memory_audit"):
        return
    conn.execute(
        """UPDATE memory_audit
           SET target_id = (SELECT id FROM memories WHERE slug = memory_audit.target_slug),
               target_type = 'record'
           WHERE target_type = 'legacy_memory'
             AND EXISTS (SELECT 1 FROM memories WHERE slug = memory_audit.target_slug)"""
    )


def _migrate_schema(conn: sqlite3.Connection) -> None:
    record_columns = {row["name"] for row in conn.execute("PRAGMA table_info(memory_records)")}
    if "vector_status" not in record_columns:
        conn.execute("ALTER TABLE memory_records ADD COLUMN vector_status TEXT NOT NULL DEFAULT 'dirty'")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(memory_versions)")}
    if "snapshot" not in columns:
        conn.execute("ALTER TABLE memory_versions ADD COLUMN snapshot TEXT NOT NULL DEFAULT '{}'")
    observation_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tendency_observations)")}
    if "suggested_scope_kind" not in observation_columns:
        conn.execute(
            "ALTER TABLE tendency_observations "
            "ADD COLUMN suggested_scope_kind TEXT NOT NULL DEFAULT 'session'"
        )
    file_version_columns = _table_columns(conn, "workspace_file_versions")
    if "existed" not in file_version_columns:
        conn.execute("ALTER TABLE workspace_file_versions ADD COLUMN existed INTEGER NOT NULL DEFAULT 1")
    audit_columns = _table_columns(conn, "memory_audit")
    if "target_id" not in audit_columns:
        conn.execute("ALTER TABLE memory_audit ADD COLUMN target_id TEXT")
        if "target_slug" in audit_columns:
            conn.execute("UPDATE memory_audit SET target_id = target_slug WHERE target_id IS NULL")
    if "target_type" not in audit_columns:
        conn.execute("ALTER TABLE memory_audit ADD COLUMN target_type TEXT NOT NULL DEFAULT 'legacy_memory'")
    if "status" not in audit_columns:
        conn.execute("ALTER TABLE memory_audit ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'")


def connect() -> sqlite3.Connection:
    """Get or create the connection to sessions.db."""
    global _connection
    if _connection is not None:
        return _connection
    thread_connection = getattr(_thread_connections, "connection", None)
    thread_generation = getattr(_thread_connections, "generation", -1)
    if thread_connection is not None and thread_generation == _connection_generation:
        return thread_connection

    MEMORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(MEMORY_DB_PATH),
        timeout=30,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    try:
        with _schema_lock:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            _prepare_legacy_schema(conn)
            conn.executescript(SCHEMA)
            # FTS5 virtual table requires separate execution
            try:
                conn.executescript(FTS_SCHEMA + FTS_TRIGGERS)
            except sqlite3.OperationalError:
                pass  # FTS may already exist
            _migrate_schema(conn)
            _import_legacy_memories(conn)
            _link_legacy_audit_targets(conn)
            _validate_schema(conn)
            conn.commit()
    except Exception:
        conn.close()
        raise
    _thread_connections.connection = conn
    _thread_connections.generation = _connection_generation
    with _connection_lock:
        _open_connections.add(conn)
    return conn


def close() -> None:
    """Close the connection (for testing/reset)."""
    global _connection, _connection_generation
    if _connection is not None:
        _connection.close()
        _connection = None
    with _connection_lock:
        for connection in _open_connections:
            connection.close()
        _open_connections.clear()
        _connection_generation += 1
    for attribute in ("connection", "generation"):
        if hasattr(_thread_connections, attribute):
            delattr(_thread_connections, attribute)


def close_current_thread() -> None:
    """Release a short-lived worker thread's connection."""
    connection = getattr(_thread_connections, "connection", None)
    if connection is not None:
        with _connection_lock:
            _open_connections.discard(connection)
        connection.close()
    for attribute in ("connection", "generation"):
        if hasattr(_thread_connections, attribute):
            delattr(_thread_connections, attribute)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _simple_hash(text: str) -> str:
    """Stable content hash for deterministic memory idempotency."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_privacy(privacy: str | None) -> str:
    value = (privacy or "public").strip().lower()
    if value in {"public", "internal", "sensitive"}:
        return value
    if value in {"private", "secret"}:
        return "sensitive"
    # Legacy "normal" records are treated as public only when explicitly
    # inserted through this normalized path.
    return "public"


def _validate_record_scope(scope_type: str, project_id: str | None, session_id: str | None) -> str:
    scope = (scope_type or "global").strip().lower()
    if scope not in {"global", "project", "session"}:
        raise ValueError("invalid scope_type")
    if scope == "project" and not project_id:
        raise ValueError("project memory requires project_id")
    if scope == "session" and not session_id:
        raise ValueError("session memory requires session_id")
    return scope


def _retrievable_where(
    alias: str,
    *,
    project_id: str | None = None,
    session_id: str | None = None,
) -> tuple[str, list[Any]]:
    """SQL visibility filter for records allowed to enter recall."""
    params: list[Any] = []
    visibility = [
        f"{alias}.status = 'active'",
        f"COALESCE({alias}.privacy, 'public') <> 'sensitive'",
        _not_tombstoned_where(alias),
    ]
    scopes = [
        f"({alias}.scope_type = 'global' AND COALESCE({alias}.privacy, 'public') = 'public')",
    ]
    if project_id:
        scopes.append(f"({alias}.scope_type = 'project' AND {alias}.project_id = ?)")
        params.append(project_id)
    if session_id:
        scopes.append(f"({alias}.scope_type = 'session' AND {alias}.session_id = ?)")
        params.append(session_id)
    visibility.append("(" + " OR ".join(scopes) + ")")
    return " AND ".join(visibility), params


def _not_tombstoned_where(alias: str) -> str:
    return (
        "NOT EXISTS (SELECT 1 FROM memory_session_tombstones mst "
        f"WHERE mst.session_id = {alias}.session_id)"
    )


def _chunk_text(text: str, max_chars: int = 500) -> list[str]:
    """Split text by paragraphs, each chunk <= max_chars chars."""
    if len(text) <= max_chars:
        return [text]
    chunks, current = [], ""
    for para in text.split("\n\n"):
        if len(current) + len(para) + 2 > max_chars:
            if current.strip():
                chunks.append(current.strip())
            current = para
        else:
            current = (current + "\n\n" + para) if current else para
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text]


def _chunk_id(record_id: str, idx: int) -> str:
    return f"{record_id}:{idx}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Types
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass(frozen=True)
class MutationResult:
    ok: bool
    action: str
    target_id: str | None = None
    changed_rows: int = 0
    version_id: str | None = None
    audit_id: str | None = None
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "target_id": self.target_id,
            "changed_rows": self.changed_rows,
            "version_id": self.version_id,
            "audit_id": self.audit_id,
            "error": self.error,
            "details": self.details,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Audit
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _record_audit(
    *,
    action: str,
    target_id: str | None = None,
    target_type: str = "record",
    reason: str = "",
    backup_path: str | None = None,
    session_id: str | None = None,
    details: dict[str, Any] | None = None,
    status: str = "completed",
    commit: bool = True,
) -> str:
    """Write an audit event and return its id."""
    audit_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        """INSERT INTO memory_audit
           (id, ts, action, target_id, target_type, source_session_id, reason, backup_path, status, details)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (audit_id, _now(), action, target_id, target_type, session_id,
         reason, backup_path, status, json.dumps(details or {}, ensure_ascii=False)),
    )
    if commit:
        conn.commit()
    return audit_id


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# memory_records CRUD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def insert_record(
    *,
    source_type: str,
    content: str,
    scope_type: str = "global",
    project_id: str | None = None,
    session_id: str | None = None,
    turn_idx: int | None = None,
    role: str = "user",
    tags: list[str] | None = None,
    privacy: str = "public",
    metadata: dict[str, Any] | None = None,
) -> MutationResult:
    """
    Insert a raw MemoryEnvelope into memory_records.

    This is the deterministic write path — no LLM filters or value judgments.
    """
    try:
        scope_type = _validate_record_scope(scope_type, project_id, session_id)
        privacy = _normalize_privacy(privacy)
    except ValueError as exc:
        return MutationResult(False, "ingest", error=str(exc))

    conn = connect()
    now = _now()
    record_id = str(uuid.uuid4())
    content_hash = _simple_hash(content)
    try:
        if session_id is not None and turn_idx is not None:
            existing = conn.execute(
                """SELECT id FROM memory_records
                   WHERE session_id = ? AND turn_idx = ? AND role = ? AND content_hash = ?
                   LIMIT 1""",
                (session_id, turn_idx, role, content_hash),
            ).fetchone()
            if existing:
                return MutationResult(
                    True,
                    "ingest",
                    target_id=str(existing["id"]),
                    changed_rows=0,
                    details={"status": "duplicate", "record_id_short": str(existing["id"])[:8]},
                )
        cur = conn.execute(
            """INSERT INTO memory_records
               (id, source_type, scope_type, project_id, session_id, turn_idx, role,
                content, content_hash, privacy, tags, status, created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
            (record_id, source_type, scope_type, project_id, session_id, turn_idx, role,
             content, content_hash, privacy,
             json.dumps(tags or [], ensure_ascii=False),
             now, now, json.dumps(metadata or {}, ensure_ascii=False)),
        )
        changed = cur.rowcount
        if changed <= 0:
            conn.rollback()
            return MutationResult(False, "ingest", error="insert failed")
        audit_id = _record_audit(
            action="ingest",
            target_id=record_id,
            reason=f"ingest {source_type}",
            details={"record_id": record_id, "scope_type": scope_type},
            commit=False,
        )
        conn.commit()
        return MutationResult(
            True, "ingest",
            target_id=record_id,
            changed_rows=changed,
            audit_id=audit_id,
            details={"record_id_short": record_id[:8]},
        )
    except sqlite3.Error as exc:
        conn.rollback()
        return MutationResult(False, "ingest", error=str(exc))


def get_record(record_id: str) -> dict[str, Any] | None:
    """Read a single record by id."""
    conn = connect()
    row = conn.execute(
        "SELECT * FROM memory_records WHERE id = ?", (record_id,)
    ).fetchone()
    return dict(row) if row else None


def record_belongs_to_tombstoned_session(record_id: str) -> bool:
    row = connect().execute(
        """SELECT 1 FROM memory_records r
           JOIN memory_session_tombstones t ON t.session_id = r.session_id
           WHERE r.id = ?""",
        (record_id,),
    ).fetchone()
    return row is not None


def list_records(
    *,
    source_type: str | None = None,
    scope_type: str | None = None,
    status: str = "active",
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List records with optional filters. Most recent first."""
    conn = connect()
    where: list[str] = ["memory_records.status = ?", _not_tombstoned_where("memory_records")]
    params: list[Any] = [status]
    if source_type:
        where.append("memory_records.source_type = ?")
        params.append(source_type)
    if scope_type:
        where.append("memory_records.scope_type = ?")
        params.append(scope_type)
    sql = f"SELECT * FROM memory_records WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return [dict(r) for r in conn.execute(sql, params)]


def list_visible_records(
    *,
    project_id: str | None = None,
    session_id: str | None = None,
    source_type: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List only records visible to one active recall scope."""
    where_sql, params = _retrievable_where(
        "memory_records",
        project_id=project_id,
        session_id=session_id,
    )
    if source_type:
        where_sql += " AND source_type = ?"
        params.append(source_type)
    params.append(limit)
    rows = connect().execute(
        f"SELECT * FROM memory_records WHERE {where_sql} ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def list_dirty_record_ids(limit: int = 20) -> list[str]:
    rows = connect().execute(
        """SELECT id FROM memory_records
           WHERE status = 'active' AND vector_status = 'dirty'
             AND NOT EXISTS (
                 SELECT 1 FROM memory_session_tombstones mst
                 WHERE mst.session_id = memory_records.session_id
             )
           ORDER BY updated_at
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [str(row["id"]) for row in rows]


def update_record_status(record_id: str, status: str, *, reason: str = "") -> MutationResult:
    """Soft delete or restore a record."""
    if status not in ("active", "deleted"):
        return MutationResult(False, "update_status", error="invalid status")
    conn = connect()
    try:
        old = get_record(record_id)
        if not old:
            return MutationResult(False, "update_status", error="record not found")
        if old.get("status") == status:
            return MutationResult(False, "update_status", target_id=record_id, error="no changes")
        version_path = _save_version(record_id, reason=reason or f"set {status}", commit=False)
        now = _now()
        cur = conn.execute(
            "UPDATE memory_records SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, record_id),
        )
        changed = cur.rowcount
        if changed <= 0:
            conn.rollback()
            return MutationResult(False, "update_status", target_id=record_id, error="no rows changed")
        audit_id = _record_audit(
            action=f"set_{status}",
            target_id=record_id,
            reason=reason or f"set {status}",
            backup_path=version_path,
            details={"changed_rows": changed},
            commit=False,
        )
        conn.commit()
        return MutationResult(True, f"set_{status}", target_id=record_id, changed_rows=changed,
                              version_id=version_path, audit_id=audit_id)
    except Exception as exc:
        conn.rollback()
        return MutationResult(False, "update_status", error=str(exc))


def count_records(status: str = "active") -> int:
    """Count records by status."""
    conn = connect()
    row = conn.execute(
        f"""SELECT COUNT(*) FROM memory_records
            WHERE status = ? AND {_not_tombstoned_where('memory_records')}""",
        (status,),
    ).fetchone()
    return row[0] if row else 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# memory_chunks — vector storage
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def clear_record_vectors(record_id: str) -> None:
    """Remove all vector chunks for a record."""
    conn = connect()
    conn.execute("DELETE FROM memory_chunks WHERE record_id = ?", (record_id,))
    conn.execute("UPDATE memory_records SET vector_status = 'dirty' WHERE id = ?", (record_id,))
    conn.commit()


def refresh_record_vectors(
    record_id: str,
    model_version: str = "bge-small-zh-v1.5",
) -> dict[str, Any]:
    """
    Refresh vector chunks for one record.
    Best-effort: on failure clears stale chunks to avoid outdated matches.
    """
    conn = connect()
    row = conn.execute(
        "SELECT content FROM memory_records WHERE id = ?", (record_id,)
    ).fetchone()
    if not row:
        return {"ok": False, "reason": "record not found"}
    chunks = _chunk_text(row["content"])
    try:
        from .embedding import embed_batch
        vectors = embed_batch(chunks)
    except Exception as exc:
        clear_record_vectors(record_id)
        return {"ok": False, "reason": str(exc)}
    if not vectors:
        clear_record_vectors(record_id)
        return {"ok": False, "reason": "embedding unavailable"}
    count = store_record_vectors(record_id, chunks, vectors, model_version=model_version)
    return {"ok": True, "chunks": count}


def store_record_vectors(
    record_id: str,
    chunks: list[str],
    vectors: list[list[float]],
    model_version: str = "bge-small-zh-v1.5",
) -> int:
    """Store chunk text and vectors."""
    conn = connect()
    conn.execute("DELETE FROM memory_chunks WHERE record_id = ?", (record_id,))
    import numpy as np
    for idx, (text, vec) in enumerate(zip(chunks, vectors)):
        conn.execute(
            "INSERT INTO memory_chunks (record_id, chunk_index, chunk_text, vector, model_version) VALUES (?, ?, ?, ?, ?)",
            (record_id, idx, text, np.array(vec, dtype=np.float32).tobytes(), model_version),
        )
    conn.execute("UPDATE memory_records SET vector_status = 'ready' WHERE id = ?", (record_id,))
    conn.commit()
    return len(chunks)


def search_vectors(
    query_vector: list[float],
    top_k: int = 10,
    *,
    project_id: str | None = None,
    session_id: str | None = None,
    source_type: str | None = None,
) -> list[dict[str, Any]]:
    """
    Cosine-similarity vector search (full scan, fine for <1000 records).
    BGE models produce normalized embeddings, so dot product = cosine.
    """
    conn = connect()
    import numpy as np
    where_sql, params = _retrievable_where("r", project_id=project_id, session_id=session_id)
    if source_type:
        where_sql += " AND r.source_type = ?"
        params.append(source_type)
    rows = conn.execute(f"""
        SELECT c.id, c.record_id, c.chunk_text, c.vector,
               r.id AS rec_id, r.scope_type, r.project_id, r.session_id, r.updated_at
        FROM memory_chunks c
        JOIN memory_records r ON r.id = c.record_id
        WHERE {where_sql}
    """, params).fetchall()
    if not rows:
        return []

    query_arr = np.array(query_vector, dtype=np.float32)
    scored: list[tuple[float, dict]] = []
    for r in rows:
        try:
            vec = np.frombuffer(r["vector"], dtype=np.float32)
            sim = float(np.dot(query_arr, vec))
        except Exception:
            continue
        scored.append((sim, {
            "record_id": r["rec_id"],
            "chunk_text": r["chunk_text"][:200],
            "similarity": round(sim, 4),
            "scope_type": r["scope_type"],
        }))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


def search_fts(
    query: str,
    limit: int = 20,
    *,
    project_id: str | None = None,
    session_id: str | None = None,
    source_type: str | None = None,
    include_all: bool = False,
) -> list[dict[str, Any]]:
    """Keyword search via FTS5. Returns record_id + snippet.

    Falls back to LIKE search for CJK text (FTS5 default tokenizer doesn't
    handle unsegmented Chinese/Japanese/Korean).
    """
    conn = connect()
    safe_query = query.replace('"', '""')
    rows: list[sqlite3.Row] = []
    if include_all:
        where_sql, visibility_params = (
            f"mr.status = 'active' AND {_not_tombstoned_where('mr')}",
            [],
        )
    else:
        where_sql, visibility_params = _retrievable_where("mr", project_id=project_id, session_id=session_id)
    if source_type:
        where_sql += " AND mr.source_type = ?"
        visibility_params.append(source_type)
    try:
        rows = conn.execute(
            f"""SELECT mf.record_id, snippet(memory_fts, 1, '<b>', '</b>', '...', 40) AS snippet,
                      mr.content, mr.scope_type, mr.created_at, mr.updated_at
               FROM memory_fts mf
               JOIN memory_records mr ON mr.id = mf.record_id
               WHERE memory_fts MATCH ? AND {where_sql}
               ORDER BY rank
               LIMIT ?""",
            (f'"{safe_query}"', *visibility_params, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        pass  # Fall through to LIKE

    # FTS5 MATCH on CJK text often returns 0 rows silently — use LIKE fallback.
    # QueryBuilder may pass a space-joined CJK term list; match any term instead
    # of requiring the whole joined string to appear verbatim.
    if not rows:
        terms = _like_terms(query)
        score_expr = " + ".join("CASE WHEN content LIKE ? THEN 1 ELSE 0 END" for _ in terms)
        where_expr = " OR ".join("content LIKE ?" for _ in terms)
        if include_all:
            where_sql, visibility_params = (
                "memory_records.status = 'active' AND " + _not_tombstoned_where("memory_records"),
                [],
            )
        else:
            where_sql, visibility_params = _retrievable_where(
                "memory_records",
                project_id=project_id,
                session_id=session_id,
            )
        if source_type:
            where_sql += " AND memory_records.source_type = ?"
            visibility_params.append(source_type)
        score_params = [f"%{term}%" for term in terms]
        where_params = [f"%{term}%" for term in terms]
        rows = conn.execute(
            f"""SELECT id AS record_id, substr(content, 1, 200) AS snippet,
                       content, scope_type, created_at, updated_at,
                       ({score_expr}) AS match_count
                FROM memory_records
                WHERE ({where_expr}) AND {where_sql}
                ORDER BY match_count DESC, created_at DESC
                LIMIT ?""",
            (*score_params, *where_params, *visibility_params, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _like_terms(query: str) -> list[str]:
    """Terms for CJK LIKE fallback, preserving exact phrase plus split terms."""
    terms: list[str] = []
    for term in [query.strip(), *re.split(r"\s+", query.strip())]:
        cleaned = term.strip().strip('"')
        if len(cleaned) < 2:
            continue
        if cleaned not in terms:
            terms.append(cleaned)
    return terms[:12] or [query]


def _has_cjk(text: str) -> bool:
    """Check if text contains CJK characters."""
    for ch in text:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or   # CJK Unified Ideographs
            0x3400 <= cp <= 0x4DBF or   # CJK Unified Ideographs Extension A
            0x3040 <= cp <= 0x309F or   # Hiragana
            0x30A0 <= cp <= 0x30FF or   # Katakana
            0xAC00 <= cp <= 0xD7AF):     # Hangul
            return True
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# tendency_observations
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def insert_observation(
    *,
    content: str,
    scope_kind: str = "agent_global",
    scope_key: str | None = None,
    workspace_id: str | None = None,
    project_id: str | None = None,
    source_session_id: str | None = None,
    source_turn_range: str | None = None,
    suggested_scope_kind: str = "session",
) -> str | None:
    """Add a raw tendency observation."""
    if not content.strip():
        return None
    try:
        scope_kind, scope_key = normalize_tendency_scope(scope_kind, scope_key)
        suggested_scope_kind, _ = normalize_tendency_scope(suggested_scope_kind, scope_key)
    except ValueError:
        return None
    if scope_kind == "agent_global":
        workspace_id = None
        project_id = None
    elif scope_kind == "workspace":
        workspace_id = workspace_id or scope_key
    conn = connect()
    obs_id = str(uuid.uuid4())
    now = _now()
    try:
        conn.execute(
            """INSERT INTO tendency_observations
               (id, scope_kind, scope_key, workspace_id, project_id, content,
                source_session_id, source_turn_range, created_at, updated_at, status,
                suggested_scope_kind)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
            (
                obs_id,
                scope_kind,
                scope_key,
                workspace_id,
                project_id,
                content,
                source_session_id,
                source_turn_range,
                now,
                now,
                suggested_scope_kind,
            ),
        )
        _record_audit(
            action="observe_tendency",
            target_id=obs_id,
            target_type="tendency_observation",
            session_id=source_session_id,
            reason="record tendency observation",
            details={
                "scope_kind": scope_kind,
                "scope_key": scope_key,
                "workspace_id": workspace_id,
                "project_id": project_id,
                "source_turn_range": source_turn_range,
                "suggested_scope_kind": suggested_scope_kind,
            },
            commit=False,
        )
        conn.commit()
        return obs_id
    except sqlite3.Error:
        conn.rollback()
        return None


def list_observations(
    *,
    scope_kind: str | None = None,
    scope_key: str | None = None,
    workspace_id: str | None = None,
    project_id: str | None = None,
    suggested_scope_kind: str | None = None,
    status: str | None = "active",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List tendency observations, newest first."""
    conn = connect()
    where: list[str] = [
        """NOT EXISTS (
               SELECT 1 FROM memory_session_tombstones mst
               WHERE mst.session_id = tendency_observations.source_session_id
           )"""
    ]
    params: list[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if scope_kind:
        normalized_kind, normalized_key = normalize_tendency_scope(scope_kind, scope_key)
        where.append("scope_kind = ?")
        params.append(normalized_kind)
        if scope_key:
            where.append("scope_key = ?")
            params.append(normalized_key)
    elif scope_key:
        where.append("scope_key = ?")
        params.append(scope_key)
    if workspace_id:
        where.append("workspace_id = ?")
        params.append(workspace_id)
    if project_id:
        where.append("project_id = ?")
        params.append(project_id)
    if suggested_scope_kind:
        where.append("suggested_scope_kind = ?")
        params.append(suggested_scope_kind)
    if not where:
        where.append("1 = 1")
    sql = f"SELECT * FROM tendency_observations WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params)]


def update_observation_status(obs_id: str, status: str, *, commit: bool = True) -> bool:
    """Set observation status (merged/deleted)."""
    if status not in ("active", "merged", "deleted"):
        return False
    conn = connect()
    old = conn.execute("SELECT * FROM tendency_observations WHERE id = ?", (obs_id,)).fetchone()
    if not old:
        return False
    cur = conn.execute(
        "UPDATE tendency_observations SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), obs_id),
    )
    _record_audit(
        action=f"set_observation_{status}",
        target_id=obs_id,
        target_type="tendency_observation",
        session_id=old["source_session_id"],
        reason=f"set tendency observation {status}",
        details={
            "previous_status": old["status"],
            "next_status": status,
            "scope_kind": old["scope_kind"],
            "scope_key": old["scope_key"],
            "workspace_id": old["workspace_id"],
            "project_id": old["project_id"],
        },
        commit=False,
    )
    if commit:
        conn.commit()
    return cur.rowcount > 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# tendency_profiles
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_profile_by_scope(scope_kind: str, scope_key: str | None = None) -> dict[str, Any] | None:
    """Read the latest compiled profile for a scope."""
    try:
        scope_kind, scope_key = normalize_tendency_scope(scope_kind, scope_key)
    except ValueError:
        return None
    conn = connect()
    row = conn.execute(
        """SELECT tp.* FROM tendency_profiles tp
           WHERE tp.scope_kind = ? AND tp.scope_key = ?
             AND (tp.scope_kind <> 'session' OR NOT EXISTS (
                 SELECT 1 FROM memory_session_tombstones mst WHERE mst.session_id = tp.scope_key
             ))
           ORDER BY tp.version DESC LIMIT 1""",
        (scope_kind, scope_key),
    ).fetchone()
    return dict(row) if row else None


def list_profiles() -> list[dict[str, Any]]:
    """List compiled tendency profiles."""
    conn = connect()
    rows = conn.execute(
        """SELECT tp.* FROM tendency_profiles tp
           WHERE tp.scope_kind <> 'session' OR NOT EXISTS (
               SELECT 1 FROM memory_session_tombstones mst WHERE mst.session_id = tp.scope_key
           )
           ORDER BY tp.scope_kind ASC, tp.updated_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def _save_profile_version(profile: dict[str, Any], reason: str = "", *, commit: bool = True) -> str | None:
    """Snapshot the current profile before it is overwritten."""
    conn = connect()
    cursor = conn.execute(
        """INSERT INTO tendency_profile_versions
           (profile_id, scope_kind, scope_key, workspace_id, project_id, content,
            source_observation_ids, version, saved_at, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            profile["id"],
            profile["scope_kind"],
            profile["scope_key"],
            profile.get("workspace_id"),
            profile.get("project_id"),
            profile.get("content", ""),
            profile.get("source_observation_ids", "[]"),
            int(profile.get("version") or 0),
            _now(),
            reason,
        ),
    )
    if commit:
        conn.commit()
    return f"profile_version://{cursor.lastrowid}"


def list_profile_versions(profile_id: str) -> list[dict[str, Any]]:
    """List saved versions for a tendency profile."""
    conn = connect()
    rows = conn.execute(
        """SELECT id, version, saved_at, reason, length(content) AS size
           FROM tendency_profile_versions
           WHERE profile_id = ?
           ORDER BY saved_at DESC, id DESC""",
        (profile_id,),
    ).fetchall()
    return [
        {"version": r["version"], "path": f"profile_version://{r['id']}",
         "mtime": r["saved_at"], "size": r["size"] or 0, "reason": r["reason"] or ""}
        for r in rows
    ]


def _string_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item)]
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed if str(item)] if isinstance(parsed, list) else []


def _save_tendency_mutation_version(
    profile_id: str,
    profile: dict[str, Any] | None,
    observations: list[dict[str, Any]],
    *,
    reason: str,
    commit: bool = True,
) -> str:
    cursor = connect().execute(
        """INSERT INTO tendency_mutation_versions (profile_id, snapshot, saved_at, reason)
           VALUES (?, ?, ?, ?)""",
        (
            profile_id,
            json.dumps({"profile": profile, "observations": observations}, ensure_ascii=False),
            _now(),
            reason,
        ),
    )
    if commit:
        connect().commit()
    return f"tendency_version://{cursor.lastrowid}"


def upsert_profile(
    *,
    scope_kind: str,
    scope_key: str | None = None,
    workspace_id: str | None = None,
    project_id: str | None = None,
    content: str,
    source_observation_ids: list[str] | None = None,
    merge_observation_ids: list[str] | None = None,
    replace_source_observation_ids: bool = False,
) -> MutationResult:
    """Create or update a profile and merge observations atomically."""
    try:
        scope_kind, scope_key = normalize_tendency_scope(scope_kind, scope_key)
    except ValueError as exc:
        return MutationResult(False, "upsert_profile", error=str(exc))
    if scope_kind == "agent_global":
        workspace_id = None
        project_id = None
    elif scope_kind == "workspace":
        workspace_id = workspace_id or scope_key
    conn = connect()
    now = _now()
    existing = get_profile_by_scope(scope_kind, scope_key)
    try:
        merge_ids = list(dict.fromkeys(str(item) for item in (merge_observation_ids or []) if str(item)))
        observations: list[dict[str, Any]] = []
        if merge_ids:
            placeholders = ",".join("?" for _ in merge_ids)
            rows = conn.execute(
                f"SELECT * FROM tendency_observations WHERE id IN ({placeholders})",
                merge_ids,
            ).fetchall()
            observations = [dict(row) for row in rows]
            found_ids = {str(item["id"]) for item in observations}
            missing = [item for item in merge_ids if item not in found_ids]
            if missing:
                return MutationResult(False, "upsert_profile", error=f"observation not found: {missing[0]}")

        profile_id = str(existing["id"]) if existing else str(uuid.uuid4())
        version_path = _save_tendency_mutation_version(
            profile_id,
            existing,
            observations,
            reason="compile tendency profile",
            commit=False,
        )
        existing_sources = _string_list(existing.get("source_observation_ids")) if existing else []
        incoming_sources = _string_list(source_observation_ids or [])
        next_sources = list(dict.fromkeys(
            incoming_sources if replace_source_observation_ids else [*existing_sources, *incoming_sources]
        ))
        if existing:
            profile_history_path = _save_profile_version(existing, reason="profile update", commit=False)
            new_version = existing["version"] + 1
            cur = conn.execute(
                """UPDATE tendency_profiles
                   SET content = ?, source_observation_ids = ?, version = ?, updated_at = ?
                   WHERE id = ?""",
                (content, json.dumps(next_sources, ensure_ascii=False),
                 new_version, now, existing["id"]),
            )
        else:
            profile_history_path = None
            new_version = 1
            cur = conn.execute(
                """INSERT INTO tendency_profiles
                   (id, scope_kind, scope_key, workspace_id, project_id, content,
                    source_observation_ids, version, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (profile_id, scope_kind, scope_key, workspace_id, project_id, content,
                 json.dumps(next_sources, ensure_ascii=False),
                 new_version, now, now),
            )
        changed = cur.rowcount
        if changed <= 0:
            conn.rollback()
            return MutationResult(False, "upsert_profile", error="no rows changed")
        for observation_id in merge_ids:
            merged = conn.execute(
                """UPDATE tendency_observations
                   SET status = 'merged', updated_at = ?
                   WHERE id = ? AND status <> 'merged'""",
                (now, observation_id),
            ).rowcount
            changed += merged
        audit_id = _record_audit(
            action="compile_profile",
            target_id=profile_id,
            target_type="tendency_profile",
            reason=f"compile {scope_kind} profile v{new_version}",
            backup_path=version_path,
            details={
                "scope_kind": scope_kind,
                "scope_key": scope_key,
                "workspace_id": workspace_id,
                "project_id": project_id,
                "version": new_version,
                "changed_rows": changed,
                "merged_observation_ids": merge_ids,
                "profile_history_version": profile_history_path,
            },
            commit=False,
        )
        conn.commit()
        return MutationResult(True, "upsert_profile", target_id=profile_id, changed_rows=changed, audit_id=audit_id,
                              version_id=version_path, details={"version": new_version, "scope_kind": scope_kind,
                                                               "merged_observation_ids": merge_ids})
    except Exception as exc:
        conn.rollback()
        return MutationResult(False, "upsert_profile", error=str(exc))


def restore_tendency_mutation_version(version: str) -> MutationResult:
    """Rollback one atomic profile/observation mutation snapshot."""
    try:
        version_id = int(version)
    except (TypeError, ValueError):
        return MutationResult(False, "rollback_tendency", error="invalid tendency version")
    conn = connect()
    row = conn.execute(
        "SELECT * FROM tendency_mutation_versions WHERE id = ?",
        (version_id,),
    ).fetchone()
    if not row:
        return MutationResult(False, "rollback_tendency", error="tendency version not found")
    try:
        snapshot = json.loads(row["snapshot"] or "{}")
        previous_profile = snapshot.get("profile")
        previous_observations = snapshot.get("observations") or []
        profile_id = str(row["profile_id"])
        current_profile_row = conn.execute(
            "SELECT * FROM tendency_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        current_profile = dict(current_profile_row) if current_profile_row else None
        observation_ids = list(dict.fromkeys([
            *_string_list(current_profile.get("source_observation_ids") if current_profile else []),
            *_string_list(previous_profile.get("source_observation_ids") if previous_profile else []),
            *[str(item.get("id") or "") for item in previous_observations if item.get("id")],
        ]))
        current_observations: list[dict[str, Any]] = []
        if observation_ids:
            placeholders = ",".join("?" for _ in observation_ids)
            current_observations = [
                dict(item)
                for item in conn.execute(
                    f"SELECT * FROM tendency_observations WHERE id IN ({placeholders})",
                    observation_ids,
                ).fetchall()
            ]
        rollback_version = _save_tendency_mutation_version(
            profile_id,
            current_profile,
            current_observations,
            reason=f"before rollback tendency version {version_id}",
            commit=False,
        )

        changed = 0
        if previous_profile is None:
            changed += conn.execute("DELETE FROM tendency_profiles WHERE id = ?", (profile_id,)).rowcount
        else:
            conn.execute("DELETE FROM tendency_profiles WHERE id = ?", (profile_id,))
            columns = [
                "id", "scope_kind", "scope_key", "workspace_id", "project_id", "content",
                "source_observation_ids", "version", "created_at", "updated_at",
            ]
            conn.execute(
                f"INSERT INTO tendency_profiles ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                [previous_profile.get(column) for column in columns],
            )
            changed += 1
        for observation in previous_observations:
            changed += conn.execute(
                """UPDATE tendency_observations
                   SET scope_kind = ?, scope_key = ?, workspace_id = ?, project_id = ?, content = ?,
                       source_session_id = ?, source_turn_range = ?, created_at = ?, updated_at = ?,
                       status = ?, suggested_scope_kind = ?
                   WHERE id = ?""",
                (
                    observation.get("scope_kind"), observation.get("scope_key"),
                    observation.get("workspace_id"), observation.get("project_id"),
                    observation.get("content"), observation.get("source_session_id"),
                    observation.get("source_turn_range"), observation.get("created_at"),
                    observation.get("updated_at"), observation.get("status"),
                    observation.get("suggested_scope_kind", "session"), observation.get("id"),
                ),
            ).rowcount
        referenced_observation_ids: set[str] = set()
        for profile_row in conn.execute("SELECT source_observation_ids FROM tendency_profiles"):
            referenced_observation_ids.update(_string_list(profile_row["source_observation_ids"]))
        previous_by_id = {
            str(item["id"]): item for item in previous_observations if item.get("id")
        }
        for observation_id in observation_ids:
            previous_status = str(previous_by_id.get(observation_id, {}).get("status") or "active")
            desired_status = (
                "deleted"
                if previous_status == "deleted"
                else ("merged" if observation_id in referenced_observation_ids else "active")
            )
            changed += conn.execute(
                """UPDATE tendency_observations SET status = ?, updated_at = ?
                   WHERE id = ? AND status <> ?""",
                (desired_status, _now(), observation_id, desired_status),
            ).rowcount
        if changed <= 0:
            conn.rollback()
            return MutationResult(False, "rollback_tendency", target_id=profile_id, error="no rows changed")
        audit_id = _record_audit(
            action="rollback_tendency",
            target_id=profile_id,
            target_type="tendency_profile",
            reason=f"restore tendency mutation version {version_id}",
            backup_path=rollback_version,
            details={"changed_rows": changed, "restored_version": version_id},
            commit=False,
        )
        conn.commit()
        return MutationResult(
            True,
            "rollback_tendency",
            target_id=profile_id,
            changed_rows=changed,
            version_id=rollback_version,
            audit_id=audit_id,
        )
    except Exception as exc:
        conn.rollback()
        return MutationResult(False, "rollback_tendency", error=str(exc))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# memory_versions — snapshots
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _save_version(record_id: str, reason: str = "", *, commit: bool = True) -> str | None:
    """Snapshot the complete mutable record state into memory_versions."""
    conn = connect()
    row = conn.execute(
        "SELECT * FROM memory_records WHERE id = ?",
        (record_id,),
    ).fetchone()
    if not row:
        return None
    description = ""
    try:
        meta = json.loads(row["metadata"] or "{}")
        description = meta.get("description", "")
    except (json.JSONDecodeError, TypeError):
        pass
    cursor = conn.execute(
        """INSERT INTO memory_versions (record_id, content, description, snapshot, saved_at, reason)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (record_id, row["content"], description,
         json.dumps(dict(row), ensure_ascii=False), _now(), reason),
    )
    if commit:
        conn.commit()
    return f"version://{cursor.lastrowid}"


def list_record_versions(record_id: str) -> list[dict[str, Any]]:
    """List versions for a record, newest first."""
    conn = connect()
    rows = conn.execute(
        """SELECT id, saved_at, reason, length(content) AS size
           FROM memory_versions
           WHERE record_id = ?
           ORDER BY saved_at DESC""",
        (record_id,),
    ).fetchall()
    return [
        {"version": r["id"], "path": f"version://{r['id']}",
         "mtime": r["saved_at"], "size": r["size"] or 0, "reason": r["reason"] or ""}
        for r in rows
    ]


def get_record_version(record_id: str, version: str) -> dict[str, Any] | None:
    try:
        version_id = int(version)
    except (TypeError, ValueError):
        return None
    row = connect().execute(
        "SELECT * FROM memory_versions WHERE record_id = ? AND id = ?",
        (record_id, version_id),
    ).fetchone()
    if not row:
        return None
    data = json.loads(row["snapshot"] or "{}")
    if not data:
        data = {"id": record_id, "content": row["content"]}
    data["version"] = row["id"]
    data["saved_at"] = row["saved_at"]
    data["reason"] = row["reason"]
    return data


def get_audit_event(audit_id: str) -> dict[str, Any] | None:
    row = connect().execute("SELECT * FROM memory_audit WHERE id = ?", (audit_id,)).fetchone()
    return dict(row) if row else None


def list_audit_events(limit: int = 100) -> list[dict[str, Any]]:
    rows = connect().execute(
        "SELECT * FROM memory_audit ORDER BY ts DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def restore_record_version(record_id: str, version: str) -> MutationResult:
    """Restore record content from a version snapshot."""
    try:
        version_id = int(version)
    except (TypeError, ValueError):
        return MutationResult(False, "restore", error="invalid version")
    conn = connect()
    row = conn.execute(
        "SELECT content, description, snapshot FROM memory_versions WHERE record_id = ? AND id = ?",
        (record_id, version_id),
    ).fetchone()
    if not row:
        return MutationResult(False, "restore", target_id=record_id, error="version not found")
    try:
        snapshot = json.loads(row["snapshot"] or "{}")
        content = str(snapshot.get("content", row["content"]))
        metadata = snapshot.get("metadata")
        if not isinstance(metadata, str):
            metadata = json.dumps({"description": row["description"]}, ensure_ascii=False)
        version_path = _save_version(record_id, reason=f"restore from version {version}", commit=False)
        now = _now()
        cur = conn.execute(
            """UPDATE memory_records
               SET source_type = ?, scope_type = ?, project_id = ?, session_id = ?, turn_idx = ?,
                   role = ?, content = ?, content_hash = ?, privacy = ?, tags = ?, status = ?, vector_status = 'dirty',
                   updated_at = ?, metadata = ?
               WHERE id = ?""",
            (
                snapshot.get("source_type", "manual"),
                snapshot.get("scope_type", "global"),
                snapshot.get("project_id"),
                snapshot.get("session_id"),
                snapshot.get("turn_idx"),
                snapshot.get("role", "user"),
                content,
                _simple_hash(content),
                snapshot.get("privacy", "public"),
                snapshot.get("tags", "[]"),
                snapshot.get("status", "active"),
                now,
                metadata,
                record_id,
            ),
        )
        changed = cur.rowcount
        if changed <= 0:
            conn.rollback()
            return MutationResult(False, "restore", target_id=record_id, error="no rows changed")
        audit_id = _record_audit(
            action="restore",
            target_id=record_id,
            reason=f"restore from version {version}",
            backup_path=version_path,
            details={"changed_rows": changed, "version_snapshot": version_path},
            commit=False,
        )
        conn.commit()
        return MutationResult(True, "restore", target_id=record_id, changed_rows=changed,
                              version_id=version_path, audit_id=audit_id)
    except Exception as exc:
        conn.rollback()
        return MutationResult(False, "restore", error=str(exc))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Stats
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_stats(
    *,
    project_id: str | None = None,
    session_id: str | None = None,
    workspace_id: str | None = None,
    include_all: bool = True,
) -> dict[str, Any]:
    """Memory system statistics."""
    conn = connect()
    if include_all:
        visible_records = _not_tombstoned_where("r")
        record_params: list[Any] = []
    else:
        visible_records, record_params = _retrievable_where(
            "r",
            project_id=project_id,
            session_id=session_id,
        )
    total = conn.execute(
        f"SELECT COUNT(*) FROM memory_records r WHERE {visible_records}",
        record_params,
    ).fetchone()[0]
    by_status = dict(conn.execute(
        f"SELECT status, COUNT(*) FROM memory_records r WHERE {visible_records} GROUP BY status",
        record_params,
    ).fetchall())
    by_source = dict(conn.execute(
        f"SELECT source_type, COUNT(*) FROM memory_records r WHERE {visible_records} GROUP BY source_type",
        record_params,
    ).fetchall())
    total_chunks = conn.execute(
        f"""SELECT COUNT(*) FROM memory_chunks c
            JOIN memory_records r ON r.id = c.record_id
            WHERE {visible_records}""",
        record_params,
    ).fetchone()[0]

    tendency_scopes = ["scope_kind = 'agent_global'"]
    tendency_params: list[Any] = []
    if workspace_id:
        tendency_scopes.append("(scope_kind = 'workspace' AND scope_key = ?)")
        tendency_params.append(workspace_id)
    if session_id:
        tendency_scopes.append("(scope_kind = 'session' AND scope_key = ?)")
        tendency_params.append(session_id)
    tendency_scope_sql = "1 = 1" if include_all else "(" + " OR ".join(tendency_scopes) + ")"
    obs_count = conn.execute(
        f"""SELECT COUNT(*) FROM tendency_observations o
           WHERE o.status = 'active' AND {tendency_scope_sql}
             AND NOT EXISTS (
                 SELECT 1 FROM memory_session_tombstones mst
                 WHERE mst.session_id = o.source_session_id
             )""",
        tendency_params,
    ).fetchone()[0]
    profile_count = conn.execute(
        f"""SELECT COUNT(*) FROM tendency_profiles tp
           WHERE ({tendency_scope_sql}) AND (tp.scope_kind <> 'session' OR NOT EXISTS (
               SELECT 1 FROM memory_session_tombstones mst WHERE mst.session_id = tp.scope_key
           ))""",
        tendency_params,
    ).fetchone()[0]
    dirty_vectors = conn.execute(
        f"""SELECT COUNT(*) FROM memory_records r
            WHERE r.status = 'active' AND r.vector_status = 'dirty' AND {visible_records}""",
        record_params,
    ).fetchone()[0]
    return {
        "total_records": total,
        "by_status": by_status,
        "by_source_type": by_source,
        "total_chunks": total_chunks,
        "active_observations": obs_count,
        "tendency_profiles": profile_count,
        "dirty_vectors": dirty_vectors,
    }


def update_record(
    record_id: str,
    *,
    content: str | None = None,
    description: str | None = None,
    reason: str = "",
) -> MutationResult:
    """Edit/replace a record's content. Saves version and writes audit."""
    old = get_record(record_id)
    if not old:
        return MutationResult(False, "update", target_id=record_id, error="record not found")
    conn = connect()
    now = _now()
    new_content = content if content is not None else old["content"]
    try:
        old_metadata = json.loads(old.get("metadata") or "{}")
    except (json.JSONDecodeError, TypeError):
        old_metadata = {}
    old_desc = str(old_metadata.get("description") or "")
    new_desc = description if description is not None else old_desc
    if new_content == old["content"] and new_desc == old_desc:
        return MutationResult(False, "update", target_id=record_id, error="no changes")
    try:
        version_path = _save_version(record_id, reason=reason or "update", commit=False)
        if not version_path:
            conn.rollback()
            return MutationResult(False, "update", target_id=record_id, error="version snapshot failed")
        cur = conn.execute(
            """UPDATE memory_records
               SET content = ?, content_hash = ?, updated_at = ?, metadata = ?, vector_status = 'dirty'
               WHERE id = ?""",
            (new_content, _simple_hash(new_content), now,
             json.dumps({**old_metadata, "description": new_desc}, ensure_ascii=False),
             record_id),
        )
        changed = cur.rowcount
        if changed <= 0:
            conn.rollback()
            return MutationResult(False, "update", target_id=record_id, error="no rows changed")
        audit_id = _record_audit(
            action="update",
            target_id=record_id,
            reason=reason or "update",
            backup_path=version_path,
            details={"changed_rows": changed},
            commit=False,
        )
        conn.commit()
        return MutationResult(True, "update", target_id=record_id, changed_rows=changed,
                              version_id=version_path, audit_id=audit_id)
    except Exception as exc:
        conn.rollback()
        return MutationResult(False, "update", target_id=record_id, error=str(exc))
