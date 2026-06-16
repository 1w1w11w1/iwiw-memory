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
        return "树洞会话"
    return clean[:24] + ("..." if len(clean) > 24 else "")


@dataclass
class ConsolidationResult:
    cycle_no: int
    idx_from: int
    idx_to: int
    summary: str
    memory_result: dict[str, Any]


class SessionMemoryService:
    """SQLite-backed SelfEcho session memory and replay service."""

    def __init__(self) -> None:
        self.conn = connect()

    def create_session(self, title: str | None = None, source: str = "gui") -> dict[str, Any]:
        sid = str(uuid.uuid4())
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO sessions (id, title, source, started_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sid, title or "新的树洞会话", source, now, now),
        )
        self.conn.commit()
        return self.get_session(sid) or {"id": sid}

    def list_sessions(self, source: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sessions"
        params: list[Any] = []
        if source:
            sql += " WHERE source = ?"
            params.append(source)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params)]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id = ? OR id LIKE ?",
            (session_id, f"{session_id}%"),
        ).fetchone()
        return _rowdict(row)

    def get_messages(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT turn_idx, ts, role, content, private FROM messages WHERE session_id = ? ORDER BY turn_idx"
        params: list[Any] = [session_id]
        if limit:
            sql += " DESC LIMIT ?"
            params.append(limit)
            rows = list(self.conn.execute(sql, params))
            return [dict(r) for r in reversed(rows)]
        return [dict(r) for r in self.conn.execute(sql, params)]

    def append_message(self, session_id: str, role: str, content: str, private: bool = False) -> dict[str, Any]:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        next_idx = int(session["turn_count"])
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO messages (session_id, turn_idx, ts, role, content, private)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session["id"], next_idx, now, role, content, 1 if private else 0),
        )
        title = session["title"]
        if next_idx == 0 and role == "user":
            title = _session_title(content)
        self.conn.execute(
            "UPDATE sessions SET turn_count = ?, updated_at = ?, title = ? WHERE id = ?",
            (next_idx + 1, now, title, session["id"]),
        )
        self.conn.commit()
        return {
            "session_id": session["id"],
            "turn_idx": next_idx,
            "ts": now,
            "role": role,
            "content": content,
            "private": private,
        }

    def recent_context(self, session_id: str, limit: int = 8) -> str:
        session = self.get_session(session_id)
        messages = self.get_messages(session_id, limit=limit)
        parts: list[str] = []
        if session and session.get("rolling_summary"):
            parts.append("## 当前会话滚动摘要\n" + session["rolling_summary"])
        if messages:
            parts.append("## 最近对话")
            for m in messages:
                role = "用户" if m["role"] == "user" else "SelfEcho"
                parts.append(f"[{role}] {m['content']}")
        return "\n".join(parts)

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
                role = "用户" if m["role"] == "user" else "SelfEcho"
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
        message = f"以下是一段 SelfEcho 树洞会话的周期整理内容，请按长期记忆策略维护记忆。\n\n## 周期摘要\n{summary}\n\n## 原始片段\n{conversation}"

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
            reason="导入旧历史库到 SelfEcho 会话记忆层",
            details={"sessions": imported_sessions, "messages": imported_messages},
        )
        self.conn.commit()
        return {"ok": True, "sessions": imported_sessions, "messages": imported_messages}
