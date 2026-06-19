from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memory_agent.config import EXTRACT_TIMEOUT
from memory_agent.extractor import extract_and_save

from .config import LEGACY_DB_PATH
from .db import connect, now_iso


def _rowdict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _session_title(text: str) -> str:
    clean = " ".join(text.strip().split())
    if not clean:
        return "新对话"
    return clean[:24] + ("..." if len(clean) > 24 else "")


@dataclass
class ConsolidationResult:
    cycle_no: int
    idx_from: int
    idx_to: int
    summary: str
    memory_result: dict[str, Any]


class SessionMemoryService:
    """SQLite-backed IwIw session memory and replay service."""

    def __init__(self) -> None:
        self.conn = connect()

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
            LEFT JOIN sessions s ON s.project_id = p.id AND s.source = 'gui' AND s.scope = 'project'
        """
        params: list[Any] = []
        if not include_archived:
            sql += " WHERE p.archived = 0"
        sql += """
            GROUP BY p.id
            ORDER BY p.pinned DESC, p.updated_at DESC, p.name ASC
        """
        return [dict(r) for r in self.conn.execute(sql, params)]

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
        sql = "SELECT * FROM sessions"
        params: list[Any] = []
        where: list[str] = []
        if source:
            where.append("source = ?")
            params.append(source)
        if project_id:
            where.append("project_id = ?")
            params.append(project_id)
        if scope:
            where.append("scope = ?")
            params.append(scope)
        if not include_archived:
            where.append("archived = 0")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY pinned DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params)]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id = ? OR id LIKE ?",
            (session_id, f"{session_id}%"),
        ).fetchone()
        return _rowdict(row)

    def get_messages(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id, turn_idx, ts, role, content, private, metadata FROM messages WHERE session_id = ? ORDER BY turn_idx"
        params: list[Any] = [session_id]
        if limit:
            sql += " DESC LIMIT ?"
            params.append(limit)
            rows = list(self.conn.execute(sql, params))
            return [self._decode_message(r) for r in reversed(rows)]
        return [self._decode_message(r) for r in self.conn.execute(sql, params)]

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
        return {
            "id": cursor.lastrowid,
            "session_id": session["id"],
            "turn_idx": next_idx,
            "ts": now,
            "role": role,
            "content": content,
            "private": private,
            "metadata": metadata or {},
        }

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

    def delete_session(self, session_id: str) -> bool:
        session = self.get_session(session_id)
        if session is None:
            return False
        self.conn.execute("DELETE FROM sessions WHERE id = ?", (session["id"],))
        if session.get("scope") == "project":
            self.conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now_iso(), session["project_id"]))
        self.conn.commit()
        return True

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
        memory_result = self._extract_memories_sync(session["id"], cycle_no, summary, pending)
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
            target_slug=None,
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

    def _extract_memories_sync(
        self,
        session_id: str,
        cycle_no: int,
        summary: str,
        pending: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not pending:
            return {"saved": []}
        conversation = "\n".join(
            f"[{m['role']}]: {m['content']}" for m in pending if m["role"] in {"user", "assistant"}
        )
        message = f"以下是一段 IwIw 会话的周期整理内容，请按长期记忆策略维护记忆。\n\n## 周期摘要\n{summary}\n\n## 原始片段\n{conversation}"

        async def _run() -> list[str]:
            return await extract_and_save(message, context=f"source_session_id={session_id}; cycle_no={cycle_no}")

        try:
            saved = asyncio.run(asyncio.wait_for(_run(), timeout=max(EXTRACT_TIMEOUT + 5, 20)))
            for item in saved:
                slug = item.split(":", 1)[-1]
                self._record_audit(
                    action=item.split(":", 1)[0] if ":" in item else "memory_write",
                    target_slug=slug,
                    source_session_id=session_id,
                    cycle_no=cycle_no,
                    reason="周期整理自动写入长期记忆",
                    details={"raw_result": item},
                )
            return {"saved": saved}
        except Exception as exc:
            return {"saved": [], "error": str(exc)}

    def _record_audit(
        self,
        *,
        action: str,
        target_slug: str | None,
        source_session_id: str | None,
        cycle_no: int | None,
        reason: str | None,
        details: dict[str, Any] | None = None,
        backup_path: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO memory_audit
                (id, ts, action, target_slug, source_session_id, cycle_no, reason, backup_path, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                now_iso(),
                action,
                target_slug,
                source_session_id,
                cycle_no,
                reason,
                backup_path,
                json.dumps(details or {}, ensure_ascii=False),
            ),
        )

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM memory_audit ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
        ]

    def migrate_legacy(self, legacy_path: Path | None = None) -> dict[str, Any]:
        legacy = legacy_path or LEGACY_DB_PATH
        if not legacy.exists():
            return {"ok": False, "error": f"Legacy DB not found: {legacy}"}

        old = sqlite3.connect(legacy)
        old.row_factory = sqlite3.Row
        sessions = list(old.execute("SELECT * FROM sessions"))
        imported_sessions = 0
        imported_messages = 0

        for s in sessions:
            sid = s["id"]
            exists = self.conn.execute("SELECT 1 FROM sessions WHERE id = ?", (sid,)).fetchone()
            if not exists:
                self.conn.execute(
                    """
                    INSERT INTO sessions
                        (id, title, source, started_at, updated_at, turn_count, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sid,
                        s["ai_title"] or "(导入历史)",
                        "legacy_claude_code",
                        s["started_at"] or now_iso(),
                        s["ended_at"] or s["started_at"] or now_iso(),
                        0,
                        json.dumps({"project": s["project"], "cwd": s["cwd"]}, ensure_ascii=False),
                    ),
                )
                imported_sessions += 1

            existing_count = self.conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                (sid,),
            ).fetchone()[0]
            if existing_count:
                continue
            rows = list(old.execute("SELECT turn_idx, ts, role, text FROM turns WHERE session_id = ? ORDER BY turn_idx", (sid,)))
            for r in rows:
                self.conn.execute(
                    """
                    INSERT INTO messages (session_id, turn_idx, ts, role, content)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (sid, r["turn_idx"], r["ts"] or now_iso(), r["role"], r["text"]),
                )
                imported_messages += 1
            self.conn.execute(
                "UPDATE sessions SET turn_count = ? WHERE id = ?",
                (len(rows), sid),
            )

        self._record_audit(
            action="migrate_legacy",
            target_slug=None,
            source_session_id=None,
            cycle_no=None,
            reason="导入旧历史库到 IwIw 会话记忆层",
            details={"sessions": imported_sessions, "messages": imported_messages},
        )
        self.conn.commit()
        return {"ok": True, "sessions": imported_sessions, "messages": imported_messages}
