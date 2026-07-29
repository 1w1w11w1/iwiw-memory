from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from functools import wraps
from typing import Any

from memory_agent.config import EXTRACT_TIMEOUT
from memory_agent.engine import MemoryEngine, MemoryMutationContext, default_memory_engine
from memory_agent.scopes import ActiveMemoryScope

from .db import connect, now_iso


_TENDENCY_LOCK = threading.Lock()


def _serialized_write(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._db_write_lock:
            return method(self, *args, **kwargs)
    return wrapped


def _rowdict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _session_title(text: str) -> str:
    clean = " ".join(text.strip().split())
    if not clean:
        return "新对话"
    return clean[:24] + ("..." if len(clean) > 24 else "")


def _estimate_tokens(text: str) -> int:
    compact = "".join(str(text or "").split())
    ascii_count = sum(1 for char in compact if ord(char) < 128)
    return len(compact) - ascii_count + (ascii_count + 3) // 4


@dataclass
class ConsolidationResult:
    cycle_no: int
    idx_from: int
    idx_to: int
    summary: str
    memory_result: dict[str, Any]


@dataclass(frozen=True)
class SessionMutationResult:
    ok: bool
    action: str
    target_id: str | None = None
    changed_rows: int = 0
    version_id: str | None = None
    audit_id: str | None = None
    error: str = ""
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "target_id": self.target_id,
            "changed_rows": self.changed_rows,
            "version_id": self.version_id,
            "audit_id": self.audit_id,
            "error": self.error,
            "details": self.details or {},
        }


class SessionMemoryService:
    """SQLite-backed IwIw session memory and replay service."""

    def __init__(self, memory_engine: MemoryEngine | None = None) -> None:
        self.conn = connect()
        self.memory_engine = memory_engine or default_memory_engine
        self._db_write_lock = threading.RLock()

    def default_project_id(self) -> str:
        row = self.conn.execute(
            "SELECT id FROM projects WHERE archived = 0 ORDER BY pinned DESC, created_at LIMIT 1"
        ).fetchone()
        return str(row["id"]) if row else "default"

    def list_projects(self, include_archived: bool = False) -> list[dict[str, Any]]:
        sql = """
            SELECT p.*,
                   COUNT(s.id) AS session_count,
                   COALESCE(SUM(CASE WHEN s.archived = 0 THEN 1 ELSE 0 END), 0) AS active_session_count
            FROM projects p
            LEFT JOIN sessions s ON s.project_id = p.id
                AND s.source = 'gui'
                AND s.scope = 'project'
                AND NOT EXISTS (
                    SELECT 1 FROM memory_session_tombstones t WHERE t.session_id = s.id
                )
        """
        params: list[Any] = []
        if not include_archived:
            sql += " WHERE p.archived = 0"
        sql += """
            GROUP BY p.id
            ORDER BY p.pinned DESC, p.updated_at DESC, p.name ASC
        """
        return [dict(r) for r in self.conn.execute(sql, params)]

    @_serialized_write
    def create_project(self, name: str, path: str = "") -> dict[str, Any]:
        pid = str(uuid.uuid4())
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO projects (id, name, path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (pid, name.strip() or "新项目", path.strip(), now, now),
        )
        self.conn.commit()
        return self.get_project(pid) or {"id": pid}

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM projects WHERE id = ? OR id LIKE ?",
            (project_id, f"{project_id}%"),
        ).fetchone()
        return _rowdict(row)

    def _active_memory_scope(self, session: dict[str, Any]) -> ActiveMemoryScope:
        if not session or session.get("scope") != "project":
            return ActiveMemoryScope.for_session(session_id=session.get("id") if session else None)
        project_id = str(session.get("project_id") or "").strip()
        project = self.get_project(project_id) if project_id else None
        workspace_root = str(project.get("path") or "").strip() if project else None
        return ActiveMemoryScope.for_session(
            session_id=str(session.get("id") or ""),
            project_id=project_id or None,
            workspace_root=workspace_root or None,
        )

    @_serialized_write
    def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        path: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if project is None:
            raise ValueError(f"Project not found: {project_id}")
        values = {
            "name": name if name is not None else project["name"],
            "path": path if path is not None else project["path"],
            "pinned": int(pinned) if pinned is not None else int(project["pinned"]),
            "archived": int(archived) if archived is not None else int(project["archived"]),
        }
        self.conn.execute(
            """
            UPDATE projects
            SET name = ?, path = ?, pinned = ?, archived = ?, updated_at = ?
            WHERE id = ?
            """,
            (values["name"], values["path"], values["pinned"], values["archived"], now_iso(), project["id"]),
        )
        self.conn.commit()
        return self.get_project(project["id"]) or project

    @_serialized_write
    def create_session(
        self,
        title: str | None = None,
        source: str = "gui",
        project_id: str | None = None,
        scope: str = "project",
    ) -> dict[str, Any]:
        sid = str(uuid.uuid4())
        now = now_iso()
        next_scope = scope if scope in {"project", "chat"} else "project"
        pid = project_id or self.default_project_id()
        if not self.get_project(pid):
            pid = self.default_project_id()
        self.conn.execute(
            """
            INSERT INTO sessions (id, project_id, title, source, scope, started_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (sid, pid, title or "新的对话", source, next_scope, now, now),
        )
        if next_scope == "project":
            self.conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, pid))
        self.conn.commit()
        return self.get_session(sid) or {"id": sid}

    def list_sessions(
        self,
        source: str | None = None,
        limit: int = 50,
        project_id: str | None = None,
        scope: str | None = None,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        sql = """SELECT s.* FROM sessions s
                 WHERE NOT EXISTS (
                     SELECT 1 FROM memory_session_tombstones t WHERE t.session_id = s.id
                 )"""
        params: list[Any] = []
        where: list[str] = []
        if source:
            where.append("s.source = ?")
            params.append(source)
        if project_id:
            where.append("s.project_id = ?")
            params.append(project_id)
        if scope:
            where.append("s.scope = ?")
            params.append(scope)
        if not include_archived:
            where.append("s.archived = 0")
        if where:
            sql += " AND " + " AND ".join(where)
        sql += " ORDER BY s.pinned DESC, s.updated_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params)]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT s.* FROM sessions s
               WHERE (s.id = ? OR s.id LIKE ?)
                 AND NOT EXISTS (
                     SELECT 1 FROM memory_session_tombstones t WHERE t.session_id = s.id
                 )""",
            (session_id, f"{session_id}%"),
        ).fetchone()
        return _rowdict(row)

    def _get_session_any(self, session_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id = ? OR id LIKE ?",
            (session_id, f"{session_id}%"),
        ).fetchone()
        return _rowdict(row)

    def get_messages(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        session = self.get_session(session_id)
        if session is None:
            return []
        sql = "SELECT id, turn_idx, ts, role, content, private, metadata FROM messages WHERE session_id = ? ORDER BY turn_idx"
        params: list[Any] = [session["id"]]
        if limit:
            sql += " DESC LIMIT ?"
            params.append(limit)
            rows = list(self.conn.execute(sql, params))
            return [self._decode_message(r) for r in reversed(rows)]
        return [self._decode_message(r) for r in self.conn.execute(sql, params)]

    @_serialized_write
    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        private: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        next_idx = int(session["turn_count"])
        now = now_iso()
        cursor = self.conn.execute(
            """
            INSERT INTO messages (session_id, turn_idx, ts, role, content, private, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["id"],
                next_idx,
                now,
                role,
                content,
                1 if private else 0,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        self.conn.execute(
            """INSERT INTO memory_ingest_outbox (message_id, created_at, updated_at)
               VALUES (?, ?, ?)""",
            (cursor.lastrowid, now, now),
        )
        title = session["title"]
        if next_idx == 0 and role == "user":
            title = _session_title(content)
        self.conn.execute(
            "UPDATE sessions SET turn_count = ?, updated_at = ?, title = ? WHERE id = ?",
            (next_idx + 1, now, title, session["id"]),
        )
        if session.get("scope") == "project":
            self.conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, session["project_id"]))
        self.conn.commit()

        memory_capture = self.retry_pending_memory_ingest(message_id=int(cursor.lastrowid), limit=1)

        # 用户消息进入倾向维护闭环；engine 内部决定是否 observe/compile。
        if role == "user" and not private:
            sess_id = session["id"]
            active_scope = self._active_memory_scope(session)
            msg = content
            threading.Thread(
                target=_maintain_tendency_message,
                args=(self.memory_engine, sess_id, active_scope, next_idx, msg),
                daemon=True,
            ).start()

        return {
            "id": cursor.lastrowid,
            "session_id": session["id"],
            "turn_idx": next_idx,
            "ts": now,
            "role": role,
            "content": content,
            "private": private,
            "metadata": metadata or {},
            "memory_capture": memory_capture,
        }

    def pending_memory_ingest_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM memory_ingest_outbox").fetchone()
        return int(row[0]) if row else 0

    @_serialized_write
    def retry_pending_memory_ingest(
        self,
        *,
        message_id: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        where = """WHERE NOT EXISTS (
                       SELECT 1 FROM memory_session_tombstones t WHERE t.session_id = s.id
                   )"""
        if message_id is not None:
            where += " AND o.message_id = ?"
        params: list[Any] = [message_id] if message_id is not None else []
        params.append(limit)
        rows = self.conn.execute(
            f"""SELECT o.message_id, m.session_id, m.turn_idx, m.role, m.content, m.private,
                       m.metadata AS message_metadata, s.project_id, s.scope
                FROM memory_ingest_outbox o
                JOIN messages m ON m.id = o.message_id
                JOIN sessions s ON s.id = m.session_id
                {where}
                ORDER BY o.created_at
                LIMIT ?""",
            params,
        ).fetchall()
        captured = 0
        errors: list[str] = []
        for row in rows:
            result = self.memory_engine.capture_material(
                source_type="message",
                content=row["content"],
                role=row["role"],
                session_id=row["session_id"],
                project_id=row["project_id"] if row["scope"] == "project" else None,
                turn_idx=row["turn_idx"],
                private=bool(row["private"]),
                metadata=self._json_obj(row["message_metadata"]),
            )
            if result.ok:
                self.conn.execute("DELETE FROM memory_ingest_outbox WHERE message_id = ?", (row["message_id"],))
                captured += 1
                continue
            error = result.error or "memory capture failed"
            errors.append(error)
            self.conn.execute(
                """UPDATE memory_ingest_outbox
                   SET attempts = attempts + 1, last_error = ?, updated_at = ?
                   WHERE message_id = ?""",
                (error, now_iso(), row["message_id"]),
            )
        self.conn.commit()
        return {
            "ok": not errors,
            "processed": len(rows),
            "captured": captured,
            "pending": self.pending_memory_ingest_count(),
            "errors": errors,
        }

    @_serialized_write
    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        pid = project_id if project_id is not None else session["project_id"]
        if not self.get_project(pid):
            raise ValueError(f"Project not found: {pid}")
        next_title = title if title is not None else session["title"]
        next_pinned = int(pinned) if pinned is not None else int(session["pinned"])
        next_archived = int(archived) if archived is not None else int(session["archived"])
        now = now_iso()
        self.conn.execute(
            """
            UPDATE sessions
            SET title = ?, pinned = ?, archived = ?, project_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (next_title, next_pinned, next_archived, pid, now, session["id"]),
        )
        if session.get("scope") == "project":
            self.conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, pid))
        self.conn.commit()
        return self.get_session(session["id"]) or session

    @_serialized_write
    def delete_session(
        self,
        session_id: str,
        *,
        mutation_context: MemoryMutationContext | None = None,
    ) -> SessionMutationResult:
        if mutation_context is None:
            return SessionMutationResult(False, "delete_session", target_id=session_id, error="confirmation required")
        if error := mutation_context.authorization_error("session.delete", session_id):
            return SessionMutationResult(False, "delete_session", target_id=session_id, error=error)
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            session = self.get_session(session_id)
            if session is None:
                self.conn.rollback()
                return SessionMutationResult(False, "delete_session", target_id=session_id, error="session not found")
            target_id = str(session["id"])
            snapshot = self._session_snapshot(target_id)
            version_id = self._save_session_version(target_id, snapshot, "delete session")
            now = now_iso()
            self.conn.execute(
                "INSERT INTO memory_session_tombstones (session_id, deleted_at, version_id) VALUES (?, ?, ?)",
                (target_id, now, version_id),
            )
            changed = self.conn.execute(
                "UPDATE sessions SET archived = 1, updated_at = ? WHERE id = ?",
                (now, target_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("no rows changed")
            if session.get("scope") == "project":
                self.conn.execute(
                    "UPDATE projects SET updated_at = ? WHERE id = ?",
                    (now, session["project_id"]),
                )
            audit_id = self._record_audit(
                action="delete_session",
                target_id=target_id,
                target_type="session",
                source_session_id=target_id,
                cycle_no=None,
                reason="hide session and linked memory materials",
                backup_path=version_id,
                details={"changed_rows": 1},
            )
            self.conn.commit()
            return SessionMutationResult(
                True,
                "delete_session",
                target_id=target_id,
                changed_rows=1,
                version_id=version_id,
                audit_id=audit_id,
            )
        except Exception as exc:
            self.conn.rollback()
            return SessionMutationResult(
                False,
                "delete_session",
                target_id=session_id,
                error=str(exc),
            )

    @_serialized_write
    def restore_session_version(
        self,
        session_id: str,
        version: str,
        *,
        mutation_context: MemoryMutationContext | None = None,
    ) -> SessionMutationResult:
        if mutation_context is None:
            return SessionMutationResult(False, "restore_session", target_id=session_id, error="confirmation required")
        if error := mutation_context.authorization_error("session.restore", session_id):
            return SessionMutationResult(False, "restore_session", target_id=session_id, error=error)
        try:
            version_id = int(version.removeprefix("session_version://"))
        except (TypeError, ValueError):
            return SessionMutationResult(False, "restore_session", target_id=session_id, error="invalid session version")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            session = self._get_session_any(session_id)
            if session is None:
                self.conn.rollback()
                return SessionMutationResult(False, "restore_session", target_id=session_id, error="session not found")
            target_id = str(session["id"])
            row = self.conn.execute(
                "SELECT * FROM session_versions WHERE id = ? AND session_id = ?",
                (version_id, target_id),
            ).fetchone()
            if not row:
                self.conn.rollback()
                return SessionMutationResult(False, "restore_session", target_id=target_id, error="session version not found")
            snapshot = json.loads(row["snapshot"] or "{}")
            current_snapshot = self._session_snapshot(target_id)
            if current_snapshot == snapshot:
                self.conn.rollback()
                return SessionMutationResult(False, "restore_session", target_id=target_id, error="no changes")
            backup_version_id = self._save_session_version(
                target_id,
                current_snapshot,
                f"before restoring session version {version_id}",
            )
            self._restore_session_snapshot(snapshot)
            audit_id = self._record_audit(
                action="restore_session",
                target_id=target_id,
                target_type="session",
                source_session_id=target_id,
                cycle_no=None,
                reason=f"restore session version {version_id}",
                backup_path=backup_version_id,
                details={"changed_rows": 1, "restored_version": f"session_version://{version_id}"},
            )
            self.conn.commit()
            return SessionMutationResult(
                True,
                "restore_session",
                target_id=target_id,
                changed_rows=1,
                version_id=backup_version_id,
                audit_id=audit_id,
            )
        except Exception as exc:
            self.conn.rollback()
            return SessionMutationResult(False, "restore_session", target_id=session_id, error=str(exc))

    def _session_snapshot(self, session_id: str) -> dict[str, Any]:
        session = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        tombstone = self.conn.execute(
            "SELECT * FROM memory_session_tombstones WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return {"session": dict(session), "tombstone": _rowdict(tombstone)}

    def _save_session_version(self, session_id: str, snapshot: dict[str, Any], reason: str) -> str:
        cursor = self.conn.execute(
            """INSERT INTO session_versions (session_id, snapshot, saved_at, reason)
               VALUES (?, ?, ?, ?)""",
            (session_id, json.dumps(snapshot, ensure_ascii=False), now_iso(), reason),
        )
        return f"session_version://{cursor.lastrowid}"

    def _restore_session_snapshot(self, snapshot: dict[str, Any]) -> None:
        session = snapshot.get("session") or {}
        session_id = str(session.get("id") or "")
        if not session_id:
            raise ValueError("invalid session snapshot")
        columns = [column for column in session if column != "id"]
        changed = self.conn.execute(
            f"UPDATE sessions SET {','.join(f'{column} = ?' for column in columns)} WHERE id = ?",
            [session[column] for column in columns] + [session_id],
        ).rowcount
        if changed != 1:
            raise RuntimeError("no rows changed")
        tombstone = snapshot.get("tombstone")
        if tombstone:
            self.conn.execute(
                """INSERT OR REPLACE INTO memory_session_tombstones
                       (session_id, deleted_at, version_id) VALUES (?, ?, ?)""",
                (session_id, tombstone["deleted_at"], tombstone["version_id"]),
            )
        else:
            self.conn.execute("DELETE FROM memory_session_tombstones WHERE session_id = ?", (session_id,))

    def recent_context(self, session_id: str, limit: int = 8) -> str:
        session = self.get_session(session_id)
        messages = self.get_messages(session_id, limit=limit)
        parts: list[str] = []
        if session and session.get("rolling_summary"):
            parts.append("## 当前会话滚动摘要\n" + session["rolling_summary"])
        if messages:
            parts.append("## 最近对话")
            for m in messages:
                role = "用户" if m["role"] == "user" else "IwIw"
                parts.append(f"[{role}] {m['content']}")
        return "\n".join(parts)

    @_serialized_write
    def start_agent_run(
        self,
        session_id: str,
        *,
        intent: str,
        path: str,
        reason: str,
        context_sections: list[str] | None = None,
        model_role: str = "chat",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        run_id = str(uuid.uuid4())
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO agent_runs
                (id, session_id, status, intent, path, reason, model_role, started_at, context_sections, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                session["id"],
                "running",
                intent,
                path,
                reason,
                model_role,
                now,
                json.dumps(context_sections or [], ensure_ascii=False),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return self.get_agent_run(run_id) or {"id": run_id, "session_id": session["id"]}

    @_serialized_write
    def record_agent_event(
        self,
        run_id: str,
        *,
        event_type: str,
        title: str = "",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = self.get_agent_run(run_id)
        if run is None:
            raise ValueError(f"Agent run not found: {run_id}")
        now = now_iso()
        cursor = self.conn.execute(
            """
            INSERT INTO agent_events (run_id, session_id, ts, type, title, details)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run["id"],
                run["session_id"],
                now,
                event_type,
                title,
                json.dumps(details or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM agent_events WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        return dict(row) if row else {"id": cursor.lastrowid, "run_id": run["id"]}

    @_serialized_write
    def finish_agent_run(
        self,
        run_id: str,
        *,
        status: str = "completed",
        metadata: dict[str, Any] | None = None,
        error: str = "",
    ) -> dict[str, Any]:
        run = self.get_agent_run(run_id)
        if run is None:
            raise ValueError(f"Agent run not found: {run_id}")
        merged = self._json_obj(run.get("metadata"))
        if metadata:
            merged.update(metadata)
        self.conn.execute(
            """
            UPDATE agent_runs
            SET status = ?, ended_at = ?, metadata = ?, error = ?
            WHERE id = ?
            """,
            (
                status,
                now_iso(),
                json.dumps(merged, ensure_ascii=False),
                error,
                run["id"],
            ),
        )
        self.conn.commit()
        return self.get_agent_run(run["id"]) or run

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM agent_runs WHERE id = ? OR id LIKE ?",
            (run_id, f"{run_id}%"),
        ).fetchone()
        return self._decode_agent_run(row)

    def list_agent_runs(self, session_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM agent_runs"
        params: list[Any] = []
        if session_id:
            sql += " WHERE session_id = ?"
            params.append(session_id)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        return [self._decode_agent_run(row) for row in self.conn.execute(sql, params)]

    def list_agent_events(self, run_id: str) -> list[dict[str, Any]]:
        run = self.get_agent_run(run_id)
        if run is None:
            return []
        events = []
        for row in self.conn.execute(
            "SELECT * FROM agent_events WHERE run_id = ? ORDER BY id",
            (run["id"],),
        ):
            event = dict(row)
            event["details"] = self._json_obj(event.get("details"))
            events.append(event)
        return events

    @_serialized_write
    def record_agent_timeline(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
        kind: str,
        title: str,
        summary: str = "",
        risk_level: str = "low",
        reversible: bool = False,
        status: str = "recorded",
        rollback_ref: str = "",
        details: dict[str, Any] | None = None,
        turn_idx: int | None = None,
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        if run_id and self.get_agent_run(run_id) is None:
            raise ValueError(f"Agent run not found: {run_id}")
        if turn_idx is None:
            row = self.conn.execute(
                "SELECT MAX(turn_idx) AS turn_idx FROM messages WHERE session_id = ?",
                (session["id"],),
            ).fetchone()
            turn_idx = int(row["turn_idx"]) if row and row["turn_idx"] is not None else None
        cursor = self.conn.execute(
            """
            INSERT INTO agent_timeline
                (session_id, run_id, turn_idx, ts, kind, title, summary, risk_level,
                 reversible, status, rollback_ref, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["id"],
                run_id,
                turn_idx,
                now_iso(),
                kind,
                title,
                summary,
                risk_level,
                1 if reversible else 0,
                status,
                rollback_ref,
                json.dumps(details or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM agent_timeline WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        return self._decode_timeline_event(row) or {"id": cursor.lastrowid, "session_id": session["id"]}

    def list_agent_timeline(
        self,
        session_id: str | None = None,
        *,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM agent_timeline"
        params: list[Any] = []
        clauses: list[str] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [self._decode_timeline_event(row) for row in self.conn.execute(sql, params)]

    def _decode_agent_run(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        run = dict(row)
        run["context_sections"] = self._json_list(run.get("context_sections"))
        run["metadata"] = self._json_obj(run.get("metadata"))
        return run

    def _decode_timeline_event(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        event = dict(row)
        event["reversible"] = bool(event.get("reversible"))
        event["details"] = self._json_obj(event.get("details"))
        return event

    def _decode_message(self, row: sqlite3.Row) -> dict[str, Any]:
        message = dict(row)
        message["private"] = bool(message.get("private"))
        message["metadata"] = self._json_obj(message.get("metadata"))
        return message

    def _json_obj(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        try:
            data = json.loads(str(value))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _json_list(self, value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if not value:
            return []
        try:
            data = json.loads(str(value))
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def search(self, query: str, limit: int = 20, source: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT m.session_id, m.turn_idx, m.ts, m.role, s.title, s.source,
                   snippet(messages_fts, 0, '>>>', '<<<', '...', 24) AS snippet
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            JOIN sessions s ON s.id = m.session_id
            WHERE messages_fts MATCH ?
              AND NOT EXISTS (
                  SELECT 1 FROM memory_session_tombstones t WHERE t.session_id = s.id
              )
        """
        params: list[Any] = [query]
        if source:
            sql += " AND s.source = ?"
            params.append(source)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        try:
            return [dict(r) for r in self.conn.execute(sql, params)]
        except sqlite3.OperationalError as exc:
            return [{"error": f"FTS query error: {exc}"}]

    def get_summary(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        cycles = [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM cycle_summaries WHERE session_id = ? ORDER BY cycle_no",
                (session_id,),
            )
        ]
        return {
            "session": session,
            "rolling_summary": session.get("rolling_summary", "") if session else "",
            "cycles": cycles,
        }

    def should_consolidate(
        self,
        session_id: str,
        *,
        turn_limit: int = 12,
        token_limit: int = 2000,
    ) -> bool:
        session = self.get_session(session_id)
        if session is None:
            return False
        start_idx = int(session.get("last_consolidated_turn", -1)) + 1
        pending = [
            message for message in self.get_messages(str(session["id"]))
            if message["turn_idx"] >= start_idx and not message.get("private")
        ]
        return len(pending) >= turn_limit or sum(
            _estimate_tokens(str(message.get("content") or "")) for message in pending
        ) >= token_limit

    @_serialized_write
    def consolidate(self, session_id: str, force: bool = False) -> ConsolidationResult:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        all_messages = self.get_messages(session["id"])
        start_idx = int(session.get("last_consolidated_turn", -1)) + 1
        pending = [m for m in all_messages if m["turn_idx"] >= start_idx and not m.get("private")]
        if not pending and not force:
            return ConsolidationResult(
                cycle_no=int(session["cycle_count"]),
                idx_from=start_idx,
                idx_to=start_idx - 1,
                summary=session.get("rolling_summary", ""),
                memory_result={"saved": [], "skipped": True, "reason": "no pending public messages"},
            )

        idx_to = pending[-1]["turn_idx"] if pending else int(session["turn_count"]) - 1
        cycle_no = int(session["cycle_count"]) + 1
        summary = self._build_summary(session.get("rolling_summary", ""), pending)
        memory_result = self._maintain_tendency_sync(session, cycle_no, summary, pending)
        now = now_iso()

        self.conn.execute(
            """
            INSERT INTO cycle_summaries
                (session_id, cycle_no, idx_from, idx_to, summary, memory_result, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session["id"], cycle_no, start_idx, idx_to, summary, json.dumps(memory_result, ensure_ascii=False), now),
        )
        self.conn.execute(
            """
            UPDATE sessions
            SET rolling_summary = ?, last_consolidated_turn = ?, cycle_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (summary, idx_to, cycle_no, now, session["id"]),
        )
        self._record_audit(
            action="consolidate",
            target_id=session["id"],
            target_type="session",
            source_session_id=session["id"],
            cycle_no=cycle_no,
            reason="周期整理会话并触发长期记忆维护",
            details={"memory_result": memory_result, "idx_from": start_idx, "idx_to": idx_to},
        )
        self.conn.commit()
        return ConsolidationResult(cycle_no, start_idx, idx_to, summary, memory_result)

    def _build_summary(self, previous: str, pending: list[dict[str, Any]]) -> str:
        lines = []
        if previous:
            lines.append(previous.strip())
        if pending:
            lines.append(f"第 {len(lines) + 1} 段整理：")
            for m in pending[-12:]:
                role = "用户" if m["role"] == "user" else "IwIw"
                text = " ".join(str(m["content"]).split())
                lines.append(f"- {role}: {text[:220]}")
        return "\n".join(lines).strip()[:6000]

    def _maintain_tendency_sync(
        self,
        session: dict[str, Any],
        cycle_no: int,
        summary: str,
        pending: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not pending:
            active_scope = self._active_memory_scope(session)

            async def _compile() -> dict[str, Any]:
                return await self.memory_engine.compile_tendency(
                    scope_kind="session",
                    scope_key=session["id"],
                    workspace_id=active_scope.workspace_id,
                    project_id=active_scope.project_id,
                    session_id=session["id"],
                )

            try:
                compiled = asyncio.run(asyncio.wait_for(_compile(), timeout=max(EXTRACT_TIMEOUT + 10, 30)))
            except Exception as exc:
                return {"saved": [], "observed_ids": [], "compiled": [], "error": str(exc)}
            return {
                "saved": [f"compile:{compiled['profile_id']}"] if compiled.get("ok") and compiled.get("profile_id") else [],
                "observed_ids": [],
                "compiled": [compiled],
                "error": "" if compiled.get("ok") else str(compiled.get("error") or ""),
            }
        conversation = "\n".join(
            f"[{m['role']}]: {m['content']}" for m in pending if m["role"] in {"user", "assistant"}
        )
        message = (
            "以下是一段 IwIw 会话的周期整理内容。只提取稳定的行为倾向或项目协作原则，"
            "不要把事实改写为倾向。\n\n"
            f"## 周期摘要\n{summary}\n\n## 原始片段\n{conversation}"
        )

        active_scope = self._active_memory_scope(session)
        turn_range = f"{pending[0]['turn_idx']}-{pending[-1]['turn_idx']}"

        async def _run() -> dict[str, Any]:
            result = await self.memory_engine.maintain_tendencies(
                text=message,
                context=f"source_session_id={session['id']}; cycle_no={cycle_no}",
                session_id=session["id"],
                active_scope=active_scope,
                turn_range=turn_range,
                force_compile=True,
            )
            return result.to_dict()

        try:
            result = asyncio.run(asyncio.wait_for(_run(), timeout=max(EXTRACT_TIMEOUT + 10, 30)))
            saved = [f"observe:{obs_id}" for obs_id in result.get("observed_ids", [])]
            for item in result.get("compiled", []):
                if item.get("profile_id"):
                    saved.append(f"{item.get('action', 'compile')}:{item['profile_id']}")
                self._record_audit(
                    action="tendency_maintenance",
                    target_id=item.get("profile_id"),
                    target_type="tendency_profile",
                    source_session_id=session["id"],
                    cycle_no=cycle_no,
                    reason="周期整理触发倾向维护",
                    details=item,
                )
            result["saved"] = saved
            return result
        except Exception as exc:
            return {"saved": [], "observed_ids": [], "compiled": [], "error": str(exc)}

    def _record_audit(
        self,
        *,
        action: str,
        target_id: str | None,
        target_type: str = "session_event",
        source_session_id: str | None,
        cycle_no: int | None,
        reason: str | None,
        details: dict[str, Any] | None = None,
        backup_path: str | None = None,
    ) -> str:
        audit_details = dict(details or {})
        if cycle_no is not None:
            audit_details.setdefault("cycle_no", cycle_no)
        audit_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO memory_audit
                (id, ts, action, target_id, target_type, source_session_id, reason, backup_path, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                now_iso(),
                action,
                target_id,
                target_type,
                source_session_id,
                reason,
                backup_path,
                json.dumps(audit_details, ensure_ascii=False),
            ),
        )
        return audit_id

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM memory_audit ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
        ]

def _trigger_context(session_id: str, limit: int = 8) -> str:
    try:
        service = SessionMemoryService()
        messages = service.get_messages(session_id, limit=limit)
    except Exception:
        return f"source_session_id={session_id}"
    lines = [f"source_session_id={session_id}", "## 最近会话上下文"]
    for item in messages:
        if item.get("private"):
            continue
        role = item.get("role", "unknown")
        content = " ".join(str(item.get("content") or "").split())
        if content:
            lines.append(f"[{role}]: {content[:500]}")
    return "\n".join(lines)


def _maintain_tendency_message(
    memory_engine: MemoryEngine,
    session_id: str,
    active_scope: ActiveMemoryScope,
    turn_idx: int,
    message: str,
) -> None:
    """Background tendency maintenance; raw message ingest already happened."""
    import asyncio
    import logging

    logger = logging.getLogger("selfecho_session.trigger")
    try:
        # ponytail: one global lock is enough for the local single-user app;
        # move to a durable worker if tendency throughput becomes measurable.
        with _TENDENCY_LOCK:
            result = asyncio.run(
                memory_engine.maintain_tendencies(
                    text=message,
                    session_id=session_id,
                    active_scope=active_scope,
                    turn_range=str(turn_idx),
                    context=_trigger_context(session_id),
                )
            )
        data = result.to_dict()
        if data.get("observed_ids") or data.get("compiled"):
            logger.info("Tendency maintenance result: %s", data)
    except Exception as e:
        logger.warning("Tendency maintenance failed: %s", e)
    finally:
        from memory_agent import db as memory_db

        memory_db.close_current_thread()
