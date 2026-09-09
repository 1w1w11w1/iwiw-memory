"""
mcp_server.py — 记忆系统 MCP 服务器（DSH 接入桥）

提供工具：
- memory_remember  : 模型写入记忆（upsert 全文替换；查重提示随结果返回）
- search_memories  : 确定性搜索（FTS5 词面 + 会话状态联想 + 时间衰减；支持 session_id 回指 / context 上下文 / exclude_mem_types 排除）
- list_memories    : 列出记忆（按 priority/type 过滤）
- read_memory      : 读取单条记忆
- memory_stats     : 记忆库统计
- touch_memories   : 命中自增（使用强化，供维护排序）
- memory_update    : 全文替换记忆（带版本 + 审计）
- memory_archive   : 归档记忆
- memory_delete    : 删除记忆（带版本 + 审计）
- memory_merge     : 合并记忆
- memory_history   : 版本历史
- memory_rollback  : 从版本回滚
- pending_actions  : 待确认动作列表
- pending_approve  : 审批通过
- pending_reject   : 审批拒绝
- maintenance_review : 维护候选审查（生成归档待确认动作）
- run_consolidate  : 空闲巩固（低风险修正自动留痕执行，归档走审批）


启动：python -m memory_agent.mcp_server
"""

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .db import (
    get_memory,
    list_memories,
    read_memory,
    replace_memory_result,
    archive_memory_result,
    delete_memory_result,
    merge_memories_result,
    list_history,
    restore_memory_from_history_result,
    list_pending_actions,
    approve_pending_action_result,
    reject_pending_action,
    get_stats,
    touch_memories,
)
from .retrieval import search_memories
from .model_tools import TOOL_GUIDES, execute_memory_tool

server = Server("memory-system")

# ── per-session 会话状态（插件回指联想；CLI 的 SessionState 在 chat 进程内）──
# MCP 子进程长驻、多会话共享，按 session_id 隔离；TTL + 容量上限防泄漏。
_SESSION_TTL_HOURS = 24.0
_SESSION_MAX = 100
_session_registry: dict[str, dict[str, Any]] = {}


def _session_entry(sid: str) -> dict[str, Any]:
    from datetime import datetime, timedelta

    from .session_state import SessionState

    now = datetime.now()
    cutoff = (now - timedelta(hours=_SESSION_TTL_HOURS)).isoformat(timespec="seconds")
    for k in [k for k, v in _session_registry.items() if v["last_seen"] < cutoff]:
        _session_registry.pop(k, None)
    if len(_session_registry) > _SESSION_MAX:
        oldest = sorted(_session_registry, key=lambda k: _session_registry[k]["last_seen"])
        for k in oldest[: len(oldest) - _SESSION_MAX]:
            _session_registry.pop(k, None)
    entry = _session_registry.get(sid)
    if entry is None:
        entry = {"state": SessionState(), "topic_freq": {}, "last_seen": ""}
        _session_registry[sid] = entry
    entry["last_seen"] = now.isoformat(timespec="seconds")
    # 每次重建词面索引：会话中新写入的记忆立即生效（与 CLI 的 A2 一致性同款）
    entry["state"].build_word_index(list_memories())
    return entry


def _tool(name: str, description: str, props: dict[str, Any], required: list[str] | None = None) -> Tool:
    return Tool(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": props,
            **({"required": required} if required else {}),
        },
    )


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        _tool(
            "memory_remember",
            TOOL_GUIDES["memory_remember"],
            {
                "description": {"type": "string", "description": "一句话描述"},
                "body": {"type": "string", "description": "完整正文（整体替换，不是追加）"},
                "level": {"type": "string", "description": "profile|fact|lesson|rules|project，默认 fact；profile 与 rules 类将全量注入每轮"},
                "slug": {"type": "string", "description": "可选。更新已有记忆时填其 slug；新建可自动生成"},
                "priority": {"type": "string", "description": "active|archived，默认 active"},
            },
            ["description", "body"],
        ),
        _tool(
            "search_memories",
            "搜索记忆库（确定性：FTS5 关键词 + 同义词扩展 + 会话状态联想 + 时间衰减）。",
            {
                "query": {"type": "string", "description": "搜索查询"},
                "top_k": {"type": "integer", "description": "返回结果数（默认 10）"},
                "session_id": {"type": "string", "description": "会话 id：启用回指联想（会话状态按 id 隔离积累）"},
                "context": {"type": "array", "items": {"type": "string"}, "description": "最近几轮对话文本，提升联想召回"},
                "exclude_mem_types": {"type": "array", "items": {"type": "string"}, "description": "按类型排除（如常驻层 profile/rules 已在 system 注入，命中注入时排除防重复）"},
            },
            ["query"],
        ),
        _tool(
            "list_memories",
            "列出记忆，支持按 priority（active/archived）或 mem_type（profile/fact/lesson/rules/project）过滤。",
            {
                "priority": {"type": "string"},
                "mem_type": {"type": "string"},
                "limit": {"type": "integer", "description": "默认 200"},
            },
        ),
        _tool(
            "read_memory",
            "读取单条记忆的完整正文。",
            {"slug": {"type": "string"}},
            ["slug"],
        ),
        _tool(
            "memory_stats",
            "记忆库统计：总数、按 priority/type 分布。",
            {},
        ),
        _tool(
            "touch_memories",
            "命中自增：记忆被实际使用（注入/读取）时更新访问统计，供维护排序。",
            {"slugs": {"type": "array", "items": {"type": "string"}, "description": "记忆 slug 列表"}},
            ["slugs"],
        ),
        _tool(
            "memory_update",
            "全文替换一条记忆（变更前快照进 memory_versions，事件进 memory_audit）。body 是完整新正文。",
            {
                "slug": {"type": "string"},
                "description": {"type": "string", "description": "一句话描述"},
                "body": {"type": "string", "description": "更新后的完整正文"},
                "mem_type": {"type": "string"},
                "priority": {"type": "string"},
                "event_date": {"type": "string"},
            },
            ["slug", "description", "body"],
        ),
        _tool(
            "memory_archive",
            "归档一条记忆（priority → archived，保留内容与历史）。",
            {"slug": {"type": "string"}},
            ["slug"],
        ),
        _tool(
            "memory_delete",
            "删除一条记忆（删除前快照进 memory_versions，可回滚）。",
            {"slug": {"type": "string"}},
            ["slug"],
        ),
        _tool(
            "memory_merge",
            "将 source 记忆合并进 target（merged_body 为目标新正文），默认归档 source。",
            {
                "target_slug": {"type": "string"},
                "source_slug": {"type": "string"},
                "merged_body": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string"},
                "mem_type": {"type": "string"},
            },
            ["target_slug", "source_slug", "merged_body", "description"],
        ),
        _tool(
            "memory_history",
            "列出一条记忆的版本历史。",
            {"slug": {"type": "string"}},
            ["slug"],
        ),
        _tool(
            "memory_rollback",
            "将记忆恢复到指定版本（写入新版本 + 审计）。",
            {"slug": {"type": "string"}, "version": {"type": "string", "description": "版本号（数字）"}},
            ["slug", "version"],
        ),
        _tool(
            "pending_actions",
            "列出待确认的记忆维护动作（archive/delete 候选）。",
            {"status": {"type": "string", "description": "pending/approved/rejected/executed，默认 pending"}},
        ),
        _tool(
            "pending_approve",
            "审批通过一条待确认动作并执行。",
            {"pending_id": {"type": "string"}},
            ["pending_id"],
        ),
        _tool(
            "pending_reject",
            "拒绝一条待确认动作。",
            {"pending_id": {"type": "string"}},
            ["pending_id"],
        ),
        _tool(
            "maintenance_review",
            "审查记忆维护候选（访问最少、更新最早的 active），生成归档待确认动作（不自动执行）。",
            {"limit": {"type": "integer", "description": "候选数量，默认 20"}},
        ),
        _tool(
            "run_consolidate",
            "空闲巩固（consolidate）：审查窗口内创建/更新的记忆；retype/update_desc 低风险修正自动执行（版本+审计留痕），archive 生成待审批动作，merge 仅输出建议。",
            {
                "since_hours": {"type": "number", "description": "审查窗口（小时），默认 24"},
                "limit": {"type": "integer", "description": "候选上限，默认 20"},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    def ok(data: Any) -> list[TextContent]:
        return [TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2, default=str))]

    if name == "memory_remember":
        out = execute_memory_tool("memory_remember", arguments)
        return ok(out["result"])

    if name == "search_memories":
        from .query_builder import extract_topic_grams, filter_topic_words

        query = str(arguments.get("query") or "")
        top_k = int(arguments.get("top_k", 10))
        exclude_types = {str(t) for t in (arguments.get("exclude_mem_types") or [])}
        ctx = [str(c) for c in (arguments.get("context") or [])][:6]

        session_state = None
        topic_words = None
        sid = str(arguments.get("session_id") or "").strip()[:128]
        if sid:
            entry = _session_entry(sid)
            session_state = entry["state"]
            grams = extract_topic_grams(query)
            for kw in grams:
                entry["topic_freq"][kw] = entry["topic_freq"].get(kw, 0) + 1
            topic_words = filter_topic_words(entry["topic_freq"], limit=30)
            session_state.update(query, grams)

        fetch_k = top_k * 2 if exclude_types else top_k
        results = search_memories(
            query, context_messages=ctx, top_k=fetch_k,
            topic_words=topic_words, session_state=session_state,
        )
        if exclude_types:
            results = [r for r in results if r.get("mem_type") not in exclude_types][:top_k]
        return ok([
            {
                "slug": r["slug"], "description": r.get("description", ""),
                "priority": r.get("priority", "active"), "mem_type": r.get("mem_type", "profile"),
                "score": r.get("score", 0), "content": r.get("content", "")[:300],
            }
            for r in results
        ])

    if name == "list_memories":
        mems = list_memories(
            priority=arguments.get("priority"), mem_type=arguments.get("mem_type"),
            limit=int(arguments.get("limit", 200)),
        )
        return ok([
            {
                "slug": m["slug"], "description": m.get("description", ""),
                "priority": m.get("priority", "active"), "mem_type": m.get("mem_type", "profile"),
                "updated_at": m.get("updated_at", ""),
            }
            for m in mems
        ])

    if name == "read_memory":
        content = read_memory(arguments.get("slug", ""))
        if content is None:
            return ok({"error": f"memory '{arguments.get('slug')}' not found"})
        return ok({"slug": arguments.get("slug"), "content": content})

    if name == "memory_stats":
        return ok(get_stats())

    if name == "touch_memories":
        slugs = arguments.get("slugs") or []
        return ok({"ok": True, "touched": touch_memories([str(s) for s in slugs])})

    if name == "memory_update":
        r = replace_memory_result(
            slug=arguments["slug"], description=arguments["description"], body=arguments["body"],
            mem_type=arguments.get("mem_type", "profile"), priority=arguments.get("priority", "active"),
            event_date=arguments.get("event_date"),
            reason="mcp update", audit_action="mcp_update",
        )
        return ok(r.to_dict())

    if name == "memory_archive":
        return ok(archive_memory_result(arguments["slug"], reason="mcp archive").to_dict())

    if name == "memory_delete":
        return ok(delete_memory_result(arguments["slug"], reason="mcp delete").to_dict())

    if name == "memory_merge":
        r = merge_memories_result(
            arguments["target_slug"], arguments["source_slug"],
            arguments["merged_body"], arguments["description"],
            priority=arguments.get("priority"), mem_type=arguments.get("mem_type"),
        )
        return ok(r.to_dict())

    if name == "memory_history":
        return ok(list_history(arguments["slug"]))

    if name == "memory_rollback":
        r = restore_memory_from_history_result(arguments["slug"], arguments["version"], reason="mcp rollback")
        return ok(r.to_dict())

    if name == "pending_actions":
        return ok(list_pending_actions(arguments.get("status", "pending")))

    if name == "pending_approve":
        return ok(approve_pending_action_result(arguments["pending_id"]).to_dict())

    if name == "pending_reject":
        return ok({"ok": reject_pending_action(arguments["pending_id"]), "pending_id": arguments["pending_id"]})

    if name == "maintenance_review":
        from .maintenance import review_maintenance
        return ok(await review_maintenance(limit=int(arguments.get("limit", 20))))

    if name == "run_consolidate":
        from .maintenance import run_consolidate
        return ok(await run_consolidate(
            since_hours=float(arguments.get("since_hours", 24.0)),
            limit=int(arguments.get("limit", 20)),
        ))

    return ok({"error": f"unknown tool: {name}"})


def main() -> None:
    asyncio.run(_run())


async def _run() -> None:
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    main()
