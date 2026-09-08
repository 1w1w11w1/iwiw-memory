"""v1→v3 迁移在真库副本上的验证（绝不触碰 data/memory.db 本体）。"""
import os, shutil, sqlite3, sys, tempfile

sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
copy_path = os.path.join(tmp, "memory.db")
shutil.copy2(os.path.join("data", "memory.db"), copy_path)

os.environ["MEMORY_AGENT_DB_PATH"] = copy_path
import memory_agent.db as memory_db  # noqa: E402

conn = sqlite3.connect(copy_path)
before = {
    "schema_v1": "mem_type IN ('user','feedback','project','reference')" in (conn.execute("SELECT sql FROM sqlite_master WHERE name='memories'").fetchone()[0]),
    "versions": conn.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0],
    "active_memories": conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
}
conn.close()

memory_db.close()
c = memory_db.connect()  # 触发 _migrate_classify
conn = sqlite3.connect(copy_path)
sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='memories'").fetchone()[0]
after = {
    "schema_v3": "'profile','fact','lesson','rules','project'" in sql and "('active','stale','archived')" in sql,
    "versions_kept": conn.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0],
    "active_memories": conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
    "fts_rows": conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0],
}
# 幂等性：再连一次不应报错、不应变化
memory_db.close()
memory_db.connect()
conn2 = sqlite3.connect(copy_path)
after["idempotent_versions"] = conn2.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0]
after["idempotent_rows"] = conn2.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
print("BEFORE:", before)
print("AFTER :", after)
ok = before["schema_v1"] and after["schema_v3"] and after["versions_kept"] == before["versions"] \
     and after["idempotent_versions"] == after["versions_kept"] and after["idempotent_rows"] == after["active_memories"]
print("MIGRATION PROBE:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
