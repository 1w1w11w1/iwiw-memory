from __future__ import annotations

import re

from .core import AgentRequest, ExecutionPlan, ToolCallPlan
from .modes import IntentDecision
from .policy import RiskPolicy


class DeterministicPlanner:
    """Small phase-two planner for deciding safe observations.

    This layer intentionally starts deterministic. LLM planning can be added
    later, but the harness should first have a reliable shape and traceable
    tool calls.
    """

    DIRECTORY_TERMS = ("工作目录", "项目目录", "这个目录", "目录下", "文件夹", "能看到什么", "有哪些文件")
    READ_TERMS = ("读取", "打开", "查看", "看一下")
    SEARCH_TERMS = ("搜索", "查找", "找一下", "找找", "grep", "rg")
    GIT_STATUS_TERMS = ("git status", "git状态", "git 状态", "仓库状态", "分支", "未提交", "改动状态")
    FILE_PATTERN = re.compile(r"([A-Za-z0-9_\-./\\\u4e00-\u9fff]+\.[A-Za-z0-9]{1,12})")

    def plan(self, request: AgentRequest, decision: IntentDecision, policy: RiskPolicy) -> ExecutionPlan:
        text = request.message.strip()
        tool_calls: list[ToolCallPlan] = []
        steps = [
            "理解用户本轮目标",
            "检查是否需要真实工作区观察",
            "结合观察结果给出回答或下一步计划",
        ]

        if self._needs_directory_observation(text, decision):
            tool_calls.append(
                ToolCallPlan(
                    name="workspace.list_directory",
                    arguments={"path": "", "limit": 80},
                    reason="用户正在询问当前工作目录或项目目录可见内容",
                )
            )

        search_query = self._requested_search(text, decision)
        if search_query:
            tool_calls.append(
                ToolCallPlan(
                    name="workspace.search_files",
                    arguments={"query": search_query, "path": "", "limit": 40},
                    reason="用户请求在当前工作目录中搜索文件或内容",
                )
            )

        file_path = self._requested_file(text)
        if file_path:
            tool_calls.append(
                ToolCallPlan(
                    name="workspace.read_text_file",
                    arguments={"path": file_path},
                    reason="用户请求查看某个文本文件内容",
                )
            )

        if self._needs_git_status(text, decision):
            tool_calls.append(
                ToolCallPlan(
                    name="git.status",
                    arguments={},
                    reason="用户请求查看当前仓库的 git 状态",
                )
            )

        if decision.intent == "planning" and not tool_calls:
            steps.extend(["列出关键假设", "指出缺口并给出澄清问题", "给出可执行推进顺序"])

        return ExecutionPlan(
            goal=self._goal(text, decision),
            mode=decision.path,
            steps=steps,
            tool_calls=tool_calls,
            requires_clarification=policy.requires_confirmation and not tool_calls,
            clarifying_questions=self._clarifying_questions(text, decision, policy),
        )

    def _needs_directory_observation(self, text: str, decision: IntentDecision) -> bool:
        if decision.intent != "execution":
            return False
        return any(term in text for term in self.DIRECTORY_TERMS)

    def _requested_file(self, text: str) -> str:
        if not any(term in text for term in self.READ_TERMS):
            return ""
        match = self.FILE_PATTERN.search(text)
        return match.group(1).strip("`'\"，。；：") if match else ""

    def _requested_search(self, text: str, decision: IntentDecision) -> str:
        if decision.intent != "execution":
            return ""
        if not any(term in text for term in self.SEARCH_TERMS):
            return ""
        for term in self.SEARCH_TERMS:
            index = text.find(term)
            if index < 0:
                continue
            tail = text[index + len(term):].strip(" ：:，,`'\"“”")
            if not tail:
                continue
            query = re.split(r"[，。；;\n]", tail, maxsplit=1)[0]
            query = query.strip("`'\"“” ")
            for suffix in ("相关文件", "文件", "内容", "在哪", "在哪里"):
                if query.endswith(suffix) and len(query) > len(suffix):
                    query = query[: -len(suffix)].strip()
            return query[:80]
        return ""

    def _needs_git_status(self, text: str, decision: IntentDecision) -> bool:
        if decision.intent != "execution":
            return False
        lowered = text.lower()
        return any(term in lowered for term in self.GIT_STATUS_TERMS)

    def _goal(self, text: str, decision: IntentDecision) -> str:
        if decision.intent == "execution":
            return "基于当前会话绑定的工作目录，给出可靠的项目观察与回答。"
        if decision.intent == "planning":
            return "把用户的问题拆解为可执行的工作蓝图。"
        if decision.intent == "memory":
            return "处理记忆、上下文整理或长期信息维护请求。"
        if decision.intent == "safety":
            return "先降低情绪压力并提供现实可行的支持。"
        return "自然回应用户，并在需要时轻量澄清。"

    def _clarifying_questions(self, text: str, decision: IntentDecision, policy: RiskPolicy) -> list[str]:
        if decision.intent == "planning":
            return ["你希望我先给总体蓝图，还是先把第一步落成可执行任务？"]
        if policy.requires_confirmation:
            return ["这个请求可能涉及写入、联网或高风险操作。你希望我先只做只读分析吗？"]
        return []
