from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Callable

from selfecho_session import SessionMemoryService

from ..context import ContextBuilder
from ..context_budget import ContextBudget, ContextBudgetReport
from ..model import ModelGateway
from ..modes import IntentRouter
from ..planner import DeterministicPlanner
from ..policy import PolicyEngine
from ..tools import ToolOrchestrator, ToolRegistry, WorkspaceTools
from ..trace import TraceRecorder
from .types import AgentRequest, AgentResult, AgentStreamEvent, ExecutionPlan, HarnessStep, ModelCallConfig, ToolResult


class AgentOrchestrator:
    """Single turn orchestrator for IwIw.

    This is intentionally small in phase one. Planner, tool execution, approval
    gates, and memory proposal will plug in here instead of expanding Runner.
    """

    def __init__(
        self,
        session_service: SessionMemoryService,
        prompt_reader: Callable[[str], str],
        *,
        router: IntentRouter | None = None,
        context_builder: ContextBuilder | None = None,
        policy_engine: PolicyEngine | None = None,
        planner: DeterministicPlanner | None = None,
        model_gateway: ModelGateway | None = None,
        tool_orchestrator: ToolOrchestrator | None = None,
        context_budget: ContextBudget | None = None,
        trace_recorder: TraceRecorder | None = None,
    ) -> None:
        self.session_service = session_service
        self.prompt_reader = prompt_reader
        self.router = router or IntentRouter()
        self.context_builder = context_builder or ContextBuilder(session_service)
        self.policy_engine = policy_engine or PolicyEngine()
        self.planner = planner or DeterministicPlanner()
        self.model_gateway = model_gateway or ModelGateway()
        self.context_budget = context_budget or ContextBudget()
        if tool_orchestrator is None:
            registry = ToolRegistry(session_service)
            WorkspaceTools(session_service).register(registry)
            tool_orchestrator = ToolOrchestrator(registry)
        self.tool_orchestrator = tool_orchestrator
        self.trace = trace_recorder or TraceRecorder(session_service)

    async def run(self, request: AgentRequest) -> AgentResult:
        answer_parts: list[str] = []
        final_answer = ""
        final_trace: dict = {}
        async for event in self.run_stream(request):
            event_type = event.get("type")
            if event_type == "delta":
                answer_parts.append(str(event.get("text") or ""))
                continue
            if event_type == "done":
                final_answer = str(event.get("answer") or "".join(answer_parts))
                trace = event.get("trace")
                final_trace = trace if isinstance(trace, dict) else {}
                continue
            if event_type == "error":
                raise RuntimeError(str(event.get("error") or "agent stream failed"))
        return AgentResult(answer=final_answer or "".join(answer_parts), trace=final_trace)

    async def run_stream(self, request: AgentRequest) -> AsyncIterator[AgentStreamEvent]:
        decision = self.router.classify(request.message)
        policy = self.policy_engine.assess(request.message, decision)
        plan = self.planner.plan(request, decision, policy)
        base_prompt = self.prompt_reader("conversation_reply")
        bundle = self.context_builder.build(request.session_id, decision, base_prompt)
        context_sections = [self._section_title(section) for section in bundle.sections]
        context_budget_report = self.context_budget.measure(bundle.sections)
        model_config = self._model_config(decision.path)

        run = self.trace.start_run(
            request.session_id,
            decision=decision,
            policy=policy,
            context_sections=context_sections,
            model=model_config,
            metadata={
                "message_chars": len(request.message),
                "plan": plan.to_dict(),
                "streaming": True,
                "context_budget": context_budget_report.to_dict(),
            },
        )
        run_id = str(run["id"])
        yield {
            "type": "meta",
            "run_id": run_id,
            "status": "running",
            **bundle.metadata,
            "policy": policy.to_dict(),
            "context_sections": context_sections,
            "context_budget": context_budget_report.to_dict(),
            "plan": plan.to_dict(),
        }

        for step in self._initial_steps(decision, policy, context_sections, context_budget_report, plan):
            self.trace.step(request.session_id, run_id, step)
            yield {"type": "step", "step": step.to_dict()}

        tool_results = self.tool_orchestrator.execute_plan(request.session_id, plan)
        for result in tool_results:
            step = HarnessStep(
                name="tool_result",
                title=f"执行工具：{result.name}",
                status="completed" if result.ok else "failed",
                summary=result.summary,
                details=result.to_dict(),
                risk_level=result.risk_level,
            )
            self.trace.step(request.session_id, run_id, step)
            yield {"type": "step", "step": step.to_dict()}

        plan_section = self._plan_section(plan)
        tool_observation_section = self._tool_observation_section(tool_results)
        prompt_sections = [*bundle.sections, plan_section, tool_observation_section]
        prompt_budget_report = self.context_budget.measure([section for section in prompt_sections if section.strip()])
        model_system_prompt = "\n\n".join(section for section in prompt_sections if section.strip())

        status = "completed"
        error = ""
        answer_parts: list[str] = []
        if not self.model_gateway.streaming_enabled(model_config.role):
            step = HarnessStep(
                name="model_streaming",
                title="模型流式状态",
                status="degraded",
                summary="当前模型提供商关闭流式输出，已退回单次生成。",
                details={"model_role": model_config.role, "streaming": False},
                risk_level=policy.risk_level,
            )
            self.trace.step(request.session_id, run_id, step)
            yield {"type": "model_status", "streaming": False, "reason": "provider_disabled"}
            yield {"type": "step", "step": step.to_dict()}
        try:
            async for chunk in self.model_gateway.stream(
                system_prompt=model_system_prompt,
                user_prompt=request.message,
                config=model_config,
                on_retry=lambda exc: self.trace.step(
                    request.session_id,
                    run_id,
                    HarnessStep(
                        name="model_retry",
                        title="模型流式调用失败，尝试回退",
                        status="running",
                        summary=str(exc),
                        details={"error": str(exc)},
                        risk_level=policy.risk_level,
                    ),
                ),
            ):
                if not chunk:
                    continue
                answer_parts.append(chunk)
                yield {"type": "delta", "text": chunk}
            answer = "".join(answer_parts)
            self.trace.step(
                request.session_id,
                run_id,
                HarnessStep(
                    name="model",
                    title="模型回复完成",
                    summary=f"生成 {len(answer)} 个字符",
                    details={
                        "answer_chars": len(answer),
                        "model_role": model_config.role,
                        "streaming": True,
                        "context_budget": prompt_budget_report.to_dict(),
                    },
                ),
            )
        except Exception as exc:
            status = "degraded"
            error = str(exc)
            answer = (
                "我先把这句话接住。当前模型连接不可用，所以我没法生成完整回复；"
                f"但你的原始消息已经保存在本地会话里。错误信息：{exc}"
            )
            yield {"type": "delta", "text": answer}
            self.trace.step(
                request.session_id,
                run_id,
                HarnessStep(
                    name="failure_recovery",
                    title="模型调用失败",
                    status="failed",
                    summary="已记录错误和恢复建议，等待用户或后续自动恢复。",
                    details={"error": error, "recovery_steps": policy.recovery_steps},
                    risk_level=policy.risk_level,
                ),
            )

        finished = self.trace.finish(
            run_id,
            status=status,
            metadata={
                "answer_chars": len(answer),
                "policy": policy.to_dict(),
                "streaming": True,
                "context_budget": prompt_budget_report.to_dict(),
            },
            error=error,
        )
        finish_step = HarnessStep(
            name="run_finished",
            title="完成本轮回复",
            status=status if status in {"completed", "degraded", "failed", "skipped", "blocked", "cancelled"} else "completed",
            summary=f"{decision.intent} / {decision.path} / {status}",
            details={
                "answer_chars": len(answer),
                "context_sections": context_sections,
                "policy": policy.to_dict(),
                "plan": plan.to_dict(),
                "tool_results": [result.to_dict() for result in tool_results],
                "context_budget": prompt_budget_report.to_dict(),
            },
            risk_level=policy.risk_level,
            reversible=policy.rollback_required,
        )
        self.trace.step(request.session_id, run_id, finish_step)
        yield {
            "type": "done",
            "answer": answer,
            "trace": {
                "run_id": run_id,
                "status": status,
                **bundle.metadata,
                "policy": policy.to_dict(),
                "context_sections": context_sections,
                "context_budget": prompt_budget_report.to_dict(),
                "plan": plan.to_dict(),
                "tool_results": [result.to_dict() for result in tool_results],
                "started_at": finished.get("started_at"),
                "ended_at": finished.get("ended_at"),
            },
        }

    def _initial_steps(
        self,
        decision,
        policy,
        context_sections: list[str],
        context_budget: ContextBudgetReport,
        plan: ExecutionPlan,
    ) -> list[HarnessStep]:
        return [
            HarnessStep(
                name="run_started",
                title="开始处理用户消息",
                status="running",
                summary=policy.guidance,
                details={
                    "intent": decision.intent,
                    "path": decision.path,
                    "reason": decision.reason,
                    "policy": policy.to_dict(),
                },
                risk_level=policy.risk_level,
            ),
            HarnessStep(
                name="intent",
                title="判断本轮模式",
                summary=decision.reason,
                details={"intent": decision.intent, "path": decision.path},
            ),
            HarnessStep(
                name="policy",
                title="评估权限与风险",
                summary=policy.guidance,
                details=policy.to_dict(),
                risk_level=policy.risk_level,
                reversible=policy.rollback_required,
            ),
            HarnessStep(
                name="context",
                title="组装上下文",
                summary=f"已组装 {len(context_sections)} 个上下文段，估算 {context_budget.estimated_tokens} tokens",
                details={"sections": context_sections, "context_budget": context_budget.to_dict()},
            ),
            HarnessStep(
                name="planner",
                title="生成本轮执行计划",
                summary=plan.goal,
                details=plan.to_dict(),
                risk_level=policy.risk_level,
                reversible=policy.rollback_required,
            ),
        ]

    def _section_title(self, section: str) -> str:
        first = section.strip().splitlines()[0] if section.strip() else "未命名上下文"
        return first.strip("# ").strip()

    def _plan_section(self, plan: ExecutionPlan) -> str:
        lines = [
            "## Harness 执行计划",
            f"- goal: {plan.goal}",
            f"- mode: {plan.mode}",
        ]
        if plan.steps:
            lines.append("- steps:")
            lines.extend(f"  - {step}" for step in plan.steps)
        if plan.requires_clarification and plan.clarifying_questions:
            lines.append("- clarification:")
            lines.extend(f"  - {question}" for question in plan.clarifying_questions)
        if plan.tool_calls:
            lines.append("- tool calls:")
            lines.extend(f"  - {call.name}: {call.reason}" for call in plan.tool_calls)
        return "\n".join(lines)

    def _tool_observation_section(self, tool_results: list[ToolResult]) -> str:
        if not tool_results:
            return ""
        lines = [
            "## Harness 工具观察",
            "以下观察来自 IwIw 只读工具。回答工作目录、文件或项目状态时，优先依据这些结果；工具失败时必须说明无法确认，而不是猜测。",
        ]
        for result in tool_results:
            observation = result.to_dict()
            lines.append(f"### {result.name}")
            lines.append(f"- status: {observation['status']}")
            lines.append(f"- summary: {result.summary}")
            if result.error:
                lines.append(f"- error: {result.error}")
            if result.root_cause_hint:
                lines.append(f"- root_cause_hint: {result.root_cause_hint}")
            if result.next_actions:
                lines.append("- next_actions:")
                lines.extend(f"  - {action}" for action in result.next_actions[:5])
            if result.safe_retry:
                lines.append("- safe_retry: true")
            if result.stop_condition:
                lines.append(f"- stop_condition: {result.stop_condition}")
            if result.content:
                lines.extend(self._format_tool_content(result))
        return "\n".join(lines)

    def _format_tool_content(self, result: ToolResult) -> list[str]:
        content = result.content
        lines: list[str] = []
        if result.name == "workspace.list_directory":
            lines.append(f"- root: {content.get('root', '')}")
            lines.append(f"- path: {content.get('path') or '.'}")
            lines.append("- items:")
            for item in content.get("items", [])[:80]:
                size = item.get("size")
                size_text = f", {size} bytes" if isinstance(size, int) else ""
                lines.append(f"  - [{item.get('kind')}] {item.get('path') or item.get('name')}{size_text}")
            if content.get("truncated"):
                lines.append("  - ... 已截断")
            return lines
        if result.name == "workspace.read_text_file":
            lines.append(f"- root: {content.get('root', '')}")
            lines.append(f"- path: {content.get('path')}")
            lines.append(f"- size: {content.get('size')} bytes")
            lines.append("- content:")
            lines.append("```")
            lines.append(str(content.get("content") or ""))
            lines.append("```")
            if content.get("truncated"):
                lines.append("- note: 内容已截断")
            return lines
        if result.name == "workspace.search_files":
            lines.append(f"- root: {content.get('root', '')}")
            lines.append(f"- path: {content.get('path') or '.'}")
            lines.append(f"- query: {content.get('query')}")
            lines.append(f"- scanned: {content.get('scanned')}")
            lines.append("- results:")
            for item in content.get("results", [])[:80]:
                location = f":{item.get('line')}" if item.get("line") else ""
                snippet = f" -- {item.get('snippet')}" if item.get("snippet") else ""
                lines.append(f"  - {item.get('path')}{location} [{item.get('match')}]{snippet}")
            if content.get("truncated"):
                lines.append("  - ... 已截断")
            return lines
        if result.name == "git.status":
            lines.append(f"- root: {content.get('root', '')}")
            lines.append(f"- branch: {content.get('branch')}")
            lines.append(f"- clean: {content.get('clean')}")
            if content.get("entries"):
                lines.append("- entries:")
                lines.extend(f"  - {entry}" for entry in content.get("entries", [])[:120])
            if content.get("truncated"):
                lines.append("  - ... 已截断")
            return lines
        for key, value in content.items():
            lines.append(f"- {key}: {value}")
        return lines

    def _model_config(self, path: str) -> ModelCallConfig:
        if path == "deliberate":
            return ModelCallConfig(role="chat_work", max_tokens=1400, temperature=0.55, timeout=30)
        if path == "safety":
            return ModelCallConfig(role="safety_reflector", max_tokens=1200, temperature=0.7, timeout=30)
        return ModelCallConfig(role="chat_light", max_tokens=1200, temperature=0.7, timeout=30)
