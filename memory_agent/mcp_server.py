"""
mcp_server.py — 记忆代理 MCP 服务器

提供工具：
- extract_and_save:  从对话文本提取记忆并保存
- search_memories:   BM25 混合搜索（规划中增加向量语义检索）
- list_memories:     列出记忆，支持按 priority/type 过滤
- read_memory:       读取单条记忆
- get_index_stats:   索引统计（记忆数量、类型分布、大小）
- reindex:           强制重建 BM25 索引

启动方式：
  python -m memory-agent.mcp_server
"""

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .extractor import extract_and_save
from .db import (
    get_memory,
    list_memories_compat as list_memories,
    read_memory,
    rebuild_index,
)
from .retrieval import hybrid_search

server = Server("memory-agent")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 工具定义
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="extract_and_save",
            description="从文本中提取记忆并自动保存。分析用户消息，识别值得跨会话保存的个人信息（身份、偏好、困难、决策等），ADD-only 追加并 hash 去重。",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "待分析的用户消息文本",
                    },
                    "context": {
                        "type": "string",
                        "description": "可选的对话上下文（前几轮对话），帮助理解消息背景",
                    },
                },
                "required": ["message"],
            },
        ),
        Tool(
            name="search_memories",
            description="混合搜索记忆库（向量语义 + FTS5 关键词 + 时间衰减 + Priority 加权）。",
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
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_memories",
            description="列出所有记忆，支持按 priority 或 type 过滤。",
            inputSchema={
                "type": "object",
                "properties": {
                    "priority": {
                        "type": "string",
                        "description": "按优先级过滤：core / important / normal / archive",
                    },
                    "mem_type": {
                        "type": "string",
                        "description": "按类型过滤：user / feedback / project / reference",
                    },
                },
            },
        ),
        Tool(
            name="read_memory",
            description="读取单条记忆的完整内容。",
            inputSchema={
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "记忆的文件名 slug（不含 .md 后缀）",
                    },
                },
                "required": ["slug"],
            },
        ),
        Tool(
            name="get_index_stats",
            description="获取记忆库统计信息：文件数量、类型分布、总大小。",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="rebuild_memory_index",
            description="根据 memory/*.md frontmatter 重建 MEMORY.md 分层索引。",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 工具实现
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "extract_and_save":
        message = arguments.get("message", "")
        context = arguments.get("context", "")
        saved = await extract_and_save(message, context)
        return [TextContent(
            type="text",
            text=json.dumps(
                {"new_memories": saved, "count": len(saved)},
                ensure_ascii=False,
                indent=2,
            ),
        )]

    elif name == "search_memories":
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 10)
        results = hybrid_search(query, top_k=top_k)
        simplified = [
            {
                "slug": r["slug"],
                "description": r.get("description", ""),
                "priority": r.get("priority", "normal"),
                "score": r.get("score", 0),
                "source": r.get("source", "hybrid"),
            }
            for r in results
        ]
        return [TextContent(
            type="text",
            text=json.dumps(simplified, ensure_ascii=False, indent=2),
        )]

    elif name == "list_memories":
        mems = list_memories(priority=arguments.get("priority"))
        if mem_type := arguments.get("mem_type"):
            mems = [m for m in mems if m["type"] == mem_type]
        # 转换 datetime 为 ISO 字符串，避免 JSON 序列化崩溃
        sanitized = []
        for m in mems:
            d = dict(m)
            if hasattr(d.get("mtime"), "isoformat"):
                d["mtime"] = d["mtime"].isoformat()
            sanitized.append(d)
        return [TextContent(
            type="text",
            text=json.dumps(sanitized, ensure_ascii=False, indent=2),
        )]

    elif name == "read_memory":
        slug = arguments.get("slug", "")
        content = read_memory(slug)
        if content is None:
            return [TextContent(type="text", text=f"Memory '{slug}' not found.")]
        return [TextContent(type="text", text=content)]

    elif name == "get_index_stats":
        mems = list_memories()
        type_dist = {}
        priority_dist = {}
        total_size = 0
        for m in mems:
            t = m.get("type", "unknown")
            type_dist[t] = type_dist.get(t, 0) + 1
            p = m.get("priority", "normal")
            priority_dist[p] = priority_dist.get(p, 0) + 1
            total_size += m.get("size", 0)

        return [TextContent(
            type="text",
            text=json.dumps({
                "total_files": len(mems),
                "total_size_bytes": total_size,
                "total_size_kb": round(total_size / 1024, 1),
                "type_distribution": type_dist,
                "priority_distribution": priority_dist,
            }, ensure_ascii=False, indent=2),
        )]

    elif name == "rebuild_memory_index":
        rebuild_index()
        return [TextContent(type="text", text="MEMORY.md rebuilt.")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 入口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    asyncio.run(_run())


async def _run():
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    main()
