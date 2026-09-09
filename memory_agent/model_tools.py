"""模型记忆工具：chat（OpenAI function calling）与 MCP 面共享的执行逻辑。

四个工具：memory_remember / memory_search / memory_read / memory_list。
写入准则由工具描述文本（TOOL_GUIDES）承载，chat 与 MCP 两端一致；
执行逻辑单源（execute_memory_tool），两端行为不会漂移。
"""

from __future__ import annotations

import hashlib
from typing import Any

from .db import get_memory, list_memories, upsert_memory, touch_memories
from .retrieval import search_memories

REMEMBER_GUIDE = (
    "把值得长期保存的稳定信息写入记忆，按内容类型选 level："
    "profile=用户身份画像（健康、偏好、关系、长期计划，默认模式将全量注入每轮）；"
    "fact=客观事实（默认）；lesson=教训与经验；"
    "rules=用户要求 AI 持续遵守的准则（全量注入每轮）；"
    "project=项目脉络与决策。"
    "写入即全文替换：带 slug 为更新该条，不带 slug 为新建"
    "（新建前先用 memory_search 查重，已存在近似条目则带其 slug 更新）。"
    "只记稳定信息与用户明确要求记住的内容；一次性、临时话题不要写。"
    "用户最新表述优先。"
)

TOOL_GUIDES: dict[str, str] = {
    "memory_remember": REMEMBER_GUIDE,
    "memory_search": "按关键词检索长期记忆（FTS 词面 + 同义词 + 会话联想）。",
    "memory_read": "读取一条记忆的完整正文。",
    "memory_list": "列出记忆库条目（按 priority/mem_type 过滤，最多 50 条）。",
}

# OpenAI function calling schema（chat 用；MCP 端按 TOOL_GUIDES 自行定义 Tool）
MEMORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "memory_remember",
            "description": REMEMBER_GUIDE,
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "一句话描述（如：用户对芒果过敏）"},
                    "body": {"type": "string", "description": "完整正文（整体替换旧内容，不是追加）"},
                    "level": {"type": "string", "enum": ["profile", "fact", "lesson", "rules", "project"], "description": "记忆类型：profile=用户身份画像/健康/偏好（默认模式下将全量注入每轮，身份健康类默认选这个）；fact=一般事实（默认）；lesson=教训与经验；rules=用户要求持续遵守的准则（将全量注入每轮）；project=项目脉络与决策"},
                    "slug": {"type": "string", "description": "可选。更新已有记忆时填其 slug；新建建议用简短英文连字符命名（如 user-mango-allergy），不填则自动生成"},
                    "priority": {"type": "string", "enum": ["active", "archived"], "description": "可选，默认 active（在役）；archived=归档退役（一般不手动用）"},
                },
                "required": ["description", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": TOOL_GUIDES["memory_search"],
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词"},
                    "top_k": {"type": "integer", "description": "返回条数，默认 5"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_read",
            "description": TOOL_GUIDES["memory_read"],
            "parameters": {
                "type": "object",
                "properties": {"slug": {"type": "string", "description": "记忆 slug"}},
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_list",
            "description": TOOL_GUIDES["memory_list"],
            "parameters": {
                "type": "object",
                "properties": {
                    "priority": {"type": "string", "enum": ["active", "archived"], "description": "可选过滤"},
                    "mem_type": {"type": "string", "enum": ["profile", "fact", "lesson", "rules", "project"], "description": "可选类型过滤"},
                    "limit": {"type": "integer", "description": "条数上限，默认 20"},
                },
            },
        },
    },
]


def _auto_slug(description: str) -> str:
    """description 无可用 slug 时的确定性回退命名。"""
    return "mem-" + hashlib.md5(description.strip().encode("utf-8")).hexdigest()[:8]


def execute_memory_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """执行一个模型记忆工具调用。

    返回 {"result": 工具结果（JSON 可序列化）, "echo": 人类可读摘要}。
    执行失败不抛出：工具错误以 {"ok": False, "error": ...} 回传给模型自行处理。
    """
    name = (name or "").strip()
    args = args if isinstance(args, dict) else {}
    try:
        if name == "memory_remember":
            description = (args.get("description") or "").strip()
            body = (args.get("body") or "").strip()
            if not description or not body:
                raise ValueError("description 与 body 不能为空")
            slug = (args.get("slug") or "").strip().lower() or _auto_slug(description)
            level = args.get("level") if args.get("level") in ("profile", "fact", "lesson", "rules", "project") else "fact"
            priority = args.get("priority") if args.get("priority") in ("active", "archived") else "active"
            related: list[dict[str, str]] = []
            if not args.get("slug"):
                hits = search_memories(description, top_k=3)
                related = [{"slug": h["slug"], "description": h.get("description", "")} for h in hits]
            row = upsert_memory(
                slug=slug, description=description, content=body,
                mem_type=level, priority=priority,
            )
            result = {"ok": True, "slug": row["slug"], "level": row.get("mem_type", level), "related": related}
            echo = f"remember → {row['slug']} [{row.get('mem_type', '')}/{row.get('priority', '')}]" + (f"（近似: {', '.join(r['slug'] for r in related)}）" if related else "")
        elif name == "memory_search":
            query = (args.get("query") or "").strip()
            hits = search_memories(query, top_k=max(1, min(int(args.get("top_k", 5)), 10)))
            result = {
                "ok": True,
                "hits": [
                    {"slug": h["slug"], "description": h.get("description", ""),
                     "priority": h.get("priority", "active"), "content": (h.get("content") or "")[:200]}
                    for h in hits
                ],
            }
            echo = f"search '{query}' → {len(hits)} 条"
        elif name == "memory_read":
            slug = (args.get("slug") or "").strip()
            mem = get_memory(slug)
            if not mem:
                result = {"ok": False, "error": f"未找到记忆: {slug}"}
                echo = f"read {slug} ✗"
            else:
                result = {"ok": True, "memory": mem}
                echo = f"read {slug} ✓"
                touch_memories([slug])
        elif name == "memory_list":
            priority = args.get("priority") if args.get("priority") in ("active", "archived") else None
            mem_type = args.get("mem_type") if args.get("mem_type") in ("profile", "fact", "lesson", "rules", "project") else None
            mems = list_memories(priority=priority, mem_type=mem_type, limit=max(1, min(int(args.get("limit", 20)), 50)))
            result = {
                "ok": True,
                "items": [
                    {"slug": m["slug"], "description": m.get("description", ""),
                     "priority": m.get("priority", "active"), "mem_type": m.get("mem_type", "profile")}
                    for m in mems
                ],
            }
            echo = f"list → {len(mems)} 条"
        else:
            result = {"ok": False, "error": f"未知工具: {name}"}
            echo = f"未知工具 {name}"
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
        echo = f"{name} 失败: {exc}"
    return {"result": result, "echo": echo}
