"""Memory system evaluation — new architecture tests.

Covers: deterministic ingestion, record CRUD, mutation audit, versions,
tendency profiles, retrieval gate decision, context assembly.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import asyncio
import threading
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import memory_agent.db as memory_db
import memory_agent.embedding as memory_embedding
from memory_agent.engine import CapturedMemory, MemoryEngine, MemoryMutationContext
from memory_agent.scopes import ActiveMemoryScope, workspace_id_from_root
from memory_agent.tendency_compiler import TendencyCompiler
from selfecho_agent.evaluator import HarnessEvaluator
from selfecho_agent.modes import IntentDecision


class FakeModelGateway:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)

    async def complete(self, **kwargs) -> str:
        return next(self.responses)


class IsolatedMemoryDb:
    def __enter__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tmpdir.name) / "sessions.db"
        self.original_path = memory_db.MEMORY_DB_PATH
        self.original_refresh = memory_db.refresh_record_vectors
        memory_db.close()
        memory_db.MEMORY_DB_PATH = self.path
        memory_db.refresh_record_vectors = self._refresh_noop  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        memory_db.close()
        memory_db.MEMORY_DB_PATH = self.original_path
        memory_db.refresh_record_vectors = self.original_refresh  # type: ignore[assignment]
        self._tmpdir.cleanup()

    @staticmethod
    def _refresh_noop(record_id: str, model_version: str = "test") -> dict:
        return {"ok": True, "chunks": 0}


class FakeContextSessionService:
    def __init__(self, scope: str = "chat", project_id: str = "", project_path: str = "") -> None:
        self.scope = scope
        self.project_id = project_id
        self.project_path = project_path

    def get_session(self, session_id: str) -> dict:
        return {"id": session_id, "scope": self.scope, "project_id": self.project_id, "rolling_summary": ""}

    def get_project(self, project_id: str) -> dict | None:
        if project_id == self.project_id and self.project_path:
            return {"id": project_id, "name": "eval-project", "path": self.project_path}
        return None

    def recent_context(self, session_id: str, limit: int = 8) -> str:
        return "用户刚才在聊考试安排。"

    def get_messages(self, session_id: str, limit: int = 6) -> list[dict]:
        return [{"role": "user", "content": "我们刚才提到了考试安排。"}]


def _audit_actions() -> list[str]:
    rows = memory_db.connect().execute(
        "SELECT action FROM memory_audit ORDER BY rowid"
    ).fetchall()
    return [row["action"] for row in rows]


def _version_count() -> int:
    return memory_db.connect().execute(
        "SELECT COUNT(*) FROM memory_versions"
    ).fetchone()[0]


def _mutation_context(
    action: str,
    target_id: str,
    *,
    confirmed: bool = True,
    permission_profile: str = "full_access",
    source: str = "internal",
) -> MemoryMutationContext:
    return MemoryMutationContext(
        permission_profile=permission_profile,
        confirmed=confirmed,
        source=source,
        action=action,
        target_id=target_id,
    )


def test_migrates_head_memory_schema_without_losing_materials() -> None:
    original_path = memory_db.MEMORY_DB_PATH
    memory_db.close()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sessions.db"
        old_conn = sqlite3.connect(db_path)
        old_conn.executescript("""
            CREATE TABLE memories (
                id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL, description TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL, mem_type TEXT NOT NULL DEFAULT 'user', priority TEXT NOT NULL DEFAULT 'normal',
                event_date TEXT, recorded_date TEXT NOT NULL, content_hash TEXT NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0, last_access_at TEXT, embedding_model TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE memory_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT NOT NULL, chunk_index INTEGER NOT NULL,
                chunk_text TEXT NOT NULL, vector BLOB, model_version TEXT
            );
            CREATE TABLE memory_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT, slug TEXT NOT NULL, content TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', mem_type TEXT NOT NULL DEFAULT 'user',
                priority TEXT NOT NULL DEFAULT 'normal', event_date TEXT, saved_at TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE memory_audit (
                id TEXT PRIMARY KEY, ts TEXT NOT NULL, action TEXT NOT NULL, target_slug TEXT,
                source_session_id TEXT, cycle_no INTEGER, reason TEXT, backup_path TEXT,
                details TEXT NOT NULL DEFAULT '{}'
            );
            INSERT INTO memories
                (id, slug, description, content, recorded_date, content_hash, created_at, updated_at)
                VALUES ('legacy-1', 'legacy-note', 'old description', 'legacy material marker',
                        '2026-01-01', 'old-hash', '2026-01-01', '2026-01-01');
            INSERT INTO memory_chunks (memory_id, chunk_index, chunk_text, model_version)
                VALUES ('legacy-1', 0, 'legacy material marker', 'legacy-model');
            INSERT INTO memory_versions (memory_id, slug, content, saved_at)
                VALUES ('legacy-1', 'legacy-note', 'older legacy material', '2025-12-31');
            INSERT INTO memory_audit (id, ts, action, target_slug, backup_path, details)
                VALUES ('legacy-audit', '2026-01-01', 'legacy.update', 'legacy-note', 'version://1', '{}');
        """)
        old_conn.commit()
        old_conn.close()

        memory_db.MEMORY_DB_PATH = db_path
        try:
            conn = memory_db.connect()
            record = conn.execute("SELECT * FROM memory_records WHERE id = 'legacy-1'").fetchone()
            assert record and record["content"] == "legacy material marker"
            assert conn.execute("SELECT COUNT(*) FROM legacy_memory_versions").fetchone()[0] == 1
            imported_version = memory_db.get_record_version("legacy-1", "1")
            assert imported_version and imported_version["content"] == "older legacy material"
            assert conn.execute("SELECT COUNT(*) FROM legacy_memory_chunks").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM memory_chunks WHERE record_id = 'legacy-1'").fetchone()[0] == 1
            audit = conn.execute("SELECT * FROM memory_audit WHERE id = 'legacy-audit'").fetchone()
            assert audit and audit["target_id"] == "legacy-1" and audit["target_type"] == "record"
            assert audit["status"] == "completed"
            captured = memory_db.insert_record(source_type="message", content="new material")
            assert captured.ok and memory_db.get_record(captured.target_id or "")
            recalled = memory_db.search_fts("legacy", limit=5)
            assert any(row["record_id"] == "legacy-1" for row in recalled), recalled
            restored = MemoryEngine().rollback_audit(
                "legacy-audit",
                mutation_context=_mutation_context("memory.rollback", "legacy-audit"),
            )
            assert restored.ok and memory_db.get_record("legacy-1")["content"] == "older legacy material"
            conn.execute("DELETE FROM memory_chunks WHERE record_id = 'legacy-1'")
            conn.execute(
                """INSERT INTO memory_chunks (record_id, chunk_index, chunk_text, model_version)
                   VALUES ('legacy-1', 0, 'refreshed chunk', 'new-model')"""
            )
            conn.commit()
            memory_db.close()
            reconnected = memory_db.connect()
            chunks = reconnected.execute(
                "SELECT chunk_text FROM memory_chunks WHERE record_id = 'legacy-1'"
            ).fetchall()
            assert [row["chunk_text"] for row in chunks] == ["refreshed chunk"], chunks
        finally:
            memory_db.close()
            memory_db.MEMORY_DB_PATH = original_path


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_session_db_migrates_head_audit_schema_in_place() -> None:
    import selfecho_session.db as session_db

    original_path = session_db.DB_PATH
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sessions.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """CREATE TABLE memory_audit (
                id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                action TEXT NOT NULL,
                target_slug TEXT,
                source_session_id TEXT,
                cycle_no INTEGER,
                reason TEXT,
                backup_path TEXT,
                details TEXT NOT NULL DEFAULT '{}'
            )"""
        )
        conn.execute(
            """INSERT INTO memory_audit
               (id, ts, action, target_slug, source_session_id, cycle_no, reason, details)
               VALUES ('audit-1', '2026-01-01', 'legacy', 'legacy-target', 's1', 3, 'keep', '{}')"""
        )
        conn.commit()
        conn.close()

        session_db.DB_PATH = db_path
        try:
            upgraded = session_db.connect()
            row = upgraded.execute("SELECT * FROM memory_audit WHERE id = 'audit-1'").fetchone()
            columns = {item["name"] for item in upgraded.execute("PRAGMA table_info(memory_audit)")}
            assert {"target_id", "target_type"} <= columns, columns
            assert row["target_id"] == "legacy-target"
            assert row["target_type"] == "session_event"
            assert row["reason"] == "keep"
            upgraded.close()
        finally:
            session_db.DB_PATH = original_path


# Test: Deterministic ingestion audit trail
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_ingest_creates_record_and_audit() -> None:
    with IsolatedMemoryDb():
        engine = MemoryEngine()
        result = engine.capture_material(
            source_type="message",
            content="用户表示喜欢周末看番。",
            session_id="s1",
            turn_idx=0,
        )
        assert result.ok, f"ingest failed: {result.error}"
        assert result.record_id is not None
        assert result.audit_id is not None
        assert result.changed_rows == 1

        actions = _audit_actions()
        assert "ingest" in actions, f"audit missing: {actions}"

        record = memory_db.get_record(result.record_id)
        assert record is not None
        assert "周末看番" in record["content"]

        duplicate = engine.capture_material(
            source_type="message",
            content="用户表示喜欢周末看番。",
            session_id="s1",
            turn_idx=0,
        )
        assert duplicate.ok
        assert duplicate.record_id == result.record_id
        assert duplicate.changed_rows == 0


def test_chat_material_is_isolated_to_its_session() -> None:
    with IsolatedMemoryDb():
        engine = MemoryEngine()
        result = engine.capture_material(
            source_type="message",
            content="会话专属标记词",
            session_id="chat-a",
            turn_idx=0,
        )

        assert result.ok, result.to_dict()
        assert result.details["scope_type"] == "session", result.to_dict()

        original_embed = memory_embedding.embed_text
        memory_embedding.embed_text = lambda text: None  # type: ignore[assignment]
        try:
            same_session, _ = engine.search(
                query="会话专属标记词",
                active_scope=ActiveMemoryScope.for_session(session_id="chat-a"),
            )
            other_session, _ = engine.search(
                query="会话专属标记词",
                active_scope=ActiveMemoryScope.for_session(session_id="chat-b"),
            )
        finally:
            memory_embedding.embed_text = original_embed  # type: ignore[assignment]

        assert same_session, "own session should recall its material"
        assert other_session == [], other_session


def test_delete_requires_confirmation_and_rollback_restores_record() -> None:
    with IsolatedMemoryDb():
        engine = MemoryEngine()
        captured = engine.capture_material(content="待删除记忆")
        assert captured.record_id is not None

        rejected = engine.delete_record(
            captured.record_id,
            mutation_context=_mutation_context("memory.delete", captured.record_id, confirmed=False),
        )
        assert not rejected.ok
        assert rejected.error == "confirmation required"

        deleted = engine.delete_record(
            captured.record_id,
            mutation_context=_mutation_context("memory.delete", captured.record_id),
        )
        assert deleted.ok, deleted.to_dict()
        assert deleted.audit_id and deleted.version_id

        restored = engine.restore_record_version(
            captured.record_id,
            deleted.version_id.removeprefix("version://"),
            mutation_context=_mutation_context("memory.restore", captured.record_id),
        )
        assert restored.ok, restored.to_dict()
        record = engine.get_record(captured.record_id)
        assert record is not None
        assert record["status"] == "active", record


def test_mutation_rolls_back_when_audit_cannot_be_written() -> None:
    with IsolatedMemoryDb():
        engine = MemoryEngine()
        captured = engine.capture_material(content="必须审计的记忆")
        assert captured.record_id is not None
        memory_db.connect().execute("DROP TABLE memory_audit")

        result = engine.delete_record(
            captured.record_id,
            mutation_context=_mutation_context("memory.delete", captured.record_id),
        )

        assert not result.ok, result.to_dict()
        record = engine.get_record(captured.record_id)
        assert record is not None
        assert record["status"] == "active", record


def test_context_marks_memory_unavailable_for_corrupt_database() -> None:
    original_path = memory_db.MEMORY_DB_PATH
    memory_db.close()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sessions.db"
        db_path.write_bytes(b"not-a-sqlite-database")
        memory_db.MEMORY_DB_PATH = db_path
        try:
            context = MemoryEngine().build_context(user_message="继续聊")
        finally:
            memory_db.close()
            memory_db.MEMORY_DB_PATH = original_path

    assert context.gate_decision == "unavailable"
    assert context.trace["memory_available"] is False
    assert context.sections == []


def test_capture_reports_storage_failure() -> None:
    original_path = memory_db.MEMORY_DB_PATH
    memory_db.close()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sessions.db"
        db_path.write_bytes(b"not-a-sqlite-database")
        memory_db.MEMORY_DB_PATH = db_path
        try:
            result = MemoryEngine().capture_material(content="应进入重试队列")
        finally:
            memory_db.close()
            memory_db.MEMORY_DB_PATH = original_path

    assert not result.ok
    assert result.error


def test_session_message_retries_failed_memory_capture() -> None:
    import selfecho_session.db as session_db
    from selfecho_session.service import SessionMemoryService

    class FailingMemoryEngine:
        def capture_material(self, **kwargs) -> CapturedMemory:
            return CapturedMemory(False, error="storage unavailable")

    original_session_path = session_db.DB_PATH
    original_memory_path = memory_db.MEMORY_DB_PATH
    memory_db.close()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sessions.db"
        session_db.DB_PATH = db_path
        memory_db.MEMORY_DB_PATH = db_path
        service = SessionMemoryService(memory_engine=FailingMemoryEngine())
        try:
            session = service.create_session(scope="chat")
            message = service.append_message(session["id"], "assistant", "稍后补写的完整消息")
            assert message["memory_capture"]["ok"] is False
            assert service.pending_memory_ingest_count() == 1

            service.memory_engine = MemoryEngine()
            retried = service.retry_pending_memory_ingest()

            assert retried["captured"] == 1, retried
            assert service.pending_memory_ingest_count() == 0
        finally:
            service.conn.close()
            memory_db.close()
            session_db.DB_PATH = original_session_path
            memory_db.MEMORY_DB_PATH = original_memory_path


def test_concurrent_session_messages_keep_unique_turns() -> None:
    import selfecho_session.db as session_db
    from selfecho_session.service import SessionMemoryService

    class SuccessfulMemoryEngine:
        def capture_material(self, **kwargs) -> CapturedMemory:
            return CapturedMemory(True, record_id=str(kwargs.get("turn_idx")), changed_rows=1)

    original_session_path = session_db.DB_PATH
    with tempfile.TemporaryDirectory() as tmpdir:
        session_db.DB_PATH = Path(tmpdir) / "sessions.db"
        service = SessionMemoryService(memory_engine=SuccessfulMemoryEngine())
        errors: list[str] = []
        try:
            session = service.create_session(scope="chat")

            def append(content: str) -> None:
                try:
                    service.append_message(session["id"], "assistant", content)
                except Exception as exc:
                    errors.append(str(exc))

            threads = [threading.Thread(target=append, args=(f"message-{idx}",)) for idx in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            messages = service.get_messages(session["id"])
            assert not errors, errors
            assert [item["turn_idx"] for item in messages] == [0, 1], messages
            assert service.pending_memory_ingest_count() == 0
        finally:
            service.conn.close()
            session_db.DB_PATH = original_session_path


def test_session_consolidation_triggers_on_token_budget_and_force_compile() -> None:
    import selfecho_session.db as session_db
    from selfecho_session.service import SessionMemoryService

    class CompileMemoryEngine:
        def __init__(self) -> None:
            self.compiled = False

        def capture_material(self, **kwargs) -> CapturedMemory:
            return CapturedMemory(True, record_id=str(kwargs.get("turn_idx")), changed_rows=1)

        async def compile_tendency(self, **kwargs) -> dict:
            self.compiled = True
            return {"ok": True, "action": "compile", "changed_rows": 1, "profile_id": "profile-1"}

    original_session_path = session_db.DB_PATH
    with tempfile.TemporaryDirectory() as tmpdir:
        session_db.DB_PATH = Path(tmpdir) / "sessions.db"
        engine = CompileMemoryEngine()
        service = SessionMemoryService(memory_engine=engine)  # type: ignore[arg-type]
        try:
            session = service.create_session(scope="chat")
            service.append_message(session["id"], "assistant", "x" * 40)
            assert service.should_consolidate(session["id"], turn_limit=12, token_limit=5)

            empty_session = service.create_session(scope="chat")
            service.consolidate(empty_session["id"], force=True)
            assert engine.compiled
        finally:
            service.conn.close()
            session_db.DB_PATH = original_session_path


def test_session_delete_versions_messages_and_linked_memory() -> None:
    import selfecho_session.db as session_db
    from selfecho_session.service import SessionMemoryService

    original_session_path = session_db.DB_PATH
    original_memory_path = memory_db.MEMORY_DB_PATH
    memory_db.close()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sessions.db"
        session_db.DB_PATH = db_path
        memory_db.MEMORY_DB_PATH = db_path
        engine = MemoryEngine()
        service = SessionMemoryService(memory_engine=engine)
        try:
            session = service.create_session(scope="chat")
            service.append_message(session["id"], "assistant", "delete-session-marker")
            records = engine.list_records(limit=10, include_all=True)
            record = next(item for item in records if item.get("session_id") == session["id"])
            edited = engine.update_record(
                record["id"],
                content="delete-session-marker-edited",
                mutation_context=_mutation_context("memory.update", record["id"]),
            )
            assert edited.ok and edited.version_id, edited.to_dict()

            rejected = service.delete_session(session["id"])
            assert not rejected.ok and rejected.error == "confirmation required"
            deleted = service.delete_session(
                session["id"],
                mutation_context=_mutation_context("session.delete", session["id"]),
            )
            assert deleted.ok and deleted.version_id and deleted.audit_id and deleted.changed_rows == 1
            assert service.get_session(session["id"]) is None
            assert service.list_sessions(include_archived=True) == []
            assert engine.list_records(limit=10, include_all=True) == []
            assert engine.search_records("delete-session-marker") == []
            assert engine.get_record(record["id"], include_all=True) is None
            assert engine.get_record(
                record["id"],
                active_scope=ActiveMemoryScope.for_session(session_id=session["id"]),
            ) is None
            assert engine.get_record_version(
                record["id"],
                edited.version_id.removeprefix("version://"),
            ) is None
            assert memory_db.get_record(record["id"])["status"] == "active"

            restored = service.restore_session_version(
                session["id"],
                deleted.version_id,
                mutation_context=_mutation_context("session.restore", session["id"]),
            )
            assert restored.ok and restored.audit_id and restored.changed_rows == 1, restored.to_dict()
            assert restored.version_id != deleted.version_id
            assert service.get_messages(session["id"])[0]["content"] == "delete-session-marker"
            assert engine.get_record(record["id"], include_all=True)["status"] == "active"
            assert engine.get_record_version(
                record["id"],
                edited.version_id.removeprefix("version://"),
            ) is not None
            assert engine.list_records(limit=10, include_all=True)[0]["id"] == record["id"]
            assert engine.search_records("delete-session-marker")[0]["id"] == record["id"]
        finally:
            service.conn.close()
            memory_db.close()
            session_db.DB_PATH = original_session_path
            memory_db.MEMORY_DB_PATH = original_memory_path


def test_tombstoned_session_records_reject_direct_mutation() -> None:
    import selfecho_session.db as session_db
    from selfecho_session.service import SessionMemoryService

    original_session_path = session_db.DB_PATH
    original_memory_path = memory_db.MEMORY_DB_PATH
    memory_db.close()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sessions.db"
        session_db.DB_PATH = db_path
        memory_db.MEMORY_DB_PATH = db_path
        engine = MemoryEngine()
        service = SessionMemoryService(memory_engine=engine)
        try:
            session = service.create_session(scope="chat")
            service.append_message(session["id"], "assistant", "tombstone-mutation-marker")
            record = engine.list_records(limit=10, include_all=True)[0]
            deleted = service.delete_session(
                session["id"],
                mutation_context=_mutation_context("session.delete", session["id"]),
            )
            assert deleted.ok, deleted.to_dict()

            result = engine.update_record(
                record["id"],
                content="must-not-change",
                mutation_context=_mutation_context("memory.update", record["id"]),
            )

            assert not result.ok, result.to_dict()
            assert result.error == "record belongs to deleted session", result.to_dict()
            assert memory_db.get_record(record["id"])["content"] == "tombstone-mutation-marker"
        finally:
            service.conn.close()
            memory_db.close()
            session_db.DB_PATH = original_session_path
            memory_db.MEMORY_DB_PATH = original_memory_path


def test_tombstoned_session_observations_are_hidden_until_restore() -> None:
    import selfecho_session.db as session_db
    from selfecho_session.service import SessionMemoryService

    original_session_path = session_db.DB_PATH
    original_memory_path = memory_db.MEMORY_DB_PATH
    memory_db.close()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sessions.db"
        session_db.DB_PATH = db_path
        memory_db.MEMORY_DB_PATH = db_path
        engine = MemoryEngine()
        service = SessionMemoryService(memory_engine=engine)
        try:
            session = service.create_session(scope="chat")
            observation_id = memory_db.insert_observation(
                content="deleted-session-tendency",
                scope_kind="session",
                scope_key=session["id"],
                source_session_id=session["id"],
            )
            assert observation_id
            profile_result = memory_db.upsert_profile(
                scope_kind="session",
                scope_key=session["id"],
                content="deleted-session-profile",
            )
            assert profile_result.ok, profile_result.to_dict()
            deleted = service.delete_session(
                session["id"],
                mutation_context=_mutation_context("session.delete", session["id"]),
            )
            assert deleted.ok and deleted.version_id, deleted.to_dict()

            hidden = memory_db.list_observations(
                scope_kind="session",
                scope_key=session["id"],
                status=None,
            )
            assert hidden == [], hidden
            assert memory_db.get_stats()["active_observations"] == 0
            assert memory_db.get_profile_by_scope("session", session["id"]) is None
            assert all(item["id"] != profile_result.target_id for item in memory_db.list_profiles())
            assert memory_db.get_stats()["tendency_profiles"] == 0
            tendency_context, _, _ = engine._load_tendency(
                ActiveMemoryScope.for_session(session_id=session["id"]),
                1400,
            )
            assert "deleted-session-profile" not in tendency_context

            restored = service.restore_session_version(
                session["id"],
                deleted.version_id,
                mutation_context=_mutation_context("session.restore", session["id"]),
            )
            assert restored.ok, restored.to_dict()
            visible = memory_db.list_observations(
                scope_kind="session",
                scope_key=session["id"],
                status=None,
            )
            assert [item["id"] for item in visible] == [observation_id]
            assert memory_db.get_profile_by_scope("session", session["id"])["id"] == profile_result.target_id
            assert memory_db.get_stats()["tendency_profiles"] == 1
        finally:
            service.conn.close()
            memory_db.close()
            session_db.DB_PATH = original_session_path
            memory_db.MEMORY_DB_PATH = original_memory_path


def test_session_delete_rolls_back_when_audit_write_fails() -> None:
    import selfecho_session.db as session_db
    from selfecho_session.service import SessionMemoryService

    original_session_path = session_db.DB_PATH
    original_memory_path = memory_db.MEMORY_DB_PATH
    memory_db.close()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sessions.db"
        session_db.DB_PATH = db_path
        memory_db.MEMORY_DB_PATH = db_path
        engine = MemoryEngine()
        service = SessionMemoryService(memory_engine=engine)
        try:
            session = service.create_session(scope="chat")
            service.append_message(session["id"], "assistant", "atomic-delete-marker")
            service._record_audit = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("audit failed"))

            result = service.delete_session(
                session["id"],
                mutation_context=_mutation_context("session.delete", session["id"]),
            )

            assert not result.ok and result.error == "audit failed", result.to_dict()
            assert service.get_session(session["id"]) is not None
            assert engine.search_records("atomic-delete-marker")
        finally:
            service.conn.close()
            memory_db.close()
            session_db.DB_PATH = original_session_path
            memory_db.MEMORY_DB_PATH = original_memory_path


def test_session_restore_rolls_back_when_audit_write_fails() -> None:
    import selfecho_session.db as session_db
    from selfecho_session.service import SessionMemoryService

    original_session_path = session_db.DB_PATH
    original_memory_path = memory_db.MEMORY_DB_PATH
    memory_db.close()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sessions.db"
        session_db.DB_PATH = db_path
        memory_db.MEMORY_DB_PATH = db_path
        engine = MemoryEngine()
        service = SessionMemoryService(memory_engine=engine)
        try:
            session = service.create_session(scope="chat")
            service.append_message(session["id"], "assistant", "atomic-restore-marker")
            deleted = service.delete_session(
                session["id"],
                mutation_context=_mutation_context("session.delete", session["id"]),
            )
            assert deleted.ok and deleted.version_id, deleted.to_dict()
            service._record_audit = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("audit failed"))

            result = service.restore_session_version(
                session["id"],
                deleted.version_id,
                mutation_context=_mutation_context("session.restore", session["id"]),
            )

            assert not result.ok and result.error == "audit failed", result.to_dict()
            assert service.get_session(session["id"]) is None
            assert engine.search_records("atomic-restore-marker") == []
        finally:
            service.conn.close()
            memory_db.close()
            session_db.DB_PATH = original_session_path
            memory_db.MEMORY_DB_PATH = original_memory_path


def test_session_delete_audit_uses_public_rollback_route() -> None:
    import selfecho_api.server as server
    import selfecho_session.db as session_db
    from selfecho_session.service import SessionMemoryService
    from starlette.requests import Request

    original_session_path = session_db.DB_PATH
    original_memory_path = memory_db.MEMORY_DB_PATH
    original_engine = server.default_memory_engine
    original_service = server.session_service
    memory_db.close()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sessions.db"
        session_db.DB_PATH = db_path
        memory_db.MEMORY_DB_PATH = db_path
        engine = MemoryEngine()
        service = SessionMemoryService(memory_engine=engine)
        server.default_memory_engine = engine
        server.session_service = service
        try:
            session = service.create_session(scope="chat")
            deleted = service.delete_session(
                session["id"],
                mutation_context=_mutation_context("session.delete", session["id"]),
            )
            assert deleted.ok and deleted.audit_id, deleted.to_dict()
            request = Request({
                "type": "http",
                "method": "POST",
                "path": f"/api/memory-audit/{deleted.audit_id}/rollback",
                "headers": [
                    (b"origin", b"http://127.0.0.1:8765"),
                    (b"x-iwiw-permission-profile", b"full_access"),
                ],
            })

            response = server.rollback_audit(request, deleted.audit_id, confirmed=True)

            assert response["ok"], response
            assert response["session"]["id"] == session["id"], response
        finally:
            server.default_memory_engine = original_engine
            server.session_service = original_service
            service.conn.close()
            memory_db.close()
            session_db.DB_PATH = original_session_path
            memory_db.MEMORY_DB_PATH = original_memory_path


def test_tendency_compile_without_changes_is_not_success() -> None:
    with IsolatedMemoryDb():
        result = asyncio.run(TendencyCompiler(FakeModelGateway([])).compile())

    assert not result.ok, result.to_dict()
    assert result.changed_rows == 0
    assert result.error == "no observations"


def test_tendency_compile_empty_model_result_is_not_success() -> None:
    for response in ("", json.dumps({"tendencies": []}, ensure_ascii=False)):
        with IsolatedMemoryDb():
            observation_id = memory_db.insert_observation(
                content="保持简洁",
                scope_kind="agent_global",
            )
            assert observation_id

            result = asyncio.run(TendencyCompiler(FakeModelGateway([response])).compile())

            assert not result.ok, result.to_dict()
            assert result.changed_rows == 0
            assert memory_db.get_profile_by_scope("agent_global") is None


def test_cancelled_tendency_compile_does_not_leak_lock() -> None:
    engine = MemoryEngine(model_gateway=FakeModelGateway([]))

    async def cancel_while_waiting() -> bool:
        engine._tendency_compile_lock.acquire()
        task = asyncio.create_task(engine.compile_tendency(scope_kind="session", scope_key="s1"))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        engine._tendency_compile_lock.release()
        await asyncio.sleep(0.05)
        acquired = engine._tendency_compile_lock.acquire(blocking=False)
        if acquired:
            engine._tendency_compile_lock.release()
        return acquired

    assert asyncio.run(cancel_while_waiting()), "cancelled waiter acquired the lock without releasing it"


def test_engine_manages_confirmed_mutations_and_version_history() -> None:
    with IsolatedMemoryDb():
        engine = MemoryEngine()
        captured = engine.capture_material(content="原始内容")
        assert captured.record_id is not None

        rejected = engine.update_record(
            captured.record_id,
            content="修改内容",
            mutation_context=_mutation_context("memory.update", captured.record_id, confirmed=False),
        )
        assert not rejected.ok
        assert rejected.error == "confirmation required"

        denied = engine.update_record(
            captured.record_id,
            content="修改内容",
            mutation_context=_mutation_context(
                "memory.update",
                captured.record_id,
                permission_profile="guided",
            ),
        )
        assert not denied.ok
        assert denied.error == "full_access permission required"

        updated = engine.update_record(
            captured.record_id,
            content="修改内容",
            mutation_context=_mutation_context("memory.update", captured.record_id),
        )
        assert updated.ok and updated.version_id
        version = updated.version_id.removeprefix("version://")
        history = engine.get_record_version(captured.record_id, version)
        assert history is not None
        assert history["content"] == "原始内容"

        no_change = engine.update_record(
            captured.record_id,
            content="修改内容",
            mutation_context=_mutation_context("memory.update", captured.record_id),
        )
        assert not no_change.ok
        assert no_change.changed_rows == 0

        deleted = engine.delete_record(
            captured.record_id,
            mutation_context=_mutation_context("memory.delete", captured.record_id),
        )
        assert deleted.ok and deleted.audit_id
        duplicate_delete = engine.delete_record(
            captured.record_id,
            mutation_context=_mutation_context("memory.delete", captured.record_id),
        )
        assert not duplicate_delete.ok and duplicate_delete.changed_rows == 0
        rollback_rejected = engine.rollback_audit(
            deleted.audit_id,
            mutation_context=_mutation_context("memory.rollback", deleted.audit_id, confirmed=False),
        )
        assert not rollback_rejected.ok
        rolled_back = engine.rollback_audit(
            deleted.audit_id,
            mutation_context=_mutation_context("memory.rollback", deleted.audit_id),
        )
        assert rolled_back.ok, rolled_back.to_dict()
        assert engine.get_record(captured.record_id)["status"] == "active"


def test_workspace_file_mutation_requires_authority_and_is_recoverable() -> None:
    from selfecho_agent.file_mutations import FileMutationService

    with IsolatedMemoryDb(), tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "notes.md"
        target.write_text("before", encoding="utf-8")
        service = FileMutationService()

        denied = service.write_file(
            target,
            "after",
            mutation_context=_mutation_context(
                "workspace.file.write",
                str(target),
                permission_profile="guided",
            ),
        )
        assert not denied.ok and denied.error == "full_access permission required", denied.to_dict()
        assert target.read_text(encoding="utf-8") == "before"

        changed = service.write_file(
            target,
            "after",
            mutation_context=_mutation_context("workspace.file.write", str(target)),
        )
        assert changed.ok and changed.changed_rows == 1, changed.to_dict()
        assert changed.version_id and changed.audit_id, changed.to_dict()
        assert target.read_text(encoding="utf-8") == "after"

        restored = service.rollback_audit(
            changed.audit_id,
            mutation_context=_mutation_context("memory.rollback", changed.audit_id),
        )
        assert restored.ok and restored.version_id and restored.audit_id, restored.to_dict()
        assert target.read_text(encoding="utf-8") == "before"
        duplicate_restore = service.rollback_audit(
            changed.audit_id,
            mutation_context=_mutation_context("memory.rollback", changed.audit_id),
        )
        assert not duplicate_restore.ok and duplicate_restore.changed_rows == 0

        non_utf8_target = Path(tmpdir) / "legacy.md"
        non_utf8_target.write_bytes(b"\xffbefore")
        non_utf8_change = service.write_file(
            non_utf8_target,
            "valid utf-8",
            mutation_context=_mutation_context("workspace.file.write", str(non_utf8_target)),
        )
        assert non_utf8_change.ok and non_utf8_change.audit_id, non_utf8_change.to_dict()
        non_utf8_restore = service.rollback_audit(
            non_utf8_change.audit_id,
            mutation_context=_mutation_context("memory.rollback", non_utf8_change.audit_id),
        )
        assert non_utf8_restore.ok, non_utf8_restore.to_dict()
        assert non_utf8_target.read_bytes() == b"\xffbefore"

        directory = Path(tmpdir) / "project"
        create_denied = service.create_directory(
            directory,
            mutation_context=_mutation_context(
                "filesystem.directory.create",
                str(directory),
                permission_profile="guided",
            ),
        )
        assert not create_denied.ok and not directory.exists(), create_denied.to_dict()
        created = service.create_directory(
            directory,
            mutation_context=_mutation_context("filesystem.directory.create", str(directory)),
        )
        assert created.ok and created.version_id and created.audit_id, created.to_dict()
        create_rollback = service.rollback_audit(
            created.audit_id,
            mutation_context=_mutation_context("memory.rollback", created.audit_id),
        )
        assert create_rollback.ok and not directory.exists(), create_rollback.to_dict()

        directory.mkdir()
        renamed_path = Path(tmpdir) / "renamed"
        renamed = service.rename_directory(
            directory,
            renamed_path,
            mutation_context=_mutation_context(
                "filesystem.directory.rename",
                f"{directory} -> {renamed_path}",
            ),
        )
        assert renamed.ok and renamed.version_id and renamed.audit_id, renamed.to_dict()
        rename_rollback = service.rollback_audit(
            renamed.audit_id,
            mutation_context=_mutation_context("memory.rollback", renamed.audit_id),
        )
        assert rename_rollback.ok and directory.exists() and not renamed_path.exists(), rename_rollback.to_dict()

        deleted = service.delete_empty_directory(
            directory,
            mutation_context=_mutation_context("filesystem.directory.delete", str(directory)),
        )
        assert deleted.ok and deleted.version_id and deleted.audit_id, deleted.to_dict()
        delete_rollback = service.rollback_audit(
            deleted.audit_id,
            mutation_context=_mutation_context("memory.rollback", deleted.audit_id),
        )
        assert delete_rollback.ok and directory.exists(), delete_rollback.to_dict()
        actions = _audit_actions()
        for action in (
            "filesystem.directory.create",
            "filesystem.directory.rename",
            "filesystem.directory.delete",
        ):
            assert action in actions, actions


def test_workspace_file_mutations_are_serialized_and_complete_audit() -> None:
    from selfecho_agent.file_mutations import FileMutationService

    with IsolatedMemoryDb(), tempfile.TemporaryDirectory() as tmpdir:
        service = FileMutationService()
        observed_target = Path(tmpdir) / "observed.txt"
        observed_target.write_text("before", encoding="utf-8")
        observed_statuses: list[str] = []
        original_atomic_write = service._atomic_write

        def observe_pending(target: Path, content: bytes) -> None:
            row = memory_db.connect().execute(
                "SELECT status FROM memory_audit ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            observed_statuses.append(row["status"] if row else "missing")
            original_atomic_write(target, content)

        service._atomic_write = observe_pending  # type: ignore[method-assign]
        try:
            observed = service.write_file(
                observed_target,
                "after",
                mutation_context=_mutation_context("workspace.file.write", str(observed_target)),
            )
        finally:
            service._atomic_write = original_atomic_write  # type: ignore[method-assign]
        assert observed.ok and observed.audit_id, observed.to_dict()
        assert observed_statuses == ["pending"], observed_statuses
        assert memory_db.get_audit_event(observed.audit_id)["status"] == "completed"

        target = Path(tmpdir) / "concurrent.txt"
        target.write_bytes(b"before")
        barrier = threading.Barrier(2)
        results: list[object] = []

        def write(content: str) -> None:
            barrier.wait()
            results.append(service.write_file(
                target,
                content,
                mutation_context=_mutation_context("workspace.file.write", str(target)),
            ))

        threads = [threading.Thread(target=write, args=(content,)) for content in ("first", "second")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(results) == 2 and all(result.ok for result in results), [result.to_dict() for result in results]
        rows = memory_db.connect().execute(
            "SELECT content FROM workspace_file_versions WHERE target_path = ? ORDER BY id",
            (str(target),),
        ).fetchall()
        states = {bytes(row["content"]) for row in rows}
        states.add(target.read_bytes())
        assert states == {b"before", b"first", b"second"}, states
        audit_statuses = memory_db.connect().execute(
            "SELECT status FROM memory_audit WHERE target_id = ?",
            (str(target),),
        ).fetchall()
        assert len(audit_statuses) == 2 and all(row["status"] == "completed" for row in audit_statuses)


def test_concurrent_mutations_have_isolated_transactions() -> None:
    with IsolatedMemoryDb():
        engine = MemoryEngine()
        bad = engine.capture_material(content="bad-before")
        good = engine.capture_material(content="good-before")
        assert bad.record_id and good.record_id

        original_audit = memory_db._record_audit
        bad_entered_audit = threading.Event()
        results: dict[str, object] = {}

        def controlled_audit(**kwargs):
            if kwargs.get("reason") == "bad mutation":
                bad_entered_audit.set()
                time.sleep(0.2)
                raise RuntimeError("forced audit failure")
            return original_audit(**kwargs)

        memory_db._record_audit = controlled_audit  # type: ignore[assignment]
        try:
            bad_thread = threading.Thread(target=lambda: results.setdefault(
                "bad",
                engine.update_record(
                    bad.record_id or "",
                    content="bad-after",
                    reason="bad mutation",
                    mutation_context=_mutation_context("memory.update", bad.record_id or ""),
                ),
            ))
            bad_thread.start()
            assert bad_entered_audit.wait(timeout=2)
            good_thread = threading.Thread(target=lambda: results.setdefault(
                "good",
                engine.update_record(
                    good.record_id or "",
                    content="good-after",
                    reason="good mutation",
                    mutation_context=_mutation_context("memory.update", good.record_id or ""),
                ),
            ))
            good_thread.start()
            bad_thread.join(timeout=3)
            good_thread.join(timeout=3)
        finally:
            memory_db._record_audit = original_audit  # type: ignore[assignment]

        assert not results["bad"].ok  # type: ignore[union-attr]
        assert results["good"].ok  # type: ignore[union-attr]
        assert engine.get_record(bad.record_id, include_all=True)["content"] == "bad-before"
        assert engine.get_record(good.record_id, include_all=True)["content"] == "good-after"


def test_concurrent_first_connections_initialize_once() -> None:
    with IsolatedMemoryDb():
        errors: list[str] = []

        def read_stats() -> None:
            try:
                MemoryEngine().stats()
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=read_stats) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert not errors, errors


def test_only_deep_search_refreshes_dirty_vectors_before_recall() -> None:
    with IsolatedMemoryDb():
        engine = MemoryEngine()
        captured = engine.capture_material(content="semantic-only-marker")
        assert captured.record_id

        original_refresh = memory_db.refresh_record_vectors
        refreshed: list[str] = []

        def fake_refresh(record_id: str, model_version: str = "test") -> dict:
            refreshed.append(record_id)
            memory_db.store_record_vectors(record_id, ["semantic-only-marker"], [[1.0, 0.0]])
            return {"ok": True, "chunks": 1}

        original_embed = memory_embedding.embed_text
        memory_db.refresh_record_vectors = fake_refresh  # type: ignore[assignment]
        memory_embedding.embed_text = lambda text: [1.0, 0.0]  # type: ignore[assignment]
        try:
            light_results, light_trace = engine.search(query="unrelated query text")
            assert refreshed == [], light_trace
            results, trace = engine.search(query="你记得 unrelated query text 吗")
        finally:
            memory_db.refresh_record_vectors = original_refresh  # type: ignore[assignment]
            memory_embedding.embed_text = original_embed  # type: ignore[assignment]

        assert captured.record_id in refreshed
        assert results and results[0]["record_id"] == captured.record_id, trace


def test_management_search_returns_full_library_results() -> None:
    with IsolatedMemoryDb():
        engine = MemoryEngine()
        for idx in range(8):
            result = engine.capture_material(
                content=f"full-library-marker {idx}",
                session_id=f"session-{idx}",
                turn_idx=idx,
            )
            assert result.ok

        results = engine.search_records("full-library-marker", limit=8)
        assert len(results) == 8, results
        assert all(item["scope_type"] == "session" for item in results)


def test_scoped_list_filters_before_limit() -> None:
    with IsolatedMemoryDb():
        engine = MemoryEngine()
        target = engine.capture_material(content="target-session", session_id="target", turn_idx=0)
        assert target.ok
        for idx in range(10):
            engine.capture_material(content=f"other {idx}", session_id="other", turn_idx=idx)

        rows = engine.list_records(
            limit=1,
            active_scope=ActiveMemoryScope.for_session(session_id="target"),
        )
        assert len(rows) == 1 and rows[0]["content"] == "target-session", rows


def test_scoped_search_filters_source_type() -> None:
    with IsolatedMemoryDb():
        engine = MemoryEngine()
        engine.capture_material(content="source-filter-marker manual", source_type="manual")
        engine.capture_material(content="source-filter-marker tool", source_type="tool")
        original_embed = memory_embedding.embed_text
        memory_embedding.embed_text = lambda text: None  # type: ignore[assignment]
        try:
            results, _trace = engine.search(query="source-filter-marker", source_type="tool")
        finally:
            memory_embedding.embed_text = original_embed  # type: ignore[assignment]
        assert len(results) == 1 and results[0]["source_type"] == "tool", results


def test_mcp_exposes_only_read_only_memory_tools() -> None:
    from memory_agent.mcp_server import call_tool, list_tools

    with IsolatedMemoryDb():
        tools = asyncio.run(list_tools())
        tool_names = {tool.name for tool in tools}
        assert tool_names == {"search_memories", "list_memories", "read_memory", "get_index_stats"}
        for tool in tools:
            properties = tool.inputSchema.get("properties", {})
            assert not {"session_id", "project_id", "workspace_root"} & properties.keys(), tool
        response = asyncio.run(call_tool("ingest_memory", {"content": "不得绕过审批的写入"}))
        assert response[0].text == "Unknown tool: ingest_memory"
        assert MemoryEngine().list_records(include_all=True) == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_mcp_stats_are_scoped_to_host_context() -> None:
    from memory_agent import mcp_server

    with IsolatedMemoryDb():
        for content, scope_type, project_id, session_id in (
            ("global", "global", None, None),
            ("project-1", "project", "p1", None),
            ("project-2", "project", "p2", None),
            ("session-1", "session", None, "s1"),
            ("session-2", "session", None, "s2"),
        ):
            inserted = memory_db.insert_record(
                source_type="message",
                content=content,
                scope_type=scope_type,
                project_id=project_id,
                session_id=session_id,
            )
            assert inserted.ok, inserted.to_dict()
        memory_db.insert_observation(content="global-observation", scope_kind="agent_global")
        memory_db.insert_observation(
            content="session-1-observation",
            scope_kind="session",
            scope_key="s1",
            source_session_id="s1",
        )
        memory_db.insert_observation(
            content="session-2-observation",
            scope_kind="session",
            scope_key="s2",
            source_session_id="s2",
        )
        memory_db.upsert_profile(scope_kind="agent_global", content="global-profile")
        memory_db.upsert_profile(scope_kind="session", scope_key="s1", content="session-1-profile")
        memory_db.upsert_profile(scope_kind="session", scope_key="s2", content="session-2-profile")
        names = ("IWIW_MCP_SESSION_ID", "IWIW_MCP_PROJECT_ID", "IWIW_MCP_WORKSPACE_ROOT")
        previous = {name: os.environ.get(name) for name in names}
        try:
            os.environ["IWIW_MCP_SESSION_ID"] = "s1"
            os.environ["IWIW_MCP_PROJECT_ID"] = "p1"
            os.environ.pop("IWIW_MCP_WORKSPACE_ROOT", None)
            response = asyncio.run(mcp_server.call_tool("get_index_stats", {}))
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        stats = json.loads(response[0].text)
        assert stats["total_records"] == 3, stats
        assert stats["active_observations"] == 2, stats
        assert stats["tendency_profiles"] == 2, stats


# Test: Record CRUD lifecycle
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_record_crud_lifecycle() -> None:
    with IsolatedMemoryDb():
        # --- Insert ---
        result = memory_db.insert_record(
            source_type="message",
            content="用户询问记忆系统设计。",
            scope_type="project",
            project_id="test-project",
        )
        assert result.ok
        record_id = result.target_id
        assert record_id is not None

        rec = memory_db.get_record(record_id)
        assert rec is not None
        assert rec["content"] == "用户询问记忆系统设计。"

        # --- List ---
        records = memory_db.list_records(scope_type="project", limit=10)
        assert len(records) == 1

        # --- Edit ---
        edit = memory_db.update_record(
            record_id,
            content="用户详细询问了记忆系统架构设计。",
            description="updated",
            reason="test edit",
        )
        assert edit.ok
        assert edit.changed_rows == 1
        assert edit.audit_id is not None
        assert edit.version_id is not None

        updated = memory_db.get_record(record_id)
        assert "详细询问" in updated["content"]

        # --- Versions ---
        assert _version_count() >= 1

        # --- Soft delete ---
        delete = memory_db.update_record_status(record_id, "deleted", reason="cleanup")
        assert delete.ok
        assert memory_db.count_records("deleted") == 1

        # --- Restore ---
        versions = memory_db.list_record_versions(record_id)
        assert len(versions) >= 1
        restore = memory_db.restore_record_version(record_id, str(versions[0]["version"]))
        assert restore.ok

        # --- Audit trail ---
        actions = _audit_actions()
        for a in ("ingest", "update", "set_deleted", "restore"):
            assert a in actions, f"'{a}' missing from {actions}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Tendency profile creation + versioning
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_tendency_profile_versioning() -> None:
    with IsolatedMemoryDb():
        observation_1 = memory_db.insert_observation(content="先讲边界", scope_kind="agent_global")
        assert observation_1
        r1 = memory_db.upsert_profile(
            scope_kind="agent_global",
            content="用户偏好先讲边界再给建议。",
            source_observation_ids=[observation_1],
            merge_observation_ids=[observation_1],
        )
        assert r1.ok, f"first upsert failed: {r1.error}"
        assert r1.details["version"] == 1 and r1.version_id and r1.changed_rows == 2

        observation_2 = memory_db.insert_observation(content="结论先行", scope_kind="agent_global")
        assert observation_2
        r2 = memory_db.upsert_profile(
            scope_kind="agent_global",
            content="用户偏好结论先行。",
            source_observation_ids=[observation_2],
            merge_observation_ids=[observation_2],
        )
        assert r2.ok
        assert r2.details["version"] == 2 and r2.version_id and r2.audit_id

        profile = memory_db.get_profile_by_scope("agent_global")
        assert profile is not None
        assert json.loads(profile["source_observation_ids"]) == [observation_1, observation_2]
        versions = memory_db.list_profile_versions(profile["id"])
        assert len(versions) == 1
        assert "结论先行" in profile["content"]

        rolled_back = MemoryEngine().rollback_audit(
            r2.audit_id,
            mutation_context=_mutation_context("memory.rollback", r2.audit_id),
        )
        assert rolled_back.ok and rolled_back.version_id and rolled_back.audit_id, rolled_back.to_dict()
        restored_profile = memory_db.get_profile_by_scope("agent_global")
        assert restored_profile is not None and "先讲边界" in restored_profile["content"]
        restored_observation = memory_db.list_observations(status="active", limit=10)
        assert any(item["id"] == observation_2 for item in restored_observation)


def test_tendency_rebuild_replaces_profile_and_provenance() -> None:
    class RecordingGateway(FakeModelGateway):
        def __init__(self) -> None:
            super().__init__([json.dumps({"tendencies": ["current-profile-marker"]})])
            self.prompts: list[str] = []

        async def complete(self, **kwargs) -> str:
            self.prompts.append(str(kwargs.get("user_prompt") or ""))
            return await super().complete(**kwargs)

    with IsolatedMemoryDb():
        old_observation = memory_db.insert_observation(
            content="obsolete-observation-marker",
            scope_kind="agent_global",
        )
        assert old_observation
        initial = memory_db.upsert_profile(
            scope_kind="agent_global",
            content="obsolete-profile-marker",
            source_observation_ids=[old_observation],
            merge_observation_ids=[old_observation],
        )
        assert initial.ok
        assert memory_db.update_observation_status(old_observation, "deleted")
        current_observation = memory_db.insert_observation(
            content="current-observation-marker",
            scope_kind="agent_global",
        )
        assert current_observation
        gateway = RecordingGateway()

        rebuilt = asyncio.run(TendencyCompiler(gateway).rebuild())

        assert rebuilt.ok, rebuilt.to_dict()
        assert "obsolete-profile-marker" not in gateway.prompts[0]
        profile = memory_db.get_profile_by_scope("agent_global")
        assert profile and "current-profile-marker" in profile["content"]
        assert json.loads(profile["source_observation_ids"]) == [current_observation]


def test_tendency_rollback_old_version_releases_later_observations() -> None:
    with IsolatedMemoryDb():
        observation_1 = memory_db.insert_observation(content="先讲边界", scope_kind="agent_global")
        observation_2 = memory_db.insert_observation(content="结论先行", scope_kind="agent_global")
        assert observation_1 and observation_2
        first = memory_db.upsert_profile(
            scope_kind="agent_global",
            content="先讲边界。",
            source_observation_ids=[observation_1],
            merge_observation_ids=[observation_1],
        )
        second = memory_db.upsert_profile(
            scope_kind="agent_global",
            content="先讲边界并结论先行。",
            source_observation_ids=[observation_2],
            merge_observation_ids=[observation_2],
        )
        assert first.ok and first.audit_id and second.ok

        rolled_back = MemoryEngine().rollback_audit(
            first.audit_id,
            mutation_context=_mutation_context("memory.rollback", first.audit_id),
        )

        assert rolled_back.ok, rolled_back.to_dict()
        assert memory_db.get_profile_by_scope("agent_global") is None
        active_ids = {
            item["id"] for item in memory_db.list_observations(status="active", limit=10)
        }
        assert active_ids == {observation_1, observation_2}, active_ids


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Retrieval gate decision
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_tendency_maintenance_compiles_workspace_profile() -> None:
    with IsolatedMemoryDb():
        active_scope = ActiveMemoryScope.for_session(
            session_id="s1",
            project_id="p1",
            workspace_root=str(ROOT),
        )

        tendency = "用户在此项目中偏好先移除旧模块，再建立新接口。"
        engine = MemoryEngine(model_gateway=FakeModelGateway([
            json.dumps([{"scope_kind": "workspace", "content": tendency}], ensure_ascii=False),
            json.dumps({"tendencies": [tendency]}, ensure_ascii=False),
            json.dumps({"tendencies": [tendency]}, ensure_ascii=False),
        ]))
        result = asyncio.run(engine.maintain_tendencies(
            text="这个项目以后先移除旧模块，再建立新接口。",
            session_id="s1",
            active_scope=active_scope,
            turn_range="0-2",
            force_compile=True,
        ))

        assert result.ok, result.to_dict()
        assert result.observed_ids, result.to_dict()
        assert any(item.get("profile_id") for item in result.compiled), result.to_dict()

        assert memory_db.get_profile_by_scope("workspace", active_scope.workspace_id) is None
        session_profile = memory_db.get_profile_by_scope("session", "s1")
        assert session_profile is not None and "旧模块" in session_profile["content"]

        active = memory_db.list_observations(
            scope_kind="session",
            scope_key="s1",
            status="active",
        )
        merged = memory_db.list_observations(
            scope_kind="session",
            scope_key="s1",
            status="merged",
        )
        assert active == []
        assert len(merged) == 1
        assert merged[0]["suggested_scope_kind"] == "workspace"

        promoted = asyncio.run(engine.compile_tendency(
            scope_kind="workspace",
            scope_key=active_scope.workspace_id,
            workspace_id=active_scope.workspace_id,
            project_id="p1",
            session_id="s1",
            mutation_context=_mutation_context(
                "tendency.promote",
                f"workspace:{active_scope.workspace_id}",
            ),
        ))
        assert promoted["ok"], promoted
        assert promoted["changed_rows"] >= 1 and promoted["audit_id"] and promoted["version_id"], promoted
        profile = memory_db.get_profile_by_scope("workspace", active_scope.workspace_id)
        assert profile is not None and "旧模块" in profile["content"]

        actions = _audit_actions()
        assert "observe_tendency" in actions, actions
        assert "compile_profile" in actions, actions


def test_unstable_global_tendency_stays_in_session_overlay() -> None:
    with IsolatedMemoryDb():
        engine = MemoryEngine(model_gateway=FakeModelGateway([json.dumps([{
            "scope_kind": "agent_global",
            "content": "用户当前更喜欢简短回复。",
        }], ensure_ascii=False)]))
        result = asyncio.run(engine.maintain_tendencies(
            text="这次回复短一点就好。" + "请只针对当前讨论保持简洁。" * 12,
            session_id="s1",
            active_scope=ActiveMemoryScope.for_session(session_id="s1"),
        ))

        assert result.observed_ids, result.to_dict()
        session_observations = memory_db.list_observations(
            scope_kind="session",
            scope_key="s1",
            status=None,
        )
        global_observations = memory_db.list_observations(
            scope_kind="agent_global",
            status=None,
        )
        assert len(session_observations) == 1, session_observations
        assert session_observations[0]["suggested_scope_kind"] == "agent_global"
        assert global_observations == [], global_observations


def test_memory_runtime_does_not_import_legacy_llm_module() -> None:
    assert not (ROOT / "memory_agent" / "llm.py").exists()
    for path in (ROOT / "memory_agent").glob("*.py"):
        assert "memory_agent.llm" not in path.read_text(encoding="utf-8")


def test_memory_management_rejects_originless_http() -> None:
    from fastapi.testclient import TestClient
    from selfecho_api.server import app

    with TestClient(app) as client:
        response = client.delete("/api/memories/not-a-record?confirmed=true")
        session_memory = client.get("/api/session-memory/search?q=test")
    assert response.status_code == 403, response.text
    assert session_memory.status_code == 403, session_memory.text

    trusted_headers = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(app) as client:
        denied = client.delete(
            "/api/memories/not-a-record?confirmed=true",
            headers={**trusted_headers, "X-IwIw-Permission-Profile": "guided"},
        )
        allowed = client.delete(
            "/api/memories/not-a-record?confirmed=true",
            headers={**trusted_headers, "X-IwIw-Permission-Profile": "full_access"},
        )
    assert denied.status_code == 400 and "full_access permission required" in denied.text
    assert allowed.status_code == 400 and "record not found" in allowed.text


def test_provider_mutations_require_authority_and_ignore_draft_secrets() -> None:
    from fastapi.testclient import TestClient
    import selfecho_api.server as server
    import selfecho_config.model_profiles as profiles

    tested_ids: list[str] = []

    async def fake_test_provider(provider_id: str) -> dict:
        tested_ids.append(provider_id)
        return {"ok": True, "provider_id": provider_id, "latency_ms": 1}

    original_test = server.test_provider
    original_server_path = server.PROVIDERS_CONFIG_PATH
    original_local = profiles.LOCAL_CONFIG
    original_example = profiles.EXAMPLE_CONFIG
    server.test_provider = fake_test_provider  # type: ignore[assignment]
    draft = {
        "id": "saved-provider",
        "base_url": "http://169.254.169.254/latest/meta-data",
        "api_key_env": "UNRELATED_MACHINE_SECRET",
        "models": ["probe"],
    }
    trusted = {"Origin": "http://127.0.0.1:8765"}
    full_access = {**trusted, "X-IwIw-Permission-Profile": "full_access"}
    with IsolatedMemoryDb(), tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "providers.local.json"
        server.PROVIDERS_CONFIG_PATH = config_path
        profiles.LOCAL_CONFIG = config_path
        profiles.EXAMPLE_CONFIG = Path(tmpdir) / "missing.example.json"
        try:
            with TestClient(server.app) as client:
                untrusted = client.post(
                    "/api/models/providers/test",
                    json={"provider": draft, "confirmed": True},
                )
                denied = client.post(
                    "/api/models/providers/test",
                    headers={**trusted, "X-IwIw-Permission-Profile": "guided"},
                    json={"provider": draft, "confirmed": True},
                )
                unconfirmed = client.post(
                    "/api/models/providers/test",
                    headers=full_access,
                    json={"provider": draft, "confirmed": False},
                )
                allowed = client.post(
                    "/api/models/providers/test",
                    headers=full_access,
                    json={"provider": draft, "confirmed": True},
                )
                write_denied = client.put(
                    "/api/models/providers",
                    headers=full_access,
                    json={"providers": [draft], "confirmed": False},
                )
                write_allowed = client.put(
                    "/api/models/providers",
                    headers=full_access,
                    json={"providers": [draft], "confirmed": True},
                )
                audit_id = write_allowed.json()["mutation"]["audit_id"]
                rolled_back = client.post(
                    f"/api/memory-audit/{audit_id}/rollback?confirmed=true",
                    headers=full_access,
                )
        finally:
            server.test_provider = original_test  # type: ignore[assignment]
            server.PROVIDERS_CONFIG_PATH = original_server_path
            profiles.LOCAL_CONFIG = original_local
            profiles.EXAMPLE_CONFIG = original_example

        assert untrusted.status_code == 403, untrusted.text
        assert denied.status_code == 400 and "full_access permission required" in denied.text
        assert unconfirmed.status_code == 400 and "confirmation required" in unconfirmed.text
        assert allowed.status_code == 200 and tested_ids == ["saved-provider"], (allowed.text, tested_ids)
        assert write_denied.status_code == 400 and "confirmation required" in write_denied.text
        assert write_allowed.status_code == 200 and write_allowed.json()["mutation"]["version_id"]
        assert rolled_back.status_code == 200, rolled_back.text
        assert not config_path.exists()


def test_disabled_providers_are_not_selected_as_fallback() -> None:
    import selfecho_config.model_profiles as profiles

    original_local = profiles.LOCAL_CONFIG
    original_example = profiles.EXAMPLE_CONFIG
    with tempfile.TemporaryDirectory() as tmpdir:
        profiles.LOCAL_CONFIG = Path(tmpdir) / "providers.local.json"
        profiles.EXAMPLE_CONFIG = Path(tmpdir) / "missing.example.json"
        profiles.LOCAL_CONFIG.write_text(
            json.dumps({
                "role_defaults": {"chat": {"provider_id": "disabled", "model": "model-a"}},
                "providers": [{
                    "id": "disabled",
                    "enabled": False,
                    "models": ["model-a"],
                    "base_url": "https://example.invalid",
                    "api_key": "secret",
                }],
            }),
            encoding="utf-8",
        )
        try:
            assert profiles.active_provider("chat") is None
            assert profiles.list_providers()["role_defaults"]["chat"] == {"provider_id": "", "model": ""}
        finally:
            profiles.LOCAL_CONFIG = original_local
            profiles.EXAMPLE_CONFIG = original_example


def test_prompt_write_requires_authority_and_is_recoverable() -> None:
    from fastapi.testclient import TestClient
    import selfecho_api.prompt_service as prompts
    import selfecho_api.server as server

    original_dir = prompts.PROMPT_DIR
    trusted = {"Origin": "http://127.0.0.1:8765"}
    full_access = {**trusted, "X-IwIw-Permission-Profile": "full_access"}
    with IsolatedMemoryDb(), tempfile.TemporaryDirectory() as tmpdir:
        prompts.PROMPT_DIR = Path(tmpdir)
        target = prompts.PROMPT_DIR / "conversation_reply.md"
        try:
            with TestClient(server.app) as client:
                untrusted = client.put(
                    "/api/prompts/conversation_reply",
                    json={"content": "tampered", "confirmed": True},
                )
                denied = client.put(
                    "/api/prompts/conversation_reply",
                    headers={**trusted, "X-IwIw-Permission-Profile": "guided"},
                    json={"content": "tampered", "confirmed": True},
                )
                unconfirmed = client.put(
                    "/api/prompts/conversation_reply",
                    headers=full_access,
                    json={"content": "tampered", "confirmed": False},
                )
                changed = client.put(
                    "/api/prompts/conversation_reply",
                    headers=full_access,
                    json={"content": "audited prompt", "confirmed": True},
                )
                audit_id = changed.json()["mutation"]["audit_id"]
                restored = client.post(
                    f"/api/memory-audit/{audit_id}/rollback?confirmed=true",
                    headers=full_access,
                )
        finally:
            prompts.PROMPT_DIR = original_dir

        assert untrusted.status_code == 403, untrusted.text
        assert denied.status_code == 400 and "full_access permission required" in denied.text
        assert unconfirmed.status_code == 400 and "confirmation required" in unconfirmed.text
        assert changed.status_code == 200 and changed.json()["mutation"]["version_id"], changed.text
        assert restored.status_code == 200 and not target.exists(), restored.text


def test_workspace_file_http_mutation_uses_public_rollback() -> None:
    from fastapi.testclient import TestClient
    import selfecho_api.server as server

    with IsolatedMemoryDb(), tempfile.TemporaryDirectory() as tmpdir:
        original_root = server.ROOT
        server.ROOT = Path(tmpdir)
        target = server.ROOT / "notes.md"
        target.write_text("before", encoding="utf-8")
        path = "/api/workspace/file?path=notes.md"
        trusted = {"Origin": "http://127.0.0.1:8765"}
        full_access = {**trusted, "X-IwIw-Permission-Profile": "full_access"}
        try:
            with TestClient(server.app) as client:
                untrusted = client.put(path, json={"content": "after", "confirmed": True})
                denied = client.put(
                    path,
                    headers={**trusted, "X-IwIw-Permission-Profile": "guided"},
                    json={"content": "after", "confirmed": True},
                )
                unconfirmed = client.put(
                    path,
                    headers=full_access,
                    json={"content": "after", "confirmed": False},
                )
                changed = client.put(
                    path,
                    headers=full_access,
                    json={"content": "after", "confirmed": True},
                )
                audit_id = changed.json()["mutation"]["audit_id"]
                restored = client.post(
                    f"/api/memory-audit/{audit_id}/rollback?confirmed=true",
                    headers=full_access,
                )
        finally:
            server.ROOT = original_root

        assert untrusted.status_code == 403, untrusted.text
        assert denied.status_code == 400 and "full_access permission required" in denied.text
        assert unconfirmed.status_code == 400 and "confirmation required" in unconfirmed.text
        assert changed.status_code == 200 and changed.json()["mutation"]["version_id"], changed.text
        assert restored.status_code == 200, restored.text
        assert target.read_text(encoding="utf-8") == "before"


def test_retrieval_gate_decision() -> None:
    with IsolatedMemoryDb():
        engine = MemoryEngine()
        memory_db.insert_record(
            source_type="message",
            content="Python 异步编程使用 asyncio 进行协程调度。",
        )

        results, trace = engine.search(query="Python 异步编程怎么用")
        assert trace["decision"] in ("none", "light", "focused"), trace
        assert "threshold_used" in trace
        assert isinstance(results, list)


def test_strong_recall_does_not_inject_zero_match_record() -> None:
    with IsolatedMemoryDb():
        inserted = memory_db.insert_record(
            source_type="message",
            content="完全无关的早餐记录",
            scope_type="session",
            session_id="s1",
        )
        assert inserted.target_id is not None
        memory_db.store_record_vectors(inserted.target_id, ["完全无关的早餐记录"], [[0.0, 0.0]])

        original_embed = memory_embedding.embed_text
        memory_embedding.embed_text = lambda text: [0.0, 0.0]  # type: ignore[assignment]
        try:
            results, trace = MemoryEngine().search(
                query="你记得之前的量子计算计划吗",
                active_scope=ActiveMemoryScope.for_session(session_id="s1"),
            )
        finally:
            memory_embedding.embed_text = original_embed  # type: ignore[assignment]

        assert trace["strong_signal"] is True, trace
        assert results == [], results


def test_cjk_phrase_retrieval_uses_like_terms() -> None:
    with IsolatedMemoryDb():
        engine = MemoryEngine()
        memory_db.insert_record(
            source_type="file",
            content=(
                "用户反馈文档写得太学术和专业，自己基础较差，"
                "需要简洁易懂的描述。用户还要求AI在回复时适当缩减篇幅。"
            ),
        )

        original_embed = memory_embedding.embed_text
        memory_embedding.embed_text = lambda text: None  # type: ignore[assignment]
        try:
            results, trace = engine.search(query="我的偏好是简洁易懂吗")
        finally:
            memory_embedding.embed_text = original_embed  # type: ignore[assignment]

        injected_text = "\n".join(item.get("content", "") for item in results)
        assert trace["decision"] in {"light", "deep"}, trace
        assert "简洁易懂" in injected_text, {"results": results, "trace": trace}


def test_private_and_cross_project_memory_do_not_recall() -> None:
    with IsolatedMemoryDb():
        engine = MemoryEngine()
        scope_p1 = ActiveMemoryScope.for_session(session_id="s1", project_id="p1", workspace_root=str(ROOT))
        scope_p2 = ActiveMemoryScope.for_session(session_id="s2", project_id="p2", workspace_root=str(ROOT / "other"))

        private = engine.capture_material(
            source_type="message",
            content="用户的秘密验证码是 123456。",
            session_id="s1",
            project_id="p1",
            turn_idx=1,
            private=True,
        )
        assert private.ok
        engine.capture_material(
            source_type="message",
            content="p2-only-marker 项目二内部决策。",
            session_id="s2",
            project_id="p2",
            turn_idx=1,
        )
        engine.capture_material(
            source_type="message",
            content="p1-only-marker 项目一内部决策。",
            session_id="s1",
            project_id="p1",
            turn_idx=2,
        )

        original_embed = memory_embedding.embed_text
        memory_embedding.embed_text = lambda text: None  # type: ignore[assignment]
        try:
            private_results, _ = engine.search(query="秘密验证码", active_scope=scope_p1)
            p1_results, _ = engine.search(query="内部决策 marker", active_scope=scope_p1)
            p2_results, _ = engine.search(query="内部决策 marker", active_scope=scope_p2)
        finally:
            memory_embedding.embed_text = original_embed  # type: ignore[assignment]

        assert all("123456" not in item.get("content", "") for item in private_results), private_results
        assert any("p1-only-marker" in item.get("content", "") for item in p1_results), p1_results
        assert all("p2-only-marker" not in item.get("content", "") for item in p1_results), p1_results
        assert any("p2-only-marker" in item.get("content", "") for item in p2_results), p2_results


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Agent context assembly
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_agent_context_assembles_profile_and_retrieved() -> None:
    with IsolatedMemoryDb():
        workspace_id = workspace_id_from_root(str(ROOT))
        assert workspace_id is not None
        # Create a tendency profile
        memory_db.upsert_profile(
            scope_kind="agent_global",
            content="用户希望回答时先给出清晰边界。",
        )

        memory_db.upsert_profile(
            scope_kind="workspace",
            scope_key=workspace_id,
            workspace_id=workspace_id,
            project_id="p1",
            content="workspace-profile-marker",
        )

        memory_db.upsert_profile(
            scope_kind="session",
            scope_key="s1",
            workspace_id=workspace_id,
            project_id="p1",
            content="session-overlay-marker",
        )

        # Create a retrievable record
        memory_db.insert_record(
            source_type="message",
            content="用户在 2026 年 6 月有期末考试安排。",
        )

        from selfecho_agent.context import ContextBuilder

        original_embed = memory_embedding.embed_text
        memory_embedding.embed_text = lambda text: None  # type: ignore[assignment]
        try:
            bundle = ContextBuilder(
                FakeContextSessionService(scope="project", project_id="p1", project_path=str(ROOT))
            ).build(  # type: ignore[arg-type]
                session_id="s1",
                decision=IntentDecision("memory", "deliberate", "eval"),
                base_prompt="base prompt",
                user_message="考试安排是什么",
            )
        finally:
            memory_embedding.embed_text = original_embed  # type: ignore[assignment]

        assert "长期倾向" in bundle.system_prompt
        assert "workspace-profile-marker" in bundle.system_prompt
        assert "session-overlay-marker" in bundle.system_prompt
        assert bundle.metadata["workspace_id"] == workspace_id
        assert "tendency_profile_ids" in bundle.metadata, bundle.metadata
        assert "retrieved_record_ids" in bundle.metadata, bundle.metadata
        assert bundle.metadata["memory_budget"]["tendency"]["included"] == 3
        assert bundle.metadata["memory_budget"]["recalled"]["included"] >= 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Runner
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main() -> int:
    evaluator = HarnessEvaluator()
    cases: dict[str, Callable[[], None]] = {
        "migrates_head_memory_schema": test_migrates_head_memory_schema_without_losing_materials,
        "session_db_migrates_head_audit": test_session_db_migrates_head_audit_schema_in_place,
        "ingest_creates_record_and_audit": test_ingest_creates_record_and_audit,
        "chat_material_session_isolation": test_chat_material_is_isolated_to_its_session,
        "delete_confirmation_and_rollback": test_delete_requires_confirmation_and_rollback_restores_record,
        "mutation_requires_audit": test_mutation_rolls_back_when_audit_cannot_be_written,
        "corrupt_database_degrades_context": test_context_marks_memory_unavailable_for_corrupt_database,
        "capture_reports_storage_failure": test_capture_reports_storage_failure,
        "session_message_retries_memory_capture": test_session_message_retries_failed_memory_capture,
        "concurrent_session_messages": test_concurrent_session_messages_keep_unique_turns,
        "session_consolidation_triggers": test_session_consolidation_triggers_on_token_budget_and_force_compile,
        "session_delete_versions_linked_memory": test_session_delete_versions_messages_and_linked_memory,
        "tombstoned_record_mutation_rejected": test_tombstoned_session_records_reject_direct_mutation,
        "tombstoned_observations_hidden": test_tombstoned_session_observations_are_hidden_until_restore,
        "session_delete_audit_rollback": test_session_delete_rolls_back_when_audit_write_fails,
        "session_restore_audit_rollback": test_session_restore_rolls_back_when_audit_write_fails,
        "session_delete_public_audit_rollback": test_session_delete_audit_uses_public_rollback_route,
        "tendency_no_change_not_success": test_tendency_compile_without_changes_is_not_success,
        "tendency_empty_model_not_success": test_tendency_compile_empty_model_result_is_not_success,
        "cancelled_tendency_compile_lock": test_cancelled_tendency_compile_does_not_leak_lock,
        "engine_manages_confirmed_mutations": test_engine_manages_confirmed_mutations_and_version_history,
        "workspace_file_mutation_recoverable": test_workspace_file_mutation_requires_authority_and_is_recoverable,
        "workspace_file_mutation_serialized": test_workspace_file_mutations_are_serialized_and_complete_audit,
        "concurrent_mutation_transactions": test_concurrent_mutations_have_isolated_transactions,
        "concurrent_first_connection": test_concurrent_first_connections_initialize_once,
        "deep_search_refreshes_dirty_vectors": test_only_deep_search_refreshes_dirty_vectors_before_recall,
        "management_search_full_library": test_management_search_returns_full_library_results,
        "scoped_list_filters_before_limit": test_scoped_list_filters_before_limit,
        "scoped_search_source_filter": test_scoped_search_filters_source_type,
        "mcp_read_only_tools": test_mcp_exposes_only_read_only_memory_tools,
        "mcp_stats_scoped": test_mcp_stats_are_scoped_to_host_context,
        "record_crud_lifecycle": test_record_crud_lifecycle,
        "tendency_profile_versioning": test_tendency_profile_versioning,
        "tendency_rebuild_replaces_profile": test_tendency_rebuild_replaces_profile_and_provenance,
        "tendency_rollback_old_version": test_tendency_rollback_old_version_releases_later_observations,
        "tendency_maintenance_workspace_profile": test_tendency_maintenance_compiles_workspace_profile,
        "unstable_tendency_session_overlay": test_unstable_global_tendency_stays_in_session_overlay,
        "memory_runtime_model_gateway": test_memory_runtime_does_not_import_legacy_llm_module,
        "memory_http_requires_source": test_memory_management_rejects_originless_http,
        "provider_mutations_authorized": test_provider_mutations_require_authority_and_ignore_draft_secrets,
        "disabled_provider_not_fallback": test_disabled_providers_are_not_selected_as_fallback,
        "prompt_write_authorized": test_prompt_write_requires_authority_and_is_recoverable,
        "workspace_file_http_rollback": test_workspace_file_http_mutation_uses_public_rollback,
        "retrieval_gate_decision": test_retrieval_gate_decision,
        "strong_recall_rejects_zero_match": test_strong_recall_does_not_inject_zero_match_record,
        "cjk_phrase_retrieval_uses_like_terms": test_cjk_phrase_retrieval_uses_like_terms,
        "private_and_cross_project_memory_do_not_recall": test_private_and_cross_project_memory_do_not_recall,
        "agent_context_assembly": test_agent_context_assembles_profile_and_retrieved,
    }
    for name, check in cases.items():
        evaluator.add(name, check)
    results = evaluator.run()
    print(json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
