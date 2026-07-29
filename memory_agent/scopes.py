"""Memory scope types used by the agent-facing memory interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

AGENT_GLOBAL_SCOPE = "agent_global"
WORKSPACE_SCOPE = "workspace"
SESSION_SCOPE = "session"
AGENT_GLOBAL_KEY = "agent"
TENDENCY_SCOPE_KINDS = {AGENT_GLOBAL_SCOPE, WORKSPACE_SCOPE, SESSION_SCOPE}


@dataclass(frozen=True)
class TendencyScope:
    """A single tendency profile scope."""

    kind: str
    key: str
    workspace_id: str | None = None
    project_id: str | None = None

    @property
    def label(self) -> str:
        if self.kind == AGENT_GLOBAL_SCOPE:
            return "全局倾向"
        if self.kind == WORKSPACE_SCOPE:
            return "工作目录倾向"
        if self.kind == SESSION_SCOPE:
            return "当前会话倾向"
        return self.kind


@dataclass(frozen=True)
class ActiveMemoryScope:
    """The current conversation's place in the memory inheritance chain."""

    session_id: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None
    workspace_root: str | None = None

    @classmethod
    def for_session(
        cls,
        *,
        session_id: str | None = None,
        project_id: str | None = None,
        workspace_root: str | None = None,
    ) -> "ActiveMemoryScope":
        workspace_id = workspace_id_from_root(workspace_root) if workspace_root else None
        return cls(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
            workspace_root=workspace_root,
        )

    def agent_global(self) -> TendencyScope:
        return TendencyScope(AGENT_GLOBAL_SCOPE, AGENT_GLOBAL_KEY)

    def workspace(self) -> TendencyScope | None:
        if not self.workspace_id:
            return None
        return TendencyScope(
            WORKSPACE_SCOPE,
            self.workspace_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
        )

    def session(self) -> TendencyScope | None:
        if not self.session_id:
            return None
        return TendencyScope(
            SESSION_SCOPE,
            self.session_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
        )

    def tendency_chain(self) -> list[TendencyScope]:
        chain = [self.agent_global()]
        workspace_scope = self.workspace()
        if workspace_scope:
            chain.append(workspace_scope)
        session_scope = self.session()
        if session_scope:
            chain.append(session_scope)
        return chain

    def compile_scopes(self) -> list[TendencyScope]:
        """Scopes that may receive compiled profiles during maintenance."""
        scopes = [self.agent_global()]
        workspace_scope = self.workspace()
        if workspace_scope:
            scopes.append(workspace_scope)
        session_scope = self.session()
        if session_scope:
            scopes.append(session_scope)
        return scopes


def workspace_id_from_root(root: str | None) -> str | None:
    """Return a stable, human-readable key for a workspace root."""
    if not root or not root.strip():
        return None
    try:
        resolved = Path(root).expanduser().resolve(strict=False)
    except OSError:
        return root.strip()
    return str(resolved).casefold() if resolved.drive else str(resolved)


def normalize_tendency_scope(kind: str, key: str | None = None) -> tuple[str, str]:
    scope_kind = (kind or AGENT_GLOBAL_SCOPE).strip()
    if scope_kind == "global":
        scope_kind = AGENT_GLOBAL_SCOPE
    if scope_kind == "project":
        scope_kind = WORKSPACE_SCOPE
    if scope_kind not in TENDENCY_SCOPE_KINDS:
        scope_kind = AGENT_GLOBAL_SCOPE

    scope_key = (key or "").strip()
    if scope_kind == AGENT_GLOBAL_SCOPE:
        return scope_kind, AGENT_GLOBAL_KEY
    if not scope_key:
        raise ValueError(f"{scope_kind} tendency scope requires scope_key")
    return scope_kind, scope_key
