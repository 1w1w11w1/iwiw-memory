"""
maintenance.py — 记忆维护：审查现有记忆，生成待确认的归档动作。

背景：旧会话层的「周期整理」（_maintain_memories_sync）已随会话层删除。
独立记忆系统下，维护改为按需触发：chat 的 /maintain 命令或 MCP 的
maintenance_review 工具调用。审查结论以 pending action 落库，
由用户（/pending approve / reject）最终确认，不自动执行。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .db import get_maintenance_candidates, create_pending_action
from .llm import complete_text

logger = logging.getLogger("memory_agent.maintenance")

MAINTENANCE_SYSTEM_PROMPT = """你是一个记忆维护器。审查用户的长期记忆，只对明确过时或冗余的记忆提议归档（archive）。

规则：
- 保守为主：不确定就 keep，不要过度归档。
- 不做合并、不修改内容，只判断是否需要归档。
- 可归档：已完成的短期计划、明显过时的偏好、与其他记忆重复的信息。
- 不要动：身份、健康、关系、长期计划、最近更新的记忆。

输出格式：JSON 数组，每项 {"action": "archive|keep", "slug": "记忆slug", "reason": "一句话原因"}。没有需要归档的则返回 []。"""


async def review_maintenance(limit: int = 20, timeout: float = 20.0) -> dict[str, Any]:
    """
    审查维护候选并生成 pending actions（不自动执行）。

    Returns:
        {"reviewed": int, "pending": [{id, slug, action, reason}], "error": str | None}
    """
    candidates = get_maintenance_candidates(limit=limit)
    if not candidates:
        return {"reviewed": 0, "pending": [], "error": None}

    catalog = "\n".join(
        "- {} ({}) -- {}".format(m["slug"], m.get("priority", "active"), m.get("description", ""))
        for m in candidates[:15]
    )

    try:
        text = await complete_text(
            system_prompt=MAINTENANCE_SYSTEM_PROMPT,
            user_prompt=f"## 记忆候选目录\n{catalog}\n\n返回JSON列表。",
            max_tokens=800,
            temperature=0.1,
            timeout=timeout,
        )
    except Exception as exc:
        logger.warning("maintenance review failed: %s", exc)
        return {"reviewed": len(candidates), "pending": [], "error": str(exc)}

    match = re.search(r"\[[\s\S]*?\]", text)
    if not match:
        return {"reviewed": len(candidates), "pending": [], "error": "unparseable LLM output"}
    try:
        actions = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return {"reviewed": len(candidates), "pending": [], "error": f"bad JSON: {exc}"}

    pending: list[dict[str, str]] = []
    for act in actions or []:
        if not isinstance(act, dict) or act.get("action") != "archive":
            continue
        slug = str(act.get("slug") or "").strip()
        if not slug:
            continue
        item = create_pending_action(
            action="archive",
            target_memory_slug=slug,
            reason=str(act.get("reason") or "maintenance review")[:200],
        )
        if item:
            pending.append({"id": item["id"], "slug": slug, "action": "archive", "reason": item["reason"]})

    return {"reviewed": len(candidates), "pending": pending, "error": None}
