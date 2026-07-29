from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from selfecho_agent.context_budget import ContextBudget
from selfecho_agent.context import ContextBundle
from selfecho_agent.core import AgentRequest, ExecutionPlan, ToolCallPlan
from selfecho_agent.core.orchestrator import AgentOrchestrator
from selfecho_agent.evaluator import HarnessEvaluator
from selfecho_agent.modes import IntentRouter
from selfecho_agent.planner import DeterministicPlanner
from selfecho_agent.policy import PolicyEngine
from selfecho_agent.tools import ToolOrchestrator, ToolRegistry, WorkspaceTools
from selfecho_model import ModelCallConfig, ModelGateway
from selfecho_model.provider import ProviderSettings


class FakeSessionService:
    def __init__(self, root: Path) -> None:
        self.root = root

    def get_session(self, session_id: str) -> dict:
        return {"id": session_id, "scope": "project", "project_id": "project-1", "rolling_summary": ""}

    def get_project(self, project_id: str) -> dict:
        return {"id": project_id, "name": "eval-project", "path": str(self.root)}

    def recent_context(self, session_id: str, limit: int = 8) -> str:
        return ""


def test_plain_chat_does_not_plan_workspace_tool() -> None:
    router = IntentRouter()
    planner = DeterministicPlanner()
    policy = PolicyEngine()
    request = AgentRequest(session_id="s1", message="今天有点焦虑，陪我想想")
    decision = router.classify(request.message)
    plan = planner.plan(request, decision, policy.assess(request.message, decision))
    assert decision.path == "light", f"expected light path, got {decision.path}"
    assert plan.tool_calls == [], f"plain support chat should not call tools: {plan.tool_calls}"


def test_workspace_directory_uses_read_only_tool() -> None:
    router = IntentRouter()
    planner = DeterministicPlanner()
    policy = PolicyEngine()
    request = AgentRequest(session_id="s1", message="这个目录下有哪些文件")
    decision = router.classify(request.message)
    plan = planner.plan(request, decision, policy.assess(request.message, decision))
    names = [call.name for call in plan.tool_calls]
    assert "workspace.list_directory" in names, f"expected workspace.list_directory, got {names}"


def test_local_search_is_read_only_not_network_risk() -> None:
    router = IntentRouter()
    planner = DeterministicPlanner()
    policy = PolicyEngine()
    request = AgentRequest(session_id="s1", message="在项目里搜索 AgentOrchestrator")
    decision = router.classify(request.message)
    risk = policy.assess(request.message, decision)
    plan = planner.plan(request, decision, risk)
    names = [call.name for call in plan.tool_calls]
    assert risk.risk_level == "low", f"local search should be low risk, got {risk.to_dict()}"
    assert "workspace.search_files" in names, f"expected workspace.search_files, got {names}"


def test_write_request_requires_confirmation() -> None:
    router = IntentRouter()
    policy = PolicyEngine()
    message = "删除这个文件并保存修改"
    decision = router.classify(message)
    risk = policy.assess(message, decision)
    assert risk.requires_confirmation, f"write/delete should require confirmation: {risk.to_dict()}"
    assert risk.rollback_required, f"write/delete should require rollback planning: {risk.to_dict()}"


def test_tool_failure_observation_contract() -> None:
    service = FakeSessionService(Path(".").resolve())
    registry = ToolRegistry(service)  # type: ignore[arg-type]
    orchestrator = ToolOrchestrator(registry)
    plan = ExecutionPlan(
        goal="eval",
        mode="deliberate",
        tool_calls=[ToolCallPlan(name="missing.tool", arguments={})],
    )
    result = orchestrator.execute_plan("s1", plan)[0].to_dict()
    assert result["status"] == "failed", f"missing tool should fail: {result}"
    assert result["root_cause_hint"], f"failure should include root cause: {result}"
    assert result["next_actions"], f"failure should include recovery actions: {result}"
    assert result["stop_condition"], f"failure should include stop condition: {result}"


def test_workspace_search_reads_bound_project_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "notes.md").write_text("AgentOrchestrator keeps the run trace visible.\n", encoding="utf-8")
        service = FakeSessionService(root)
        registry = ToolRegistry(service)  # type: ignore[arg-type]
        WorkspaceTools(service).register(registry)  # type: ignore[arg-type]
        orchestrator = ToolOrchestrator(registry)
        result = orchestrator.execute_call(
            "s1",
            ToolCallPlan(name="workspace.search_files", arguments={"query": "AgentOrchestrator"}),
        )
        data = result.to_dict()
        assert data["status"] == "ok", f"search should succeed: {data}"
        assert data["content"]["results"], f"search should return matches: {data}"
        assert data["content"]["results"][0]["path"] == "notes.md", f"unexpected match path: {data}"


def test_git_status_failure_has_recovery_contract() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        service = FakeSessionService(root)
        registry = ToolRegistry(service)  # type: ignore[arg-type]
        WorkspaceTools(service).register(registry)  # type: ignore[arg-type]
        orchestrator = ToolOrchestrator(registry)
        result = orchestrator.execute_call("s1", ToolCallPlan(name="git.status", arguments={})).to_dict()
        if result["status"] == "failed":
            assert result["root_cause_hint"], f"git failure should include root cause: {result}"
            assert result["stop_condition"], f"git failure should include stop condition: {result}"
        else:
            assert "clean" in result["content"], f"git success should include clean flag: {result}"


def test_context_budget_reports_sources_and_warnings() -> None:
    report = ContextBudget().measure(
        [
            "## IwIw 身份与介绍\n你是 IwIw。",
            "## 当前工作目录\n- path: E:/repo",
            "## Harness 工具观察\n### workspace.search_files",
        ]
    ).to_dict()
    sources = [section["source"] for section in report["sections"]]
    assert "identity" in sources, f"identity source missing: {report}"
    assert "project_context" in sources, f"project context source missing: {report}"
    assert "tool_observation" in sources, f"tool observation source missing: {report}"
    assert report["estimated_tokens"] > 0, f"token estimate missing: {report}"


def test_partial_model_stream_is_not_retried_as_full_completion() -> None:
    class PartialFailureProvider:
        def __init__(self) -> None:
            self.complete_calls = 0

        async def stream(self, **kwargs):
            yield "partial"
            raise RuntimeError("stream interrupted")

        async def complete(self, **kwargs) -> str:
            self.complete_calls += 1
            return "complete"

    class StreamingGateway(ModelGateway):
        @staticmethod
        def _settings(model_role: str) -> ProviderSettings:
            return ProviderSettings(model="test", api_style="anthropic", base_url="", api_key="", streaming=True)

    provider = PartialFailureProvider()

    async def collect() -> tuple[list[str], str]:
        chunks: list[str] = []
        error = ""
        try:
            async for chunk in StreamingGateway(provider=provider).stream(  # type: ignore[arg-type]
                system_prompt="system",
                user_prompt="user",
                config=ModelCallConfig(role="chat", max_tokens=10, temperature=0, timeout=1),
            ):
                chunks.append(chunk)
        except RuntimeError as exc:
            error = str(exc)
        return chunks, error

    chunks, error = asyncio.run(collect())
    assert chunks == ["partial"] and error == "stream interrupted"
    assert provider.complete_calls == 0, "a partial stream must not be followed by a full duplicate response"


def test_sync_context_and_tools_do_not_block_event_loop() -> None:
    worker_threads: dict[str, int] = {}

    class RecordingContextBuilder:
        def build(self, *args, **kwargs) -> ContextBundle:
            worker_threads["context"] = threading.get_ident()
            return ContextBundle(system_prompt="system", sections=["system"], metadata={})

    class RecordingTools:
        def execute_plan(self, *args, **kwargs) -> list:
            worker_threads["tools"] = threading.get_ident()
            return []

    class StaticGateway:
        def streaming_enabled(self, model_role: str) -> bool:
            return True

        async def stream(self, **kwargs):
            yield "ok"

    class RecordingTrace:
        def start_run(self, *args, **kwargs) -> dict:
            return {"id": "run-1", "started_at": "now"}

        def step(self, *args, **kwargs) -> None:
            pass

        def finish(self, run_id: str, **kwargs) -> dict:
            return {"id": run_id, "started_at": "now", "ended_at": "later", **kwargs}

    orchestrator = AgentOrchestrator(
        FakeSessionService(Path(".").resolve()),  # type: ignore[arg-type]
        lambda name: "system",
        context_builder=RecordingContextBuilder(),  # type: ignore[arg-type]
        model_gateway=StaticGateway(),  # type: ignore[arg-type]
        tool_orchestrator=RecordingTools(),  # type: ignore[arg-type]
        trace_recorder=RecordingTrace(),  # type: ignore[arg-type]
    )

    async def collect() -> int:
        loop_thread = threading.get_ident()
        events = [event async for event in orchestrator.run_stream(
            AgentRequest(session_id="s1", message="hello")
        )]
        assert events[-1]["type"] == "done", events
        return loop_thread

    loop_thread = asyncio.run(collect())
    assert set(worker_threads) == {"context", "tools"}
    assert worker_threads["context"] != loop_thread
    assert worker_threads["tools"] != loop_thread


def test_cancelled_agent_stream_finishes_run() -> None:
    class StaticContextBuilder:
        def build(self, *args, **kwargs) -> ContextBundle:
            return ContextBundle(system_prompt="system", sections=["system"], metadata={})

    class EmptyToolOrchestrator:
        def execute_plan(self, *args, **kwargs) -> list:
            return []

    class BlockingGateway:
        def streaming_enabled(self, model_role: str) -> bool:
            return True

        async def stream(self, **kwargs):
            yield "partial"
            await asyncio.Event().wait()

    class RecordingTrace:
        def __init__(self) -> None:
            self.finished: dict | None = None
            self.steps: list[dict] = []

        def start_run(self, *args, **kwargs) -> dict:
            return {"id": "run-1", "started_at": "now"}

        def step(self, session_id: str, run_id: str, step) -> None:
            self.steps.append(step.to_dict())

        def finish(self, run_id: str, **kwargs) -> dict:
            self.finished = {"id": run_id, **kwargs, "started_at": "now", "ended_at": "later"}
            return self.finished

    trace = RecordingTrace()
    orchestrator = AgentOrchestrator(
        FakeSessionService(Path(".").resolve()),  # type: ignore[arg-type]
        lambda name: "system",
        context_builder=StaticContextBuilder(),  # type: ignore[arg-type]
        model_gateway=BlockingGateway(),  # type: ignore[arg-type]
        tool_orchestrator=EmptyToolOrchestrator(),  # type: ignore[arg-type]
        trace_recorder=trace,  # type: ignore[arg-type]
    )

    async def close_after_delta() -> None:
        stream = orchestrator.run_stream(AgentRequest(session_id="s1", message="hello"))
        async for event in stream:
            if event.get("type") == "delta":
                await stream.aclose()
                break

    asyncio.run(close_after_delta())
    assert trace.finished and trace.finished["status"] == "cancelled", trace.finished
    assert trace.finished["metadata"]["answer_chars"] == len("partial"), trace.finished
    assert trace.steps[-1]["status"] == "cancelled", trace.steps

    meta_trace = RecordingTrace()
    meta_orchestrator = AgentOrchestrator(
        FakeSessionService(Path(".").resolve()),  # type: ignore[arg-type]
        lambda name: "system",
        context_builder=StaticContextBuilder(),  # type: ignore[arg-type]
        model_gateway=BlockingGateway(),  # type: ignore[arg-type]
        tool_orchestrator=EmptyToolOrchestrator(),  # type: ignore[arg-type]
        trace_recorder=meta_trace,  # type: ignore[arg-type]
    )

    async def close_after_meta() -> None:
        stream = meta_orchestrator.run_stream(AgentRequest(session_id="s1", message="hello"))
        assert (await anext(stream))["type"] == "meta"
        await stream.aclose()

    asyncio.run(close_after_meta())
    assert meta_trace.finished and meta_trace.finished["status"] == "cancelled", meta_trace.finished
    assert meta_trace.finished["metadata"]["answer_chars"] == 0, meta_trace.finished


def test_cancelled_http_stream_persists_partial_assistant() -> None:
    import selfecho_api.server as server

    class RecordingSessionService:
        def __init__(self) -> None:
            self.messages: list[dict] = []

        def append_message(self, session_id: str, role: str, content: str, **kwargs) -> dict:
            message = {"id": len(self.messages) + 1, "session_id": session_id, "role": role,
                       "content": content, **kwargs}
            self.messages.append(message)
            return message

    class PartialRunner:
        async def respond_stream(self, session_id: str, message: str):
            yield {"type": "meta", "run_id": "run-1", "status": "running"}
            yield {"type": "delta", "text": "partial"}
            await asyncio.Event().wait()

    service = RecordingSessionService()
    original_service = server.session_service
    original_runner = server.agent_runner
    server.session_service = service  # type: ignore[assignment]
    server.agent_runner = PartialRunner()  # type: ignore[assignment]

    async def close_after_delta() -> None:
        response = await server.chat_message_stream(
            "s1",
            server.ChatMessageRequest(message="hello"),
        )
        iterator = response.body_iterator
        await anext(iterator)
        await anext(iterator)
        await iterator.aclose()

    try:
        asyncio.run(close_after_delta())
    finally:
        server.session_service = original_service
        server.agent_runner = original_runner

    assistant = [item for item in service.messages if item["role"] == "assistant"]
    assert len(assistant) == 1, service.messages
    assert assistant[0]["content"] == "partial", assistant
    assert assistant[0]["metadata"]["status"] == "cancelled", assistant
    assert assistant[0]["metadata"]["run_id"] == "run-1", assistant


def main() -> int:
    evaluator = HarnessEvaluator()
    evaluator.add("plain_chat_no_workspace_tool", test_plain_chat_does_not_plan_workspace_tool)
    evaluator.add("workspace_directory_tool", test_workspace_directory_uses_read_only_tool)
    evaluator.add("local_search_low_risk", test_local_search_is_read_only_not_network_risk)
    evaluator.add("write_request_requires_confirmation", test_write_request_requires_confirmation)
    evaluator.add("tool_failure_observation_contract", test_tool_failure_observation_contract)
    evaluator.add("workspace_search_bound_project", test_workspace_search_reads_bound_project_only)
    evaluator.add("git_status_recovery_contract", test_git_status_failure_has_recovery_contract)
    evaluator.add("context_budget_sources", test_context_budget_reports_sources_and_warnings)
    evaluator.add("partial_stream_no_duplicate_retry", test_partial_model_stream_is_not_retried_as_full_completion)
    evaluator.add("sync_work_off_event_loop", test_sync_context_and_tools_do_not_block_event_loop)
    evaluator.add("cancelled_stream_finishes_run", test_cancelled_agent_stream_finishes_run)
    evaluator.add("cancelled_http_stream_persists_partial", test_cancelled_http_stream_persists_partial_assistant)
    results = evaluator.run()
    print(json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
