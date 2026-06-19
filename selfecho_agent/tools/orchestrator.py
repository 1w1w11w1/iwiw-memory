from __future__ import annotations

from ..core import ExecutionPlan, ToolCallPlan, ToolResult
from .registry import ToolRegistry


class ToolOrchestrator:
    """Executes approved tool calls.

    Phase two only allows read-only tools. Write tools will enter here later
    through approval, backup, execution, and rollback refs.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def execute_plan(self, session_id: str, plan: ExecutionPlan) -> list[ToolResult]:
        results: list[ToolResult] = []
        for call in plan.tool_calls:
            results.append(self.execute_call(session_id, call))
        return results

    def execute_call(self, session_id: str, call: ToolCallPlan) -> ToolResult:
        spec = self.registry.get(call.name)
        if spec is None:
            return ToolResult(
                name=call.name,
                ok=False,
                summary=f"工具未注册：{call.name}",
                error="tool not registered",
                risk_level=call.risk_level,
                root_cause_hint="planner requested a tool that is not present in ToolRegistry",
                safe_retry=False,
                next_actions=["检查工具注册表", "改用已注册的只读工具或直接说明无法执行"],
                stop_condition="工具注册前不要重复调用同名工具。",
            )
        if spec.required_permission != "read_only":
            return ToolResult(
                name=call.name,
                ok=False,
                summary=f"工具 {call.name} 需要 {spec.required_permission} 权限，当前阶段未执行。",
                error="permission not granted",
                risk_level=spec.risk_level,
                root_cause_hint="current phase only allows read-only tool execution",
                safe_retry=False,
                next_actions=["向用户说明需要更高权限", "等待显式确认后再进入 guided/workspace 权限阶段"],
                stop_condition="没有用户确认或权限升级时停止执行。",
            )
        try:
            return spec.handler(session_id, call.arguments)
        except Exception as exc:
            return ToolResult(
                name=call.name,
                ok=False,
                summary=f"工具 {call.name} 执行失败：{exc}",
                error=str(exc),
                risk_level=spec.risk_level,
                root_cause_hint="tool handler raised an exception",
                safe_retry=True,
                next_actions=["检查工具参数和工作目录绑定", "修正输入后可以安全重试只读工具"],
                stop_condition="同一参数连续失败时停止重试并向用户说明原因。",
            )
