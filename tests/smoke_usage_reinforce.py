"""④ 使用强化 tracer bullet（本地冒烟，不入库提交）。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_agent.db as memory_db

tmp = Path(tempfile.mkdtemp()) / "smoke.db"
memory_db.MEMORY_DB_PATH = tmp
memory_db._connection = None

from memory_agent.db import upsert_memory, touch_memories, get_memory, get_maintenance_candidates

upsert_memory(slug="smoke-a", description="测试甲", content="内容甲", mem_type="fact")
upsert_memory(slug="smoke-b", description="测试乙", content="内容乙", mem_type="rules")

# 1) 单独 touch a：去重与空 slug
n = touch_memories(["smoke-a", "smoke-a", ""])
assert n == 1, f"touch rows={n}（重复与空 slug 应去重）"
a = get_memory("smoke-a")
assert a["access_count"] == 1 and a["last_access_at"], "a 未正确自增"

# 2) 批量 touch：a+b
touch_memories(["smoke-a", "smoke-b"])
assert get_memory("smoke-a")["access_count"] == 2
assert get_memory("smoke-b")["access_count"] == 1

# 3) 维护候选：带 mem_type，按访问升序（b=1 < a=2）
cands = get_maintenance_candidates(limit=10)
by_slug = {c["slug"]: c for c in cands}
assert "smoke-a" in by_slug and by_slug["smoke-a"]["mem_type"] == "fact"
assert cands[0]["slug"] == "smoke-b", f"候选排序错: {[c['slug'] for c in cands]}"

# 4) model_tools 读取路径自增
from memory_agent.model_tools import execute_memory_tool
out = execute_memory_tool("memory_read", {"slug": "smoke-a"})
assert out["result"]["ok"] is True
assert get_memory("smoke-a")["access_count"] == 3, "memory_read 未自增"

print("④ smoke: touch 去重/自增/last_access_at/维护候选排序/read 自增 — ALL PASS")
