from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias


StepStatus = Literal[
    "pending",
    "running",
    "waiting_approval",
    "executing_tool",
    "recovering",
    "completed",
    "degraded",
    "blocked",
    "failed",
    "cancelled",
    "skipped",
]

PermissionLevel = Literal["read_only", "guided", "workspace", "full_access"]
ToolRiskLevel = Literal["low", "medium", "high"]
AgentStreamEvent: TypeAlias = dict[str, Any]


@dataclass(frozen=True)
class AgentRequest:
    session_id: str
    message: str


@dataclass(frozen=True)
class ModelCallConfig:
    role: str
    max_tokens: int
    temperature: float
    timeout: float


@dataclass(frozen=True)
class HarnessStep:
    name: str
    title: str
    status: StepStatus = "completed"
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "low"
    reversible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
            "risk_level": self.risk_level,
            "reversible": self.reversible,
        }


@dataclass(frozen=True)
class ToolCallPlan:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    required_permission: PermissionLevel = "read_only"
    risk_level: ToolRiskLevel = "low"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "reason": self.reason,
            "required_permission": self.required_permission,
            "risk_level": self.risk_level,
        }


@dataclass(frozen=True)
class ExecutionPlan:
    goal: str
    mode: str
    steps: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallPlan] = field(default_factory=list)
    requires_clarification: bool = False
    clarifying_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "mode": self.mode,
            "steps": self.steps,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "requires_clarification": self.requires_clarification,
            "clarifying_questions": self.clarifying_questions,
        }


@dataclass(frozen=True)
class ToolResult:
    name: str
    ok: bool
    summary: str
    content: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    risk_level: ToolRiskLevel = "low"
    status: str = ""
    next_actions: list[str] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    root_cause_hint: str = ""
    safe_retry: bool = False
    stop_condition: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "status": self.status or ("ok" if self.ok else "failed"),
            "summary": self.summary,
            "content": self.content,
            "error": self.error,
            "risk_level": self.risk_level,
            "next_actions": self.next_actions,
            "artifacts": self.artifacts,
            "root_cause_hint": self.root_cause_hint,
            "safe_retry": self.safe_retry,
            "stop_condition": self.stop_condition,
        }


@dataclass(frozen=True)
class AgentResult:
    answer: str
    trace: dict[str, Any]
