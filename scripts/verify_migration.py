"""meow 迁移质量抽查：检索/分类/留痕/统计。"""
import memory_agent.db as db
from memory_agent.retrieval import search_memories

# 1) 检索冒烟：用户准则应可召回
hits = search_memories("完整审视当前架构 盲目改", top_k=3)
for h in hits:
    print("hit:", h["slug"], h["mem_type"], "|", h["description"][:40])
assert hits, "检索无结果——FTS 未刷新？"

# 2) profile 抽查 + mutation 留痕
mems = db.list_memories(mem_type="profile", limit=5)
assert mems, "profile 层为空"
for x in mems:
    print("profile:", x["slug"], "|", x["description"][:40])
hist = db.list_history(mems[0]["slug"])
print("history entries:", len(hist))

# 3) rules 段（换装后进准则节）
rules = db.list_memories(mem_type="rules", limit=5)
for r in rules:
    print("rules:", r["slug"], "|", (r.get("content") or "")[:50])

# 4) metadata 可追溯
row = db.get_memory(hits[0]["slug"])
print("meta.source:", (row.get("metadata") or "")[:80])

print("[verify] 迁移抽查 PASS")
