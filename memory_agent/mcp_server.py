"""
mcp_server.py — 记忆系统 MCP 服务器（DSH 接入桥）

提供工具：
- extract_and_save : 从对话文本提取记忆并保存（实时触发）
- search_memories  : 确定性搜索（FTS5 词面 + 会话状态联想 + 时间衰减 + 优先级）
- list_memories    : 列出记忆（按 priority/type 过滤）
- read_memory      : 读取单条记忆
- memory_stats     : 记忆库统计
- memory_update    : 全文替换记忆（带版本 + 审计）
- memory_archive   : 归档记忆
- memory_delete    : 删除记忆（带版本 + 审计）
- memory_merge     : 合并记忆
- memory_history   : 版本历史
- memory_rollback  : 从版本回滚
- pending_actions  : 待确认动作列表
- pending_approve  : 审批通过
- pending_reject   : 审批拒绝
- memory_trigger   : 判断消息是否应触发提取


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
)
from .extractor import extract_and_save
from .retrieval import search_memories
from .triggers import should_trigger

server = Server("memory-system")


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
            "extract_and_save",
            "分析对话文本，识别值得跨会话保存的个人信息（身份、偏好、困难、决策、反馈等），提取并保存到长期记忆。ADD 语义：已有记忆全文替换。",
            {
                "message": {"type": "string", "description": "待分析的用户消息"},
                "context": {"type": "string", "description": "可选的对话上下文（前几轮）"},
            },
            ["message"],
        ),
        _tool(
            "search_memories",
            "搜索记忆库（确定性：FTS5 关键词 + 同义词扩展 + 会话状态联想 + 时间衰减 + Priority 加权）。",
            {
                "query": {"type": "string", "description": "搜索查询"},
                "top_k": {"type": "integer", "description": "返回结果数（默认 10）"},
            },
            ["query"],
        ),
        _tool(
            "list_memories",
            "列出记忆，支持按 priority（core/normal/archive）或 mem_type（user/feedback/project/reference）过滤。",
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
            "归档一条记忆（priority → archive，保留内容与历史）。",
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
            "列出待确认的记忆维护动作（archive/delete/downgrade 候选）。",
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
            "memory_trigger",
            "判断一条消息是否包含高信号（应立刻提取为长期记忆的内容）。",
            {"message": {"type": "string"}},
            ["message"],
        ),
        _tool(
            "maintenance_review",
            "审查记忆维护候选（访问最少、更新最早的 normal），生成归档待确认动作（不自动执行）。",
            {"limit": {"type": "integer", "description": "候选数量，默认 20"}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    def ok(data: Any) -> list[TextContent]:
        return [TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2, default=str))]

    if name == "extract_and_save":
        saved = await extract_and_save(arguments.get("message", ""), arguments.get("context", ""))
        return ok({"new_memories": saved, "count": len(saved)})

    if name == "search_memories":
        results = search_memories(arguments.get("query", ""), top_k=int(arguments.get("top_k", 10)))
        return ok([
            {
                "slug": r["slug"], "description": r.get("description", ""),
                "priority": r.get("priority", "normal"), "mem_type": r.get("mem_type", "user"),
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
                "priority": m.get("priority", "normal"), "mem_type": m.get("mem_type", "user"),
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

    if name == "memory_update":
        r = replace_memory_result(
            slug=arguments["slug"], description=arguments["description"], body=arguments["body"],
            mem_type=arguments.get("mem_type", "user"), priority=arguments.get("priority", "normal"),
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

    if name == "memory_trigger":
        message = arguments.get("message", "")
        return ok({"trigger": should_trigger(message), "message": message[:200]})

    if name == "maintenance_review":
        from .maintenance import review_maintenance
        return ok(await review_maintenance(limit=int(arguments.get("limit", 20))))

    return ok({"error": f"unknown tool: {name}"})


def main() -> None:
    asyncio.run(_run())


async def _run() -> None:
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    main()
