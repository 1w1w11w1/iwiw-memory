from __future__ import annotations

from pathlib import Path

from memory_agent.store import list_memories, read_memory

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


def write_prompt(name: str, content: str) -> str:
    if name not in ALLOWED_PROMPTS:
        raise ValueError("Unknown prompt")
    PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    path = PROMPT_DIR / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return content


def preview_prompt(user_message: str = "") -> str:
    sections = ["# IwIw Prompt 预览", ""]
    sections.append("## 对话策略")
    sections.append(read_prompt("conversation_reply"))
    sections.append("## L0/L1 长期记忆")
    for priority in ("core", "important"):
        for mem in list_memories(priority=priority):
            body = read_memory(mem["slug"]) or ""
            sections.append(f"### {mem['slug']} ({priority})")
            sections.append(body[:1600])
    if user_message:
        sections.append("## 当前用户消息")
        sections.append(user_message)
    return "\n\n".join(sections)
