from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from selfecho_session import SessionMemoryService

from .core.orchestrator import AgentOrchestrator
from .core.types import AgentRequest, AgentStreamEvent


@dataclass(frozen=True)
class AgentResponse:
    answer: str
    trace: dict[str, Any]


class AgentRunner:
    """Compatibility facade for the API layer.

    New harness behavior belongs in AgentOrchestrator and its collaborators.
    """

    def __init__(self, session_service: SessionMemoryService, prompt_reader) -> None:
        self.orchestrator = AgentOrchestrator(session_service, prompt_reader)

    async def respond(self, session_id: str, message: str) -> AgentResponse:
        result = await self.orchestrator.run(AgentRequest(session_id=session_id, message=message))
        return AgentResponse(answer=result.answer, trace=result.trace)

    async def respond_stream(self, session_id: str, message: str) -> AsyncIterator[AgentStreamEvent]:
        async for event in self.orchestrator.run_stream(AgentRequest(session_id=session_id, message=message)):
            yield event
