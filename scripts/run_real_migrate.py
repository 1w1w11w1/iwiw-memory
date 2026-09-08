"""真库迁移执行：备份 → v1→v3（内核自动迁移）→ 验证。"""
import sqlite3
from datetime import datetime

ts = datetime.now().strftime("%Y%m%d-%H%M%S")
bak = rf"data\memory.db.backup-{ts}.db"

# 1) 一致性备份（sqlite backup API，含 WAL 已合并内容）
src = sqlite3.connect("data/memory.db")
dst = sqlite3.connect(bak)
src.backup(dst)
dst.close()
print(f"[backup] {bak} ({src.execute('SELECT COUNT(*) FROM memories').fetchone()[0]} rows)")

# 2) 内核 connect()：自动执行 v1→v3 幂等迁移
import memory_agent.db as memory_db  # noqa: E402  真库路径默认

conn = memory_db.connect()
sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='memories'").fetchone()[0]
assert "'archived'" in sql, "迁移后仍非 v3 schema"
print("[migrate] schema → v3 OK")
print("[stats]", memory_db.get_stats())
memory_db.close()
src.close()
print("[done] 真库迁移完成")
