from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from selfecho_agent.context_budget import ContextBudget
from selfecho_agent.core import AgentRequest, ExecutionPlan, ToolCallPlan
from selfecho_agent.evaluator import HarnessEvaluator
from selfecho_agent.modes import IntentRouter
from selfecho_agent.planner import DeterministicPlanner
from selfecho_agent.policy import PolicyEngine
from selfecho_agent.tools import ToolOrchestrator, ToolRegistry, WorkspaceTools


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
    results = evaluator.run()
    print(json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
