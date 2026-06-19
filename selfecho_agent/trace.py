from __future__ import annotations

from typing import Any

from selfecho_session import SessionMemoryService

from .core import HarnessStep, ModelCallConfig
from .modes import IntentDecision
from .policy import RiskPolicy


class TraceRecorder:
    """Thin persistence adapter for run events and timeline entries."""

    def __init__(self, session_service: SessionMemoryService) -> None:
        self.session_service = session_service

    def start_run(
        self,
        session_id: str,
        *,
        decision: IntentDecision,
        policy: RiskPolicy,
        context_sections: list[str],
        model: ModelCallConfig,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged = {
            "context_section_count": len(context_sections),
            "policy": policy.to_dict(),
        }
        if metadata:
            merged.update(metadata)
        return self.session_service.start_agent_run(
            session_id,
            intent=decision.intent,
            path=decision.path,
            reason=decision.reason,
            context_sections=context_sections,
            model_role=model.role,
            metadata=merged,
        )

    def step(self, session_id: str, run_id: str, step: HarnessStep) -> None:
        self.session_service.record_agent_event(
            run_id,
            event_type=step.name,
            title=step.title,
            details={
                "summary": step.summary,
                "status": step.status,
                **step.details,
            },
        )
        self.session_service.record_agent_timeline(
            session_id,
            run_id=run_id,
            kind=step.name,
            title=step.title,
            summary=step.summary,
            risk_level=step.risk_level,
            reversible=step.reversible,
            status=step.status,
            details=step.details,
        )

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        metadata: dict[str, Any] | None = None,
        error: str = "",
    ) -> dict[str, Any]:
        return self.session_service.finish_agent_run(
            run_id,
            status=status,
            metadata=metadata or {},
            error=error,
        )
