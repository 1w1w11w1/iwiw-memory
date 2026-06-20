from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memory_agent.db import list_memories, read_memory
from memory_agent.config import LLM_MODEL

from selfecho_session import SessionMemoryService

from .modes import IntentDecision


WORKSPACE_EXCLUDE_DIRS = {
    ".git",
    ".claude",
    ".codex",
    "__pycache__",
    "node_modules",
    "dist",
    ".venv",
    "venv",
}
WORKSPACE_EXCLUDE_NAMES = {
    ".env",
    "providers.local.json",
}
WORKSPACE_EXCLUDE_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pdf",
    ".mp3",
    ".wav",
    ".pyc",
    ".pyo",
    ".log",
}
APP_ROOT = Path(__file__).resolve().parents[1]
APP_PRIVATE_DIRS = {"memory", "selfecho_data"}


@dataclass(frozen=True)
class ContextBundle:
    system_prompt: str
    sections: list[str]
    metadata: dict[str, Any]


class ContextBuilder:
    def __init__(self, session_service: SessionMemoryService) -> None:
        self.session_service = session_service

    def build(
        self,
        session_id: str,
        decision: IntentDecision,
        base_prompt: str,
        user_message: str = "",
    ) -> ContextBundle:
        core_context, loaded_memory_slugs = self._core_important_context()
        sections = [
            self._identity_section(),
            self._mode_section(decision),
            base_prompt.strip(),
            core_context,
            self._related_memory_context(session_id, user_message, loaded_memory_slugs),
            self._project_context(session_id),
            self._session_context(session_id),
        ]
        clean_sections = [section for section in sections if section.strip()]
        metadata = {
            "intent": decision.intent,
            "path": decision.path,
            "reason": decision.reason,
        }
        return ContextBundle(
            system_prompt="\n\n".join(clean_sections),
            sections=clean_sections,
            metadata=metadata,
        )

    def _identity_section(self) -> str:
        return "\n".join([
            "## IwIw 身份与介绍",
            "你是 IwIw 内置智能体。",
            f"当前后端默认模型：{LLM_MODEL or '未配置'}。",
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

    def _core_important_context(self) -> tuple[str, set[str]]:
        parts: list[str] = []
        loaded_slugs: set[str] = set()
        for priority in ("core", "important"):
            for mem in list_memories(priority=priority):
                loaded_slugs.add(mem["slug"])
                body = read_memory(mem["slug"]) or ""
                parts.append(f"### {mem['slug']} ({priority})\n{body[:1800]}")
        if not parts:
            return "", loaded_slugs
        return "## L0/L1 长期记忆\n" + "\n\n".join(parts), loaded_slugs

    def _related_memory_context(
        self,
        session_id: str,
        user_message: str,
        loaded_slugs: set[str],
    ) -> str:
        if not user_message.strip():
            return ""
        try:
            from memory_agent.retrieval import format_memory_context, hybrid_search

            results = hybrid_search(
                user_message=user_message,
                context_messages=self._recent_message_texts(session_id),
                top_k=5,
                relevance_threshold=0.35,
                exclude_slugs=loaded_slugs,
            )
            return format_memory_context(results, max_total_chars=2000)
        except Exception:
            return ""

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
        snapshot = self._directory_snapshot(project_path)
        if snapshot:
            lines.append("")
            lines.append("### 目录快照（顶层）")
            lines.extend(snapshot)
        return "\n".join(lines)

    def _directory_snapshot(self, path: Path, limit: int = 40) -> list[str]:
        rows: list[tuple[str, str]] = []
        try:
            children = list(path.iterdir())
        except OSError as exc:
            return [f"- 无法读取目录：{exc}"]
        for child in children:
            try:
                if self._is_excluded(child, path):
                    continue
                marker = "dir" if child.is_dir() else "file"
                rows.append((child.name.lower(), f"- [{marker}] {child.name}"))
            except OSError:
                continue
        rows.sort(key=lambda item: item[0])
        items = [row for _, row in rows[:limit]]
        if len(rows) > limit:
            items.append(f"- ... 还有 {len(rows) - limit} 项未展示")
        return items

    def _is_excluded(self, path: Path, root: Path | None = None) -> bool:
        name = path.name
        if root and root.resolve() == APP_ROOT and name in APP_PRIVATE_DIRS:
            return True
        if name in WORKSPACE_EXCLUDE_NAMES:
            return True
        if path.is_dir() and name in WORKSPACE_EXCLUDE_DIRS:
            return True
        if path.is_file() and path.suffix.lower() in WORKSPACE_EXCLUDE_SUFFIXES:
            return True
        if os.name == "nt" and name.lower() in {"thumbs.db", "desktop.ini"}:
            return True
        return False

    def _session_context(self, session_id: str) -> str:
        return "## 当前会话上下文\n" + self.session_service.recent_context(session_id)
