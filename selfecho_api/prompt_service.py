from __future__ import annotations

from pathlib import Path

from memory_agent.db import list_memories, get_memory
from memory_agent.retrieval import hybrid_search, format_memory_context

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


def preview_prompt(user_message: str = "", context_messages: list[str] | None = None) -> str:
    """
    组装完整 system prompt，包含对话策略 + L0/L1 始终加载 + L2/L3 语义检索注入。

    L2/L3 不全文加载，通过 hybrid_search 按用户消息检索相关结果，
    减少上下文噪声，避免 LLM 幻觉。
    """
    sections = ["# IwIw Prompt 预览", ""]
    sections.append("## 对话策略")
    sections.append(read_prompt("conversation_reply"))

    # ── L0/L1 始终加载 ──
    sections.append("## 长期记忆（始终加载）")
    l0_l1_slugs: set[str] = set()
    for priority in ("core", "important"):
        for mem in list_memories(priority=priority):
            l0_l1_slugs.add(mem["slug"])
            body = get_memory(mem["slug"])
            content = (body.get("content", "") if body else "")[:1600]
            sections.append(f"### {mem['slug']} ({priority})")
            sections.append(content)

    # ── L2/L3 按需检索 ──
    if user_message:
        retrieved = hybrid_search(
            user_message=user_message,
            context_messages=context_messages,
            top_k=5,
            relevance_threshold=0.35,
            exclude_slugs=l0_l1_slugs,
        )
        context_block = format_memory_context(retrieved, max_total_chars=2000)
        if context_block:
            sections.append(context_block)

    if user_message:
        sections.append("## 当前用户消息")
        sections.append(user_message)

    return "\n\n".join(sections)
