"""
mcp_server.py — 记忆代理 MCP 服务器

提供工具：
- search_memories:   语义向量检索
- list_memories:     列出全量记忆库记录
- read_memory:       读取单条记录
- get_index_stats:   统计信息

启动方式：
  python -m memory_agent.mcp_server
"""

import asyncio
import json
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .engine import default_memory_engine
from .scopes import ActiveMemoryScope

server = Server("memory-agent")


def _host_scope() -> ActiveMemoryScope:
    return ActiveMemoryScope.for_session(
        session_id=os.environ.get("IWIW_MCP_SESSION_ID") or None,
        project_id=os.environ.get("IWIW_MCP_PROJECT_ID") or None,
        workspace_root=os.environ.get("IWIW_MCP_WORKSPACE_ROOT") or None,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 工具定义
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_memories",
            description="在宿主绑定的当前作用域内搜索记忆。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询词",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果数（默认 10）",
                    },
                    "source_type": {
                        "type": "string",
                        "description": "可选来源过滤：message / tool / manual / file / system_event",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_memories",
            description="列出宿主绑定的当前作用域内的记忆记录。",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_type": {
                        "type": "string",
                        "description": "按来源类型过滤：message / tool / manual / file / system_event",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数上限（默认 50）",
                    },
                },
            },
        ),
        Tool(
            name="read_memory",
            description="读取单条记忆记录的完整内容。",
            inputSchema={
                "type": "object",
                "properties": {
                    "record_id": {
                        "type": "string",
                        "description": "记忆记录完整 ID",
                    },
                },
                "required": ["record_id"],
            },
        ),
        Tool(
            name="get_index_stats",
            description="获取记忆库统计信息：记录数量、来源分布、向量块数、倾向观察数。",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 工具实现
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "search_memories":
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 10)
        active_scope = _host_scope()
        results, _trace = default_memory_engine.search(
            query=query,
            top_k=top_k,
            active_scope=active_scope,
            source_type=arguments.get("source_type") or None,
        )
        simplified = [
            {
                "record_id": r.get("record_id", ""),
                "score": r.get("score", 0),
                "source": r.get("source", "vector"),
            }
            for r in results
        ]
        return [TextContent(
            type="text",
            text=json.dumps(simplified, ensure_ascii=False, indent=2),
        )]

    elif name == "list_memories":
        source_type = arguments.get("source_type")
        limit = arguments.get("limit", 50)
        active_scope = _host_scope()
        records = default_memory_engine.list_records(
            source_type=source_type,
            limit=limit,
            active_scope=active_scope,
        )
        simplified = [
            {
                "id": r["id"],
                "source_type": r.get("source_type", ""),
                "scope_type": r.get("scope_type", ""),
                "created_at": r.get("created_at", ""),
                "size": len(r.get("content", "")),
            }
            for r in records
        ]
        return [TextContent(
            type="text",
            text=json.dumps(simplified, ensure_ascii=False, indent=2),
        )]

    elif name == "read_memory":
        record_id = arguments.get("record_id", "")
        active_scope = _host_scope()
        record = default_memory_engine.get_record(record_id, active_scope=active_scope)
        if not record:
            return [TextContent(type="text", text=f"Record '{record_id}' not found.")]
        return [TextContent(type="text", text=record.get("content", ""))]

    elif name == "get_index_stats":
        stats = default_memory_engine.stats(active_scope=_host_scope())
        return [TextContent(
            type="text",
            text=json.dumps(stats, ensure_ascii=False, indent=2),
        )]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 入口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    asyncio.run(_run())


async def _run():
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    main()
