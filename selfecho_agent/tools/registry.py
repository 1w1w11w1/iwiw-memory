from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from selfecho_session import SessionMemoryService

from ..core import PermissionLevel, ToolRiskLevel, ToolResult


ToolHandler = Callable[[str, dict], ToolResult]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    required_permission: PermissionLevel
    risk_level: ToolRiskLevel
    handler: ToolHandler

    def public_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "required_permission": self.required_permission,
            "risk_level": self.risk_level,
        }


class ToolRegistry:
    def __init__(self, session_service: SessionMemoryService) -> None:
        self.session_service = session_service
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list(self) -> list[dict]:
        return [spec.public_dict() for spec in sorted(self._tools.values(), key=lambda item: item.name)]
