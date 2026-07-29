from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from memory_agent import db
from memory_agent.engine import MemoryMutationContext


_MUTATION_LOCK = threading.Lock()
# ponytail: one process-wide lock is enough until filesystem mutation throughput matters.


class FileMutationService:
    """Authorized, audited filesystem mutations with recoverable file writes."""

    def write_file(
        self,
        target: Path,
        content: str,
        *,
        mutation_context: MemoryMutationContext | None,
    ) -> db.MutationResult:
        action = "workspace.file.write"
        target_id = str(target)
        if error := self._authorization_error(mutation_context, action, target_id):
            return db.MutationResult(False, action, target_id=target_id, error=error)
        return self._write_file_bytes(
            target,
            content.encode("utf-8"),
            action=action,
            reason="workspace file write",
        )

    def write_config_file(
        self,
        target: Path,
        content: str,
        *,
        action: str,
        reason: str,
        mutation_context: MemoryMutationContext | None,
    ) -> db.MutationResult:
        target_id = str(target)
        if error := self._authorization_error(mutation_context, action, target_id):
            return db.MutationResult(False, action, target_id=target_id, error=error)
        return self._write_file_bytes(
            target,
            content.encode("utf-8"),
            action=action,
            reason=reason,
            allow_create=True,
        )

    def create_directory(
        self,
        target: Path,
        *,
        mutation_context: MemoryMutationContext | None,
    ) -> db.MutationResult:
        action = "filesystem.directory.create"
        target_id = str(target)
        if error := self._authorization_error(mutation_context, action, target_id):
            return db.MutationResult(False, action, target_id=target_id, error=error)
        return self._directory_mutation(
            action=action,
            target_id=target_id,
            mutate=target.mkdir,
            undo=target.rmdir,
            details={"path": target_id},
            version_action="create",
            source_path=target_id,
        )

    def rename_directory(
        self,
        target: Path,
        next_path: Path,
        *,
        mutation_context: MemoryMutationContext | None,
    ) -> db.MutationResult:
        action = "filesystem.directory.rename"
        target_id = f"{target} -> {next_path}"
        if error := self._authorization_error(mutation_context, action, target_id):
            return db.MutationResult(False, action, target_id=target_id, error=error)
        return self._directory_mutation(
            action=action,
            target_id=target_id,
            mutate=lambda: target.rename(next_path),
            undo=lambda: next_path.rename(target),
            details={"from": str(target), "to": str(next_path)},
            version_action="rename",
            source_path=str(target),
            target_path=str(next_path),
        )

    def delete_empty_directory(
        self,
        target: Path,
        *,
        mutation_context: MemoryMutationContext | None,
    ) -> db.MutationResult:
        action = "filesystem.directory.delete"
        target_id = str(target)
        if error := self._authorization_error(mutation_context, action, target_id):
            return db.MutationResult(False, action, target_id=target_id, error=error)
        return self._directory_mutation(
            action=action,
            target_id=target_id,
            mutate=target.rmdir,
            undo=target.mkdir,
            details={"path": target_id},
            version_action="delete",
            source_path=target_id,
        )

    def rollback_audit(
        self,
        audit_id: str,
        *,
        mutation_context: MemoryMutationContext | None,
    ) -> db.MutationResult:
        action = "memory.rollback"
        if error := self._authorization_error(mutation_context, action, audit_id):
            return db.MutationResult(False, action, target_id=audit_id, error=error)
        event = db.get_audit_event(audit_id)
        backup = str(event.get("backup_path") or "") if event else ""
        if event and event.get("target_type") == "workspace_file" and backup.startswith("workspace_version://"):
            return self._rollback_file(event, audit_id, backup)
        if event and event.get("target_type") == "directory" and backup.startswith("directory_version://"):
            return self._rollback_directory(event, audit_id, backup)
        return db.MutationResult(False, action, target_id=audit_id, error="no restorable workspace version")

    def _rollback_file(self, event: dict, audit_id: str, backup: str) -> db.MutationResult:
        target = Path(str(event.get("target_id") or ""))
        row = db.connect().execute(
            "SELECT target_path, content, existed FROM workspace_file_versions WHERE id = ?",
            (backup.removeprefix("workspace_version://"),),
        ).fetchone()
        if not row or row["target_path"] != str(target):
            return db.MutationResult(False, "memory.rollback", target_id=str(target), error="workspace version not found")
        restored_content: bytes | None = row["content"] if row["existed"] else None
        if isinstance(restored_content, str):
            restored_content = restored_content.encode("utf-8")
        return self._mutate_file(
            target,
            restored_content,
            action="workspace.file.restore",
            reason=f"rollback audit {audit_id}",
            details={"rolled_back_event": audit_id},
            allow_missing=True,
        )

    def _rollback_directory(self, event: dict, audit_id: str, backup: str) -> db.MutationResult:
        row = db.connect().execute(
            """SELECT action, source_path, target_path
               FROM workspace_directory_versions WHERE id = ?""",
            (backup.removeprefix("directory_version://"),),
        ).fetchone()
        if not row:
            return db.MutationResult(False, "memory.rollback", target_id=audit_id, error="directory version not found")
        source = Path(row["source_path"])
        target = Path(row["target_path"]) if row["target_path"] else None
        details = {"rolled_back_event": audit_id}
        if row["action"] == "create":
            return self._directory_mutation(
                action="filesystem.directory.restore",
                target_id=str(source),
                mutate=source.rmdir,
                undo=source.mkdir,
                details=details,
                version_action="delete",
                source_path=str(source),
            )
        if row["action"] == "rename" and target is not None:
            return self._directory_mutation(
                action="filesystem.directory.restore",
                target_id=f"{target} -> {source}",
                mutate=lambda: target.rename(source),
                undo=lambda: source.rename(target),
                details=details,
                version_action="rename",
                source_path=str(target),
                target_path=str(source),
            )
        if row["action"] == "delete":
            return self._directory_mutation(
                action="filesystem.directory.restore",
                target_id=str(source),
                mutate=source.mkdir,
                undo=source.rmdir,
                details=details,
                version_action="create",
                source_path=str(source),
            )
        return db.MutationResult(False, "memory.rollback", target_id=audit_id, error="invalid directory version")

    def _write_file_bytes(
        self,
        target: Path,
        content: bytes,
        *,
        action: str,
        reason: str,
        details: dict[str, str] | None = None,
        allow_create: bool = False,
    ) -> db.MutationResult:
        return self._mutate_file(
            target,
            content,
            action=action,
            reason=reason,
            details=details,
            allow_missing=allow_create,
        )

    def _mutate_file(
        self,
        target: Path,
        content: bytes | None,
        *,
        action: str,
        reason: str,
        details: dict[str, str] | None = None,
        allow_missing: bool = False,
    ) -> db.MutationResult:
        target_id = str(target)
        with _MUTATION_LOCK:
            try:
                previous = target.read_bytes()
                existed = True
            except FileNotFoundError as exc:
                if not allow_missing:
                    return db.MutationResult(False, action, target_id=target_id, error=str(exc))
                previous = b""
                existed = False
            except OSError as exc:
                return db.MutationResult(False, action, target_id=target_id, error=str(exc))
            if (content is None and not existed) or (content is not None and existed and previous == content):
                return db.MutationResult(False, action, target_id=target_id, error="no changes")

            conn = db.connect()
            try:
                version_id = self._save_file_version(target_id, previous, reason, existed=existed)
                audit_id = db._record_audit(
                    action=action,
                    target_id=target_id,
                    target_type="workspace_file",
                    reason=reason,
                    backup_path=version_id,
                    status="pending",
                    details={"changed_rows": 1, **(details or {})},
                    commit=False,
                )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                return db.MutationResult(False, action, target_id=target_id, error=str(exc))

            try:
                if content is None:
                    target.unlink()
                else:
                    self._atomic_write(target, content)
                self._complete_audit(audit_id)
            except Exception as exc:
                conn.rollback()
                try:
                    if existed:
                        self._atomic_write(target, previous)
                    else:
                        target.unlink(missing_ok=True)
                except Exception as recovery_exc:
                    return db.MutationResult(
                        False,
                        action,
                        target_id=target_id,
                        version_id=version_id,
                        audit_id=audit_id,
                        error=f"{exc}; recovery failed: {recovery_exc}",
                        details={"recovery_pending": True},
                    )
                self._discard_version(audit_id, version_id)
                return db.MutationResult(False, action, target_id=target_id, error=str(exc))
            return db.MutationResult(
                True,
                action,
                target_id=target_id,
                changed_rows=1,
                version_id=version_id,
                audit_id=audit_id,
                details=details or {},
            )

    @staticmethod
    def _authorization_error(
        mutation_context: MemoryMutationContext | None,
        action: str,
        target_id: str,
    ) -> str:
        if mutation_context is None:
            return "mutation context required"
        return mutation_context.authorization_error(action, target_id)

    @staticmethod
    def _save_file_version(target_path: str, content: bytes, reason: str, *, existed: bool = True) -> str:
        cursor = db.connect().execute(
            """INSERT INTO workspace_file_versions (target_path, content, existed, saved_at, reason)
               VALUES (?, ?, ?, ?, ?)""",
            (target_path, content, int(existed), db._now(), reason),
        )
        return f"workspace_version://{cursor.lastrowid}"

    @staticmethod
    def _save_directory_version(
        action: str,
        source_path: str,
        target_path: str | None,
        reason: str,
    ) -> str:
        cursor = db.connect().execute(
            """INSERT INTO workspace_directory_versions
               (action, source_path, target_path, saved_at, reason)
               VALUES (?, ?, ?, ?, ?)""",
            (action, source_path, target_path, db._now(), reason),
        )
        return f"directory_version://{cursor.lastrowid}"

    @staticmethod
    def _complete_audit(audit_id: str) -> None:
        conn = db.connect()
        cursor = conn.execute(
            "UPDATE memory_audit SET status = 'completed' WHERE id = ? AND status = 'pending'",
            (audit_id,),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("pending audit event not found")
        conn.commit()

    @staticmethod
    def _atomic_write(target: Path, content: bytes) -> None:
        fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            if target.exists():
                os.chmod(temp_name, target.stat().st_mode)
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _discard_version(audit_id: str, version_id: str) -> None:
        conn = db.connect()
        conn.execute("DELETE FROM memory_audit WHERE id = ?", (audit_id,))
        if version_id.startswith("workspace_version://"):
            conn.execute(
                "DELETE FROM workspace_file_versions WHERE id = ?",
                (version_id.removeprefix("workspace_version://"),),
            )
        elif version_id.startswith("directory_version://"):
            conn.execute(
                "DELETE FROM workspace_directory_versions WHERE id = ?",
                (version_id.removeprefix("directory_version://"),),
            )
        conn.commit()

    def _directory_mutation(
        self,
        *,
        action: str,
        target_id: str,
        mutate: Callable[[], object],
        undo: Callable[[], object],
        details: dict[str, str],
        version_action: str,
        source_path: str,
        target_path: str | None = None,
    ) -> db.MutationResult:
        with _MUTATION_LOCK:
            conn = db.connect()
            try:
                version_id = self._save_directory_version(
                    version_action,
                    source_path,
                    target_path,
                    action,
                )
                audit_id = db._record_audit(
                    action=action,
                    target_id=target_id,
                    target_type="directory",
                    reason=action,
                    backup_path=version_id,
                    status="pending",
                    details={**details, "changed_rows": 1},
                    commit=False,
                )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                return db.MutationResult(False, action, target_id=target_id, error=str(exc))
            changed = False
            try:
                mutate()
                changed = True
                self._complete_audit(audit_id)
            except Exception as exc:
                conn.rollback()
                if changed:
                    try:
                        undo()
                    except Exception as recovery_exc:
                        return db.MutationResult(
                            False,
                            action,
                            target_id=target_id,
                            version_id=version_id,
                            audit_id=audit_id,
                            error=f"{exc}; recovery failed: {recovery_exc}",
                            details={"recovery_pending": True},
                        )
                self._discard_version(audit_id, version_id)
                return db.MutationResult(False, action, target_id=target_id, error=str(exc))
            return db.MutationResult(
                True,
                action,
                target_id=target_id,
                changed_rows=1,
                version_id=version_id,
                audit_id=audit_id,
                details=details,
            )
