"""只读检查真库版本状态（不触发内核迁移）。"""
import sqlite3

conn = sqlite3.connect(r"file:data/memory.db?mode=ro", uri=True)
sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='memories'").fetchone()
version = "v3" if sql and "'archived'" in (sql[0] or "") else ("missing" if sql is None else "v1/v2")
print("schema:", version)
print("rows:", conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
if version != "missing":
    print("by_type:", dict(conn.execute("SELECT mem_type, COUNT(*) FROM memories GROUP BY mem_type").fetchall()))
    print("by_priority:", dict(conn.execute("SELECT priority, COUNT(*) FROM memories GROUP BY priority").fetchall()))
conn.close()
