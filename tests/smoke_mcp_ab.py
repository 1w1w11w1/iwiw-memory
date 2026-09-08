"""A/B MCP 面冒烟：exclude_mem_types + session 回指 + touch（本地，不入库提交）。"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_agent.db as memory_db

tmp = Path(tempfile.mkdtemp()) / "smoke.db"
memory_db.MEMORY_DB_PATH = tmp
memory_db._connection = None

from memory_agent.db import upsert_memory, get_memory
import memory_agent.mcp_server as mcp

upsert_memory(slug="user-is-dev", description="用户身份画像", content="用户是开发者", mem_type="profile")
upsert_memory(slug="badminton-wed", description="用户每周三晚打羽毛球", content="用户每周三晚上去体育馆打羽毛球", mem_type="fact")


async def main():
    async def call(name, args):
        blocks = await mcp.call_tool(name, args)
        import json
        return json.loads(blocks[0].text)

    # A：exclude_mem_types 排除常驻层
    hits = await call("search_memories", {"query": "用户 开发者 羽毛球", "top_k": 5,
                                          "exclude_mem_types": ["profile", "rules"]})
    assert all(h["mem_type"] not in ("profile", "rules") for h in hits), hits
    assert any(h["slug"] == "badminton-wed" for h in hits), hits

    # 无排除时 profile 可召回
    hits_all = await call("search_memories", {"query": "用户 开发者", "top_k": 5})
    assert any(h["slug"] == "user-is-dev" for h in hits_all), hits_all

    # B：session 回指——第一轮积累话题，第二轮无词面命中时靠会话状态召回
    await call("search_memories", {"query": "今天周三晚上去打球", "top_k": 3, "session_id": "sess-A"})
    # 回指：不含"羽毛球"字面，靠 SessionState 话题联想
    hits2 = await call("search_memories", {"query": "那个活动改时间了吗", "top_k": 3, "session_id": "sess-A"})
    assert any(h["slug"] == "badminton-wed" for h in hits2), f"回指失败: {hits2}"

    # 会话隔离：sess-B 无状态，同一回指查询不应命中
    hits3 = await call("search_memories", {"query": "那个活动改时间了吗", "top_k": 3, "session_id": "sess-B"})
    assert all(h["slug"] != "badminton-wed" for h in hits3), f"会话串了: {hits3}"

    # touch
    out = await call("touch_memories", {"slugs": ["badminton-wed", "badminton-wed"]})
    assert out["ok"] and out["touched"] == 1, out
    assert get_memory("badminton-wed")["access_count"] == 1

    print("A/B MCP smoke: exclude 排除 + 回指联想 + 会话隔离 + touch — ALL PASS")


asyncio.run(main())
