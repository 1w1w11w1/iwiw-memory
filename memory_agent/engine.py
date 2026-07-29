"""Memory engine: full storage, search-style recall, and tendency maintenance.

The module's external interface is intentionally small:
- capture_message/capture_material: deterministic full storage.
- build_context: search-engine recall plus compiled behavior tendency context.
- maintain_tendencies/compile_tendency: periodic strategy/profile maintenance.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from selfecho_model import ModelGateway

from . import db
from .extractor import extract_tendency_observations
from .query_builder import build_queries
from .scopes import ActiveMemoryScope, TendencyScope, normalize_tendency_scope
from .tendency_compiler import TendencyCompiler


RECALL_WEIGHTS = {
    "match": 0.62,
    "scope": 0.23,
    "time": 0.15,
}
NORMAL_RECALL_THRESHOLD = 0.45
STRONG_RECALL_THRESHOLD = 0.35
FOCUSED_RECALL_THRESHOLD = 0.68
MIN_MATCH_SCORE = 0.20

_STRONG_RECALL_PATTERNS = [
    re.compile(r"(之前|上次|以前|记得|我们说过|你之前|你提过)"),
    re.compile(r"(这个项目|接下来|继续推进|当时为什么|之前怎么)"),
    re.compile(r"(我的偏好|我的计划|我的情况|我的习惯)"),
    re.compile(r"(删除记忆|修改记忆|纠正记忆|更新记忆)"),
]
_TENDENCY_TRIGGER_PATTERNS = [
    re.compile(r"(以后|每次|默认|我偏好|我习惯|不要再|你应该|改成|记住)"),
    re.compile(r"(这个项目|架构|规范|原则|工作流|重构|风险|权限)"),
]
FULL_ACCESS_PERMISSION = "full_access"
TRUSTED_MUTATION_SOURCES = {"trusted_gui", "internal"}


@dataclass(frozen=True)
class MemoryMutationContext:
    """Action-bound authority for one high-risk memory mutation."""

    permission_profile: str
    confirmed: bool
    source: str
    action: str
    target_id: str

    def authorization_error(self, action: str, target_id: str) -> str:
        if not self.confirmed:
            return "confirmation required"
        if self.permission_profile != FULL_ACCESS_PERMISSION:
            return "full_access permission required"
        if self.source not in TRUSTED_MUTATION_SOURCES:
            return "trusted mutation source required"
        if self.action != action or self.target_id != target_id:
            return "confirmation does not match requested action"
        return ""

    def delegated(self, action: str, target_id: str) -> "MemoryMutationContext":
        return MemoryMutationContext(
            permission_profile=self.permission_profile,
            confirmed=self.confirmed,
            source=self.source,
            action=action,
            target_id=target_id,
        )


@dataclass(frozen=True)
class CapturedMemory:
    ok: bool
    record_id: str | None = None
    changed_rows: int = 0
    audit_id: str | None = None
    chunks_created: int = 0
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "record_id": self.record_id,
            "changed_rows": self.changed_rows,
            "audit_id": self.audit_id,
            "chunks_created": self.chunks_created,
            "error": self.error,
            "details": self.details,
        }


@dataclass(frozen=True)
class MemoryContext:
    tendency_context: str
    recalled_context: str
    gate_decision: str
    trace: dict[str, Any]

    @property
    def sections(self) -> list[str]:
        return [section for section in (self.tendency_context, self.recalled_context) if section.strip()]


@dataclass(frozen=True)
class TendencyMaintenanceResult:
    ok: bool
    observed_ids: list[str] = field(default_factory=list)
    compiled: list[dict[str, Any]] = field(default_factory=list)
    skipped_reason: str = ""
    errors: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "observed_ids": self.observed_ids,
            "compiled": self.compiled,
            "skipped_reason": self.skipped_reason,
            "errors": self.errors,
            "details": self.details,
        }


class MemoryEngine:
    """Deep module for IwIw memory behavior."""

    DIRTY_OBSERVATION_THRESHOLD = 3

    def __init__(
        self,
        *,
        compiler: TendencyCompiler | None = None,
        model_gateway: ModelGateway | None = None,
    ) -> None:
        self.model_gateway = model_gateway or ModelGateway()
        self.compiler = compiler or TendencyCompiler(self.model_gateway)
        self._tendency_compile_lock = threading.Lock()

    def capture_material(
        self,
        *,
        content: str,
        source_type: str = "manual",
        role: str = "user",
        session_id: str | None = None,
        project_id: str | None = None,
        turn_idx: int | None = None,
        private: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> CapturedMemory:
        """Store raw material deterministically before any LLM judgement."""
        if not content.strip():
            return CapturedMemory(False, error="content is empty")
        scope_type = "project" if project_id else ("session" if session_id else "global")
        privacy = "sensitive" if private else ("public" if scope_type == "global" else "internal")
        try:
            result = db.insert_record(
                source_type=source_type,
                content=content,
                scope_type=scope_type,
                project_id=project_id,
                session_id=session_id,
                turn_idx=turn_idx,
                role=role,
                privacy=privacy,
                metadata=metadata,
            )
        except Exception as exc:
            return CapturedMemory(False, error=str(exc))
        if not result.ok:
            return CapturedMemory(False, error=result.error, details=result.details)

        return CapturedMemory(
            True,
            record_id=result.target_id,
            changed_rows=result.changed_rows,
            audit_id=result.audit_id,
            chunks_created=0,
            details={
                **result.details,
                "privacy": privacy,
                "scope_type": scope_type,
                "vector_status": "dirty" if result.changed_rows > 0 else "unchanged",
            },
        )

    def list_records(
        self,
        *,
        source_type: str | None = None,
        limit: int = 200,
        active_scope: ActiveMemoryScope | None = None,
        include_all: bool = False,
    ) -> list[dict[str, Any]]:
        if include_all:
            return db.list_records(source_type=source_type, limit=limit)
        scope = active_scope or ActiveMemoryScope()
        return db.list_visible_records(
            source_type=source_type,
            project_id=scope.project_id,
            session_id=scope.session_id,
            limit=limit,
        )

    def search_records(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Search the complete library for the trusted management UI."""
        hits = db.search_fts(query, limit=limit, include_all=True)
        records: list[dict[str, Any]] = []
        for hit in hits:
            record = db.get_record(str(hit.get("record_id") or ""))
            if record:
                records.append(record)
        return records

    def get_record(
        self,
        record_id: str,
        *,
        active_scope: ActiveMemoryScope | None = None,
        include_all: bool = False,
    ) -> dict[str, Any] | None:
        record = db.get_record(record_id)
        if not record or db.record_belongs_to_tombstoned_session(record_id):
            return None
        if not include_all and not _record_is_visible(record, active_scope or ActiveMemoryScope()):
            return None
        return record

    def get_record_version(self, record_id: str, version: str) -> dict[str, Any] | None:
        if db.record_belongs_to_tombstoned_session(record_id):
            return None
        return db.get_record_version(record_id, version)

    def list_record_versions(self, record_id: str) -> list[dict[str, Any]]:
        if db.record_belongs_to_tombstoned_session(record_id):
            return []
        return db.list_record_versions(record_id)

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return db.list_audit_events(limit)

    def get_audit_event(self, audit_id: str) -> dict[str, Any] | None:
        return db.get_audit_event(audit_id)

    def stats(self, active_scope: ActiveMemoryScope | None = None) -> dict[str, Any]:
        if active_scope is None:
            return db.get_stats()
        return db.get_stats(
            project_id=active_scope.project_id,
            session_id=active_scope.session_id,
            workspace_id=active_scope.workspace_id,
            include_all=False,
        )

    def refresh_dirty_vectors(self, limit: int = 20) -> dict[str, Any]:
        refreshed = 0
        errors: list[str] = []
        # ponytail: refresh a small batch on demand; add a durable worker only
        # when the local library makes search latency measurable.
        for record_id in db.list_dirty_record_ids(limit=limit):
            result = db.refresh_record_vectors(record_id)
            if result.get("ok"):
                refreshed += 1
            elif result.get("reason"):
                errors.append(str(result["reason"]))
        return {"attempted": refreshed + len(errors), "refreshed": refreshed, "errors": errors}

    def tendency_overview(self, active_scope: ActiveMemoryScope, limit: int = 50) -> dict[str, Any]:
        scopes = {
            "agent_global": active_scope.agent_global(),
            "workspace": active_scope.workspace(),
            "session": active_scope.session(),
        }
        profiles: dict[str, dict[str, Any] | None] = {}
        observations: dict[str, list[dict[str, Any]]] = {}
        for name, scope in scopes.items():
            if not scope:
                profiles[name] = None
                observations[name] = []
                continue
            profile = db.get_profile_by_scope(scope.kind, scope.key)
            if profile:
                profile["history"] = db.list_profile_versions(profile["id"])
            profiles[name] = profile
            if name == "session":
                observations[name] = db.list_observations(
                    scope_kind=scope.kind,
                    scope_key=scope.key,
                    status="active",
                    limit=limit,
                )
                continue
            session_scope = active_scope.session()
            if not session_scope:
                observations[name] = []
                continue
            candidates = db.list_observations(
                scope_kind="session",
                scope_key=session_scope.key,
                status=None,
                suggested_scope_kind=name,
                limit=limit,
            )
            existing_ids = _source_observation_ids(profile)
            observations[name] = [
                item
                for item in candidates
                if item.get("status") != "deleted"
                and item.get("id") not in existing_ids
                and (name != "workspace" or item.get("workspace_id") == active_scope.workspace_id)
            ]
        return {
            "profiles": db.list_profiles(),
            "active_scope": active_scope.__dict__,
            "agent_global_profile": profiles["agent_global"],
            "workspace_profile": profiles["workspace"],
            "session_profile": profiles["session"],
            "observations": observations,
            "stats": db.get_stats(),
        }

    def update_record(
        self,
        record_id: str,
        *,
        content: str | None = None,
        description: str | None = None,
        reason: str = "",
        mutation_context: MemoryMutationContext | None = None,
    ) -> db.MutationResult:
        if error := _mutation_authorization_error(mutation_context, "memory.update", record_id):
            return db.MutationResult(False, "update", target_id=record_id, error=error)
        return db.update_record(
            record_id,
            content=content,
            description=description,
            reason=reason or "update memory",
        )

    def delete_record(
        self,
        record_id: str,
        *,
        reason: str = "",
        mutation_context: MemoryMutationContext | None = None,
    ) -> db.MutationResult:
        if error := _mutation_authorization_error(mutation_context, "memory.delete", record_id):
            return db.MutationResult(False, "delete", target_id=record_id, error=error)
        return db.update_record_status(record_id, "deleted", reason=reason or "delete memory")

    def restore_record_version(
        self,
        record_id: str,
        version: str,
        *,
        mutation_context: MemoryMutationContext | None = None,
    ) -> db.MutationResult:
        if error := _mutation_authorization_error(mutation_context, "memory.restore", record_id):
            return db.MutationResult(False, "restore", target_id=record_id, error=error)
        return db.restore_record_version(record_id, version)

    def rollback_audit(
        self,
        audit_id: str,
        *,
        mutation_context: MemoryMutationContext | None = None,
    ) -> db.MutationResult:
        if error := _mutation_authorization_error(mutation_context, "memory.rollback", audit_id):
            return db.MutationResult(False, "rollback", error=error)
        event = db.get_audit_event(audit_id)
        if not event:
            return db.MutationResult(False, "rollback", error="audit event not found")
        target_id = str(event.get("target_id") or "")
        backup = str(event.get("backup_path") or "")
        if backup.startswith("tendency_version://"):
            return db.restore_tendency_mutation_version(backup.removeprefix("tendency_version://"))
        if not target_id or not backup.startswith("version://"):
            return db.MutationResult(False, "rollback", target_id=target_id or None, error="no restorable version")
        result = db.restore_record_version(target_id, backup.removeprefix("version://"))
        if result.ok:
            result.details["rolled_back_event"] = audit_id
        return result

    def build_context(
        self,
        *,
        user_message: str,
        context_messages: list[str] | None = None,
        active_scope: ActiveMemoryScope | None = None,
        top_k: int = 5,
        max_tendency_chars: int = 1400,
        max_fact_chars: int = 2000,
    ) -> MemoryContext:
        scope = active_scope or ActiveMemoryScope()
        try:
            tendency_context, profile_ids, tendency_budget = self._load_tendency(scope, max_tendency_chars)
            recalled_items, recall_trace = self.search(
                query=user_message,
                context_messages=context_messages,
                active_scope=scope,
                top_k=top_k,
            )
        except Exception as exc:
            return MemoryContext(
                tendency_context="",
                recalled_context="",
                gate_decision="unavailable",
                trace={
                    "memory_available": False,
                    "error": str(exc),
                    "active_scope": scope.__dict__,
                    "tendency_profile_ids": [],
                    "retrieved_record_ids": [],
                },
            )
        recalled_context, fact_budget = self._format_recalled(recalled_items, max_fact_chars)
        return MemoryContext(
            tendency_context=tendency_context,
            recalled_context=recalled_context,
            gate_decision=recall_trace.get("decision", "none"),
            trace={
                **recall_trace,
                "memory_available": True,
                "active_scope": scope.__dict__,
                "tendency_profile_ids": sorted(profile_ids),
                "retrieved_record_ids": [item["record_id"] for item in recalled_items],
                "budget": {
                    "tendency": tendency_budget,
                    "recalled": fact_budget,
                },
            },
        )

    def search(
        self,
        *,
        query: str,
        context_messages: list[str] | None = None,
        active_scope: ActiveMemoryScope | None = None,
        top_k: int = 5,
        source_type: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        scope = active_scope or ActiveMemoryScope()
        queries = build_queries(query, context_messages)
        if not queries:
            return [], {"decision": "none", "reason": "no queries", "queries": []}

        strong_signal = any(pattern.search(query) for pattern in _STRONG_RECALL_PATTERNS)
        vector_maintenance = (
            self.refresh_dirty_vectors()
            if strong_signal
            else {"attempted": 0, "refreshed": 0, "errors": [], "skipped": "light retrieval"}
        )
        candidate_multiplier = 8 if strong_signal else 4

        vector_scores: dict[str, list[float]] = {}
        fts_hits: dict[str, dict[str, Any]] = {}
        for q in queries:
            try:
                from .embedding import embed_text
                q_vec = embed_text(q)
            except Exception:
                q_vec = None
            if q_vec:
                for row in db.search_vectors(
                    q_vec,
                    top_k=top_k * candidate_multiplier,
                    project_id=scope.project_id,
                    session_id=scope.session_id,
                    source_type=source_type,
                ):
                    vector_scores.setdefault(str(row["record_id"]), []).append(float(row.get("similarity") or 0))
            for row in db.search_fts(
                q,
                limit=top_k * candidate_multiplier,
                project_id=scope.project_id,
                session_id=scope.session_id,
                source_type=source_type,
            ):
                rid = str(row.get("record_id") or "")
                if rid:
                    fts_hits.setdefault(rid, row)

        record_ids = set(vector_scores) | set(fts_hits)
        scored: list[dict[str, Any]] = []
        for record_id in record_ids:
            record = db.get_record(record_id)
            if not record or record.get("status") != "active" or record.get("privacy") == "sensitive":
                continue
            score = self._score_record(record, vector_scores.get(record_id, []), record_id in fts_hits, scope)
            if score <= 0:
                continue
            scored.append({
                "record_id": record_id,
                "record_id_short": record_id[:8],
                "content": str(record.get("content") or "")[:700],
                "source_type": record.get("source_type", ""),
                "scope_type": record.get("scope_type", ""),
                "created_at": record.get("created_at", ""),
                "updated_at": record.get("updated_at", ""),
                "score": round(score, 4),
            })
        scored.sort(key=lambda item: item["score"], reverse=True)

        threshold = STRONG_RECALL_THRESHOLD if strong_signal else NORMAL_RECALL_THRESHOLD
        qualifying = [item for item in scored if item["score"] >= threshold]
        if not qualifying:
            return [], {
                "decision": "none",
                "queries": queries,
                "threshold_used": threshold,
                "strong_signal": strong_signal,
                "total_scored": len(scored),
                "depth": "deep" if strong_signal else "light",
                "vector_maintenance": vector_maintenance,
                "suppressed_record_ids": [item["record_id"] for item in scored[: top_k * 2]],
            }

        focused = [item for item in qualifying if item["score"] >= FOCUSED_RECALL_THRESHOLD]
        decision = "deep" if strong_signal else ("focused" if len(focused) >= 2 else "light")
        limit = top_k if strong_signal else (5 if decision == "focused" else 3)
        injected = qualifying[:limit]
        return injected, {
            "decision": decision,
            "queries": queries,
            "threshold_used": threshold,
            "strong_signal": strong_signal,
            "depth": "deep" if strong_signal else "light",
            "vector_maintenance": vector_maintenance,
            "total_scored": len(scored),
            "total_injected": len(injected),
        }

    async def maintain_tendencies(
        self,
        *,
        text: str,
        session_id: str,
        active_scope: ActiveMemoryScope | None = None,
        turn_range: str | None = None,
        context: str = "",
        force_compile: bool = False,
    ) -> TendencyMaintenanceResult:
        scope = active_scope or ActiveMemoryScope.for_session(session_id=session_id)
        session_scope = scope.session()
        if not session_scope:
            return TendencyMaintenanceResult(False, errors=["session_id is required for tendency observation"])
        observed_ids: list[str] = []
        errors: list[str] = []

        should_observe = force_compile or _should_extract_tendency(text)
        if should_observe and text.strip():
            try:
                observations = await extract_tendency_observations(
                    text,
                    context,
                    model_gateway=self.model_gateway,
                )
            except Exception as exc:
                observations = []
                errors.append(str(exc))
            for raw in observations:
                content = str(raw.get("content") or "").strip()
                if not content:
                    continue
                suggested_scope = self._resolve_tendency_scope(raw, scope)
                obs_id = db.insert_observation(
                    content=content,
                    scope_kind=session_scope.kind,
                    scope_key=session_scope.key,
                    workspace_id=session_scope.workspace_id,
                    project_id=session_scope.project_id,
                    source_session_id=session_id,
                    source_turn_range=turn_range,
                    suggested_scope_kind=suggested_scope.kind,
                )
                if obs_id:
                    observed_ids.append(obs_id)

        compiled: list[dict[str, Any]] = []
        for tendency_scope in [session_scope]:
            active_count = len(db.list_observations(
                scope_kind=tendency_scope.kind,
                scope_key=tendency_scope.key,
                status="active",
                limit=1000,
            ))
            if not force_compile and active_count < self.DIRTY_OBSERVATION_THRESHOLD:
                continue
            if active_count <= 0:
                continue
            result = await self.compile_tendency(
                scope_kind=tendency_scope.kind,
                scope_key=tendency_scope.key,
                workspace_id=tendency_scope.workspace_id,
                project_id=tendency_scope.project_id,
                session_id=session_id,
            )
            compiled.append(result)
            if not result.get("ok") and result.get("error"):
                errors.append(str(result["error"]))

        return TendencyMaintenanceResult(
            ok=not errors,
            observed_ids=observed_ids,
            compiled=compiled,
            skipped_reason="" if observed_ids or compiled else "no tendency maintenance due",
            errors=errors,
            details={"force_compile": force_compile, "turn_range": turn_range},
        )

    async def compile_tendency(
        self,
        *,
        scope_kind: str,
        scope_key: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        rebuild: bool = False,
        mutation_context: MemoryMutationContext | None = None,
    ) -> dict[str, Any]:
        try:
            normalized_kind, normalized_key = normalize_tendency_scope(scope_kind, scope_key)
        except ValueError as exc:
            return {"ok": False, "action": "rebuild" if rebuild else "compile", "error": str(exc)}
        promotion = normalized_kind != "session"
        mutation_action = "tendency.rebuild" if rebuild else "tendency.promote"
        authorization_error = _mutation_authorization_error(
            mutation_context,
            mutation_action,
            f"{normalized_kind}:{normalized_key}",
        )
        if (rebuild or promotion) and authorization_error:
            return {
                "ok": False,
                "action": "rebuild" if rebuild else "compile",
                "error": authorization_error,
            }

        observations: list[dict[str, Any]] | None = None
        if promotion:
            observations = db.list_observations(
                scope_kind=normalized_kind,
                scope_key=normalized_key,
                status=None if rebuild else "active",
                limit=100,
            )
            if session_id:
                overlay = db.list_observations(
                    scope_kind="session",
                    scope_key=session_id,
                    status=None,
                    suggested_scope_kind=normalized_kind,
                    limit=100,
                )
                if normalized_kind == "workspace":
                    overlay = [item for item in overlay if item.get("workspace_id") == workspace_id]
                observations.extend(item for item in overlay if item.get("status") != "deleted")
            if not rebuild:
                profile = db.get_profile_by_scope(normalized_kind, normalized_key)
                existing_ids = _source_observation_ids(profile)
                observations = [item for item in observations if item.get("id") not in existing_ids]

        while not self._tendency_compile_lock.acquire(blocking=False):
            await asyncio.sleep(0.05)
        try:
            if rebuild and not promotion:
                result = await self.compiler.rebuild(
                    scope_kind=normalized_kind,
                    scope_key=normalized_key,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    session_id=session_id,
                )
            else:
                result = await self.compiler.compile(
                    scope_kind=normalized_kind,
                    scope_key=normalized_key,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    session_id=session_id,
                    observation_status=None if rebuild else "active",
                    action="rebuild" if rebuild else "compile",
                    observations=observations,
                )
        finally:
            self._tendency_compile_lock.release()
        data = result.to_dict()
        data.update({
            "scope_kind": normalized_kind,
            "scope_key": normalized_key,
            "workspace_id": workspace_id,
            "project_id": project_id,
        })
        return data

    @staticmethod
    def _score_record(
        record: dict[str, Any],
        vector_scores: list[float],
        keyword_hit: bool,
        scope: ActiveMemoryScope,
    ) -> float:
        semantic = max(vector_scores) if vector_scores else 0.0
        keyword = 0.82 if keyword_hit else 0.0
        match_score = max(semantic, keyword)
        if match_score < MIN_MATCH_SCORE:
            return 0.0
        scope_score = _scope_score(record, scope)
        time_score = _time_score(record.get("updated_at") or record.get("created_at"))
        return (
            RECALL_WEIGHTS["match"] * match_score
            + RECALL_WEIGHTS["scope"] * scope_score
            + RECALL_WEIGHTS["time"] * time_score
        )

    @staticmethod
    def _load_tendency(
        active_scope: ActiveMemoryScope,
        max_chars: int,
    ) -> tuple[str, set[str], dict[str, int]]:
        parts: list[str] = []
        profile_ids: set[str] = set()
        chars_used = 0
        truncated = 0
        for scope in active_scope.tendency_chain():
            profile = db.get_profile_by_scope(scope.kind, scope.key)
            if not profile:
                continue
            content = str(profile.get("content") or "").strip()
            if not content:
                continue
            entry = f"### {scope.label}\n{content[:500]}"
            if chars_used + len(entry) > max_chars:
                truncated += 1
                continue
            parts.append(entry)
            chars_used += len(entry)
            if profile.get("id"):
                profile_ids.add(str(profile["id"]))
        if not parts:
            return "", set(), {
                "max_chars": max_chars,
                "used_chars": 0,
                "included": 0,
                "truncated": truncated,
            }
        return (
            "## 长期倾向\n"
            "这些内容影响回应方式、决策默认值和风险判断；它们不是事实检索结果。\n\n"
            + "\n\n".join(parts),
            profile_ids,
            {
                "max_chars": max_chars,
                "used_chars": chars_used,
                "included": len(parts),
                "truncated": truncated,
            },
        )

    @staticmethod
    def _format_recalled(items: list[dict[str, Any]], max_chars: int) -> tuple[str, dict[str, int]]:
        if not items:
            return "", {"max_chars": max_chars, "used_chars": 0, "included": 0, "truncated": 0}
        lines = [
            "## 相关记忆",
            "以下片段来自全量记忆库的检索结果。只在确实相关时使用；不要把它们当作当前事实的唯一依据。",
            "",
        ]
        used = 0
        included = 0
        for item in items:
            content = " ".join(str(item.get("content") or "").split())[:360]
            entry = f"- [{item.get('record_id_short')}] score={item.get('score')}: {content}"
            if used + len(entry) > max_chars:
                break
            lines.append(entry)
            used += len(entry)
            included += 1
        return (
            "\n".join(lines) if included else "",
            {
                "max_chars": max_chars,
                "used_chars": used,
                "included": included,
                "truncated": len(items) - included,
            },
        )

    @staticmethod
    def _resolve_tendency_scope(
        raw: dict[str, Any],
        active_scope: ActiveMemoryScope,
    ) -> TendencyScope:
        requested = str(raw.get("scope_kind") or raw.get("scope_type") or "session").strip()
        if requested == "global":
            requested = "agent_global"
        if requested == "project":
            requested = "workspace"
        if requested == "agent_global":
            return active_scope.agent_global()
        if requested == "workspace":
            return active_scope.workspace() or active_scope.session() or active_scope.agent_global()
        return active_scope.session() or active_scope.workspace() or active_scope.agent_global()


def _should_extract_tendency(text: str) -> bool:
    if not text.strip():
        return False
    if any(pattern.search(text) for pattern in _TENDENCY_TRIGGER_PATTERNS):
        return True
    return len(text.strip()) >= 160


def _mutation_authorization_error(
    mutation_context: MemoryMutationContext | None,
    action: str,
    target_id: str,
) -> str:
    if mutation_context is None:
        return "confirmation required"
    return mutation_context.authorization_error(action, target_id)


def _source_observation_ids(profile: dict[str, Any] | None) -> set[str]:
    if not profile:
        return set()
    try:
        values = json.loads(profile.get("source_observation_ids") or "[]")
    except (TypeError, json.JSONDecodeError):
        return set()
    return {str(value) for value in values} if isinstance(values, list) else set()


def _scope_score(record: dict[str, Any], scope: ActiveMemoryScope) -> float:
    scope_type = record.get("scope_type")
    if scope_type == "session" and scope.session_id and record.get("session_id") == scope.session_id:
        return 1.0
    if scope_type == "project" and scope.project_id and record.get("project_id") == scope.project_id:
        return 0.82
    if scope_type == "global":
        return 0.45
    return 0.0


def _record_is_visible(record: dict[str, Any], scope: ActiveMemoryScope) -> bool:
    if record.get("status") != "active" or record.get("privacy") == "sensitive":
        return False
    scope_type = record.get("scope_type")
    if scope_type == "global":
        return record.get("privacy") == "public"
    if scope_type == "project":
        return bool(scope.project_id and record.get("project_id") == scope.project_id)
    if scope_type == "session":
        return bool(scope.session_id and record.get("session_id") == scope.session_id)
    return False


def _time_score(value: str | None) -> float:
    if not value:
        return 0.6
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return 0.6
    age_days = max(0.0, (datetime.now() - dt).total_seconds() / 86400)
    return math.exp(-math.log(2) * age_days / 90)


default_memory_engine = MemoryEngine()
