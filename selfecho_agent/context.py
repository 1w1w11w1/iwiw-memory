from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memory_agent.engine import MemoryEngine, default_memory_engine
from memory_agent.scopes import ActiveMemoryScope

from selfecho_session import SessionMemoryService

from .modes import IntentDecision


APP_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ContextBundle:
    system_prompt: str
    sections: list[str]
    metadata: dict[str, Any]


class ContextBuilder:
    def __init__(
        self,
        session_service: SessionMemoryService,
        memory_engine: MemoryEngine | None = None,
    ) -> None:
        self.session_service = session_service
        self.memory_engine = memory_engine or default_memory_engine

    def build(
        self,
        session_id: str,
        decision: IntentDecision,
        base_prompt: str,
        user_message: str = "",
    ) -> ContextBundle:
        active_scope = self._active_memory_scope(session_id)
        memory_context = self.memory_engine.build_context(
            user_message=user_message,
            context_messages=self._recent_message_texts(session_id),
            active_scope=active_scope,
        )
        sections = [
            self._identity_section(),
            self._mode_section(decision),
            base_prompt.strip(),
            *memory_context.sections,
            self._project_context(session_id),
            self._session_context(session_id),
        ]
        clean_sections = [section for section in sections if section.strip()]
        trace = memory_context.trace
        metadata = {
            "intent": decision.intent,
            "path": decision.path,
            "reason": decision.reason,
            "workspace_id": active_scope.workspace_id,
            "project_id": active_scope.project_id,
            "memory_available": bool(trace.get("memory_available", True)),
            "memory_error": trace.get("error", ""),
            "tendency_profile_ids": sorted(trace.get("tendency_profile_ids", [])),
            "retrieved_record_ids": trace.get("retrieved_record_ids", []),
            "memory_budget": trace.get("budget", {}),
        }
        return ContextBundle(
            system_prompt="\n\n".join(clean_sections),
            sections=clean_sections,
            metadata=metadata,
        )

    def _active_memory_scope(self, session_id: str) -> ActiveMemoryScope:
        try:
            session = self.session_service.get_session(session_id)
        except Exception:
            return ActiveMemoryScope.for_session(session_id=session_id)
        if not session or session.get("scope") != "project":
            return ActiveMemoryScope.for_session(session_id=session_id)
        project_id = str(session.get("project_id") or "").strip()
        project = self.session_service.get_project(project_id) if project_id else None
        workspace_root = str(project.get("path") or "").strip() if project else None
        return ActiveMemoryScope.for_session(
            session_id=session_id,
            project_id=project_id or None,
            workspace_root=workspace_root or None,
        )

    def _identity_section(self) -> str:
        return "\n".join([
            "## IwIw 身份与介绍",
            "你是 IwIw 内置智能体。",
            "当前后端模型由系统配置决定，可通过 selfecho_config 查看。",
            "当用户询问身份、模型或功能时，简短说明：IwIw 可以对话、整理记忆、处理问题，并协助推进工作流。",
            "旧工具来源、本地路径和内部实现不属于常规介绍内容，除非它们和用户当前问题直接相关。",
        ])

    def _mode_section(self, decision: IntentDecision) -> str:
        guidance = {
            "light": "本轮采用轻量路径：优先自然回应、温和整理，不主动承诺执行外部动作。",
            "deliberate": "本轮采用审慎路径：先定位目标和上下文，必要时给出计划；执行、写入、删除等动作必须等待明确批准。",
            "safety": "本轮采用安全路径：先接住情绪和降低压力，再判断是否需要现实支持或进一步行动。",
        }.get(decision.path, "本轮采用默认路径。")
        return "\n".join([
            "## 本轮运行决策",
            f"- intent: {decision.intent}",
            f"- path: {decision.path}",
            f"- reason: {decision.reason}",
            f"- guidance: {guidance}",
        ])

    def _recent_message_texts(self, session_id: str, limit: int = 6) -> list[str]:
        if not hasattr(self.session_service, "get_messages"):
            return []
        try:
            messages = self.session_service.get_messages(session_id, limit=limit)
        except Exception:
            return []
        texts: list[str] = []
        for message in messages:
            content = str(message.get("content") or "").strip()
            if content:
                texts.append(content)
        return texts

    def _project_context(self, session_id: str) -> str:
        session = self.session_service.get_session(session_id)
        if not session:
            return "## 当前工作目录\n当前会话尚未绑定可用会话记录。"
        project = self.session_service.get_project(str(session.get("project_id") or ""))
        if not project or not str(project.get("path") or "").strip():
            if session.get("scope") == "chat":
                return "## 当前工作目录\n当前是普通对话，没有绑定项目工作目录。不要声称能看到某个项目文件。"
            return "## 当前工作目录\n当前项目没有绑定工作目录。"

        raw_path = str(project.get("path") or "")
        try:
            project_path = Path(raw_path).expanduser().resolve()
        except OSError:
            return f"## 当前工作目录\n项目 `{project.get('name')}` 的路径不可解析：{raw_path}"

        lines = [
            "## 当前工作目录",
            f"- project: {project.get('name') or '未命名项目'}",
            f"- path: {project_path}",
        ]
        if not project_path.exists() or not project_path.is_dir():
            lines.append("- status: 路径不存在或不是文件夹。回答时必须说明这一点。")
            return "\n".join(lines)

        lines.append("- status: 已绑定到该本地文件夹。回答项目可见范围时应以此路径为准。")
        return "\n".join(lines)

    def _session_context(self, session_id: str) -> str:
        return "## 当前会话上下文\n" + self.session_service.recent_context(session_id)
