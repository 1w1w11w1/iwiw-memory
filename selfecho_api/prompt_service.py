from __future__ import annotations

from pathlib import Path

from memory_agent.engine import MemoryEngine, default_memory_engine

ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIR = ROOT / "selfecho_config" / "prompts"
ALLOWED_PROMPTS = {
    "conversation_reply",
    "memory_consolidation",
    "memory_editing",
    "session_summary",
}


def list_prompts() -> list[dict]:
    PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in sorted(ALLOWED_PROMPTS):
        path = PROMPT_DIR / f"{name}.md"
        rows.append({
            "name": name,
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
        })
    return rows


def read_prompt(name: str) -> str:
    if name not in ALLOWED_PROMPTS:
        raise ValueError("Unknown prompt")
    path = PROMPT_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def prompt_path(name: str) -> Path:
    if name not in ALLOWED_PROMPTS:
        raise ValueError("Unknown prompt")
    return PROMPT_DIR / f"{name}.md"


def preview_prompt(
    user_message: str = "",
    context_messages: list[str] | None = None,
    memory_engine: MemoryEngine | None = None,
) -> str:
    """
    组装完整 system prompt，包含对话策略、长期倾向和事实检索结果。
    """
    sections = ["# IwIw Prompt 预览", ""]
    sections.append("## 对话策略")
    sections.append(read_prompt("conversation_reply"))

    memory = (memory_engine or default_memory_engine).build_context(
        user_message=user_message,
        context_messages=context_messages,
    )
    sections.extend(memory.sections)

    if user_message:
        sections.append("## 当前用户消息")
        sections.append(user_message)

    return "\n\n".join(sections)
