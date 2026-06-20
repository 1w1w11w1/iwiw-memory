"""
memory_agent/db.py — SQLite 统一存储层（与会话层共用 sessions.db）

Schema 包含：
- memories        — 记忆主表（取代 memory/*.md 作为真源）
- memory_chunks   — 分块文本 + 向量 BLOB（为 Phase 2 预留）
- memories_fts    — FTS5 关键词检索（回退通道）
- memory_pending_actions — 待确认淘汰动作（为 Phase 5 预留）

数据流：
  写入：extract_and_save / consolidate → db.upsert_memory()
  读取：db.get_memory() / db.search_memories()
  检索：Phase 3 前用 FTS5 回退；Phase 3 后用向量混合检索
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import MEMORY_DB_PATH

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Schema
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCHEMA = """

CREATE TABLE IF NOT EXISTS memories (
    id              TEXT PRIMARY KEY,
    slug            TEXT UNIQUE NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL,
    mem_type        TEXT NOT NULL DEFAULT 'user'
                    CHECK(mem_type IN ('user','feedback','project','reference')),
    priority        TEXT NOT NULL DEFAULT 'normal'
                    CHECK(priority IN ('core','important','normal','archive')),
    event_date      TEXT,
    recorded_date   TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    access_count    INTEGER NOT NULL DEFAULT 0,
    last_access_at  TEXT,
    embedding_model TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    metadata        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS memory_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id       TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    chunk_text      TEXT NOT NULL,
    vector          BLOB,
    model_version   TEXT
);

CREATE INDEX IF NOT EXISTS idx_memory_chunks_memory
    ON memory_chunks(memory_id, chunk_index);

CREATE TABLE IF NOT EXISTS memory_pending_actions (
    id              TEXT PRIMARY KEY,
    ts              TEXT NOT NULL,
    session_id      TEXT,
    cycle_no        INTEGER,
    action          TEXT NOT NULL
                    CHECK(action IN ('archive','merge','delete','downgrade')),
    target_memory_id TEXT,
    source_memory_ids TEXT,
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','approved','rejected','executed')),
    details         TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_memory_pending_status
    ON memory_pending_actions(status);

CREATE TABLE IF NOT EXISTS memory_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id       TEXT,
    slug            TEXT NOT NULL,
    content         TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    mem_type        TEXT NOT NULL DEFAULT 'user',
    priority        TEXT NOT NULL DEFAULT 'normal',
    event_date      TEXT,
    saved_at        TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT ''
);

"""

# FTS5 虚拟表必须单独执行（executescript 不支持 CREATE VIRTUAL TABLE 的事务控制）
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    description,
    content='memories',
    content_rowid='rowid'
);
"""

# FTS 同步触发器
FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, description)
    VALUES (new.rowid, new.content, new.description);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, description)
    VALUES ('delete', old.rowid, old.content, old.description);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, description)
    VALUES ('delete', old.rowid, old.content, old.description);
    INSERT INTO memories_fts(rowid, content, description)
    VALUES (new.rowid, new.content, new.description);
END;
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Connection
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_connection: sqlite3.Connection | None = None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure_data_dir() -> None:
    MEMORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply lightweight migrations for tables created by older memory schemas."""
    fk_rows = conn.execute("PRAGMA foreign_key_list(memory_versions)").fetchall()
    has_delete_cascade = any(str(row["on_delete"]).upper() == "CASCADE" for row in fk_rows)
    if not has_delete_cascade:
        return

    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TABLE IF EXISTS memory_versions_new")
    conn.execute(
        """
        CREATE TABLE memory_versions_new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id       TEXT,
            slug            TEXT NOT NULL,
            content         TEXT NOT NULL,
            description     TEXT NOT NULL DEFAULT '',
            mem_type        TEXT NOT NULL DEFAULT 'user',
            priority        TEXT NOT NULL DEFAULT 'normal',
            event_date      TEXT,
            saved_at        TEXT NOT NULL,
            reason          TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        INSERT INTO memory_versions_new
            (id, memory_id, slug, content, description, mem_type, priority, event_date, saved_at, reason)
        SELECT id, memory_id, slug, content, description, mem_type, priority, event_date, saved_at, reason
        FROM memory_versions
        """
    )
    conn.execute("DROP TABLE memory_versions")
    conn.execute("ALTER TABLE memory_versions_new RENAME TO memory_versions")
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")


def connect() -> sqlite3.Connection:
    """
    获取或创建到 sessions.db 的连接。

    与会话层（selfecho_session/db.py）共用同一 DB 文件。
    只初始化记忆相关的表，不涉及会话表。
    """
    global _connection
    if _connection is not None:
        return _connection

    _ensure_data_dir()
    conn = sqlite3.connect(
        str(MEMORY_DB_PATH),
        timeout=30,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # 执行 schema
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_schema(conn)

    # FTS 必须在事务外单独创建
    conn.execute(FTS_SCHEMA)
    conn.executescript(FTS_TRIGGERS)

    conn.commit()
    _connection = conn
    return conn


def close() -> None:
    """关闭连接（用于测试/重置）。"""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CRUD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _rowdict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _valid_priority(p: str) -> str:
    return p if p in {"core", "important", "normal", "archive"} else "normal"


def _valid_type(t: str) -> str:
    return t if t in {"user", "feedback", "project", "reference"} else "user"


def upsert_memory(
    *,
    slug: str,
    description: str,
    content: str,
    mem_type: str = "user",
    priority: str = "normal",
    event_date: str | None = None,
    content_hash: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    写入一条记忆（INSERT OR UPDATE）。

    如果 slug 已存在则更新（content 追加），不存在则创建。
    返回完整记忆记录。
    """
    conn = connect()
    now = _now()
    priority = _valid_priority(priority)
    mem_type = _valid_type(mem_type)
    slug = slug.strip().lower().replace(" ", "-")[:50] or "memory"

    existing = conn.execute(
        "SELECT * FROM memories WHERE slug = ?", (slug,)
    ).fetchone()

    if existing:
        # ── 更新：追加到正文末尾 ──
        old_body = existing["content"]
        new_body = f"{old_body}\n\n（以下为 {now} 追加）\n\n{content}"
        new_hash = _simple_hash(new_body)
        if existing["content_hash"] == new_hash:
            # 内容无变化，跳过
            return dict(existing)

        _save_version(existing["id"], reason="upsert append")
        conn.execute(
            """
            UPDATE memories
            SET content = ?, content_hash = ?, description = ?,
                priority = ?, mem_type = ?, event_date = COALESCE(?, event_date),
                updated_at = ?
            WHERE id = ?
            """,
            (new_body, new_hash, description.strip()[:200],
             priority, mem_type, event_date, now, existing["id"]),
        )
        conn.commit()
        refresh_memory_vectors(existing["id"])
        # 获取更新后的记录
        updated = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (existing["id"],)
        ).fetchone()
        return dict(updated)

    # ── 创建 ──
    mem_id = str(uuid.uuid4())
    new_hash = content_hash or _simple_hash(content)

    conn.execute(
        """
        INSERT INTO memories
            (id, slug, description, content, mem_type, priority,
             event_date, recorded_date, content_hash,
             created_at, updated_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (mem_id, slug, description.strip()[:200], content, mem_type, priority,
         event_date, now, new_hash,
         now, now, json.dumps(metadata or {}, ensure_ascii=False)),
    )
    conn.commit()
    refresh_memory_vectors(mem_id)

    created = conn.execute(
        "SELECT * FROM memories WHERE id = ?", (mem_id,)
    ).fetchone()
    return dict(created)


def get_memory(slug: str) -> dict[str, Any] | None:
    """按 slug 查询单条记忆。"""
    conn = connect()
    row = conn.execute(
        "SELECT * FROM memories WHERE slug = ?", (slug,)
    ).fetchone()
    return _rowdict(row)


def get_memory_by_id(memory_id: str) -> dict[str, Any] | None:
    """按 id 查询单条记忆。"""
    conn = connect()
    row = conn.execute(
        "SELECT * FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    return _rowdict(row)


def list_memories(
    priority: str | None = None,
    mem_type: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """列出记忆，支持按 priority / mem_type 过滤。"""
    conn = connect()
    sql = "SELECT * FROM memories"
    params: list[Any] = []
    where: list[str] = []
    if priority:
        where.append("priority = ?")
        params.append(priority)
    if mem_type:
        where.append("mem_type = ?")
        params.append(mem_type)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY priority ASC, updated_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params)]


def delete_memory(slug: str, *, save_version: bool = True) -> bool:
    """删除一条记忆，默认先保存可恢复快照。"""
    conn = connect()
    if save_version:
        old = get_memory(slug)
        if not old:
            return False
        if not _save_version(old["id"], reason="delete"):
            return False
    cur = conn.execute("DELETE FROM memories WHERE slug = ?", (slug,))
    conn.commit()
    return cur.rowcount > 0


def save_memory_candidate(candidate: dict[str, Any]) -> tuple[str, str] | None:
    """
    保存 LLM 返回的一条记忆维护动作到 SQLite。

    支持动作：create / update / archive / merge / ignore。
    与旧 store.py 的 save_memory_candidate 接口兼容，但返回
    (action, slug) 而非 (action, Path)。供 extractor.py 调用。

    Returns:
        (action, slug) 或 None（跳过/失败）
    """
    action = (candidate.get("action") or "create").strip().lower()
    if action == "ignore":
        return None

    slug = candidate.get("target_slug") or candidate.get("slug") or "memory"

    try:
        if action == "archive":
            archived = archive_memory_by_slug(slug)
            return ("archive", archived) if archived else None

        if action == "merge":
            target = candidate.get("target_slug") or slug
            content = candidate.get("content", "")
            if not content:
                return None
            record = upsert_memory(
                slug=target,
                description=candidate.get("description", ""),
                content=content,
                mem_type=candidate.get("mem_type", "user"),
                priority=candidate.get("priority", "normal"),
                event_date=candidate.get("event_date"),
                content_hash="",
            )
            return ("merge", record["slug"])

        record = upsert_memory(
            slug=slug,
            description=candidate.get("description", ""),
            content=candidate.get("content", ""),
            mem_type=candidate.get("mem_type", "user"),
            priority=candidate.get("priority", "normal"),
            event_date=candidate.get("event_date"),
            content_hash="",
        )
        return (action, record["slug"])
    except Exception:
        return None


def create_pending_action(
    action: str,
    target_memory_slug: str | None = None,
    *,
    session_id: str | None = None,
    cycle_no: int | None = None,
    reason: str = "",
    source_memory_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """写入一条待确认的淘汰/合并动作。"""
    import uuid
    conn = connect()
    now = _now()
    mem = None
    if target_memory_slug:
        mem = get_memory(target_memory_slug)
    pending_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO memory_pending_actions
            (id, ts, session_id, cycle_no, action, target_memory_id, source_memory_ids, reason, status, details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', '{}')""",
        (
            pending_id, now, session_id, cycle_no, action,
            mem['id'] if mem else None,
            json.dumps(source_memory_ids or [], ensure_ascii=False) if source_memory_ids else None,
            reason,
        ),
    )
    conn.commit()
    return {'id': pending_id, 'action': action, 'target_slug': target_memory_slug, 'reason': reason, 'status': 'pending'}


def list_pending_actions(status: str = 'pending') -> list[dict[str, Any]]:
    """列出待确认的淘汰动作。"""
    conn = connect()
    rows = conn.execute(
        """SELECT pa.id, pa.ts, pa.action, pa.reason, pa.status, pa.source_memory_ids,
                   m.slug AS target_slug, m.description AS target_description, m.priority AS target_priority
            FROM memory_pending_actions pa
            LEFT JOIN memories m ON m.id = pa.target_memory_id
            WHERE pa.status = ?
            ORDER BY pa.ts DESC""",
        (status,),
    ).fetchall()
    return [dict(r) for r in rows]


def _record_audit(
    *,
    action: str,
    target_slug: str | None,
    reason: str,
    backup_path: str | None = None,
    session_id: str | None = None,
    cycle_no: int | None = None,
    details: dict[str, Any] | None = None,
) -> str | None:
    """Best-effort audit write into the shared session DB."""
    audit_id = str(uuid.uuid4())
    try:
        conn = connect()
        conn.execute(
            """
            INSERT INTO memory_audit
                (id, ts, action, target_slug, source_session_id, cycle_no, reason, backup_path, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                _now(),
                action,
                target_slug,
                session_id,
                cycle_no,
                reason,
                backup_path,
                json.dumps(details or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
    except sqlite3.OperationalError:
        return None
    return audit_id


def approve_pending_action(pending_id: str) -> bool:
    """确认执行待确认动作。"""
    conn = connect()
    row = conn.execute(
        "SELECT * FROM memory_pending_actions WHERE id = ? AND status = 'pending'",
        (pending_id,),
    ).fetchone()
    if not row:
        return False

    action = row['action']
    target_id = row['target_memory_id']
    if action == "merge":
        return False
    if not target_id:
        return False

    target = conn.execute("SELECT * FROM memories WHERE id = ?", (target_id,)).fetchone()
    if not target:
        return False

    details = {}
    try:
        details = json.loads(row["details"] or "{}")
    except json.JSONDecodeError:
        details = {}

    backup_path = _save_version(target_id, reason=f"pending {action}: {row['reason'] or ''}".strip())
    if not backup_path:
        return False

    changed_rows = 0
    if action == 'archive':
        cur = conn.execute(
            'UPDATE memories SET priority = "archive", updated_at = ? WHERE id = ?',
            (_now(), target_id),
        )
        changed_rows = cur.rowcount
    elif action == 'delete':
        cur = conn.execute('DELETE FROM memories WHERE id = ?', (target_id,))
        changed_rows = cur.rowcount
    elif action == 'downgrade':
        next_priority = _valid_priority(str(details.get("priority") or "normal"))
        if next_priority == "archive":
            next_priority = "normal"
        cur = conn.execute(
            'UPDATE memories SET priority = ?, updated_at = ? WHERE id = ?',
            (next_priority, _now(), target_id),
        )
        changed_rows = cur.rowcount
    else:
        return False

    if changed_rows <= 0:
        return False

    audit_id = _record_audit(
        action=f"pending_{action}",
        target_slug=target["slug"],
        reason=row["reason"] or f"approve pending {action}",
        backup_path=backup_path,
        session_id=row["session_id"],
        cycle_no=row["cycle_no"],
        details={"pending_id": pending_id, "changed_rows": changed_rows, "details": details},
    )
    details["backup_path"] = backup_path
    if audit_id:
        details["audit_id"] = audit_id

    conn.execute(
        'UPDATE memory_pending_actions SET status = "executed", details = ? WHERE id = ?',
        (json.dumps(details, ensure_ascii=False), pending_id),
    )
    conn.commit()
    return True


def reject_pending_action(pending_id: str) -> bool:
    """拒绝待确认动作。"""
    conn = connect()
    cur = conn.execute(
        "UPDATE memory_pending_actions SET status = 'rejected' WHERE id = ? AND status = 'pending'",
        (pending_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def get_maintenance_candidates(limit: int = 30) -> list[dict[str, Any]]:
    """获取适合维护审查的记忆候选（访问最少、更新最早的 normal/important）。"""
    conn = connect()
    rows = conn.execute(
        """SELECT slug, description, priority, access_count, updated_at, content
            FROM memories
            WHERE priority IN ('normal', 'important')
            ORDER BY access_count ASC, updated_at ASC
            LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_stats() -> dict[str, Any]:
    """记忆库统计。"""
    conn = connect()
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    by_priority = dict(conn.execute(
        "SELECT priority, COUNT(*) FROM memories GROUP BY priority"
    ).fetchall())
    by_type = dict(conn.execute(
        "SELECT mem_type, COUNT(*) FROM memories GROUP BY mem_type"
    ).fetchall())
    total_chunks = conn.execute(
        "SELECT COUNT(*) FROM memory_chunks"
    ).fetchone()[0]
    return {
        "total_memories": total,
        "by_priority": by_priority,
        "by_type": by_type,
        "total_chunks": total_chunks,
    }


def search_fts(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    FTS5 关键词搜索（作为向量检索上线前的回退）。

    这是临时方案。Phase 3 上线向量混合检索后，此函数
    降级为 FTS 回退兜底。
    """
    conn = connect()
    # FTS5 查询：中文按词组匹配（引号），英文前缀查询（加 *）
    fts_tokens: list[str] = []
    for w in query.split():
        w = w.strip()
        if not w:
            continue
        if any('一' <= c <= '鿿' for c in w):
            # 中文词：双引号精确匹配（CJK 按字符拆分，需短语查询）
            fts_tokens.append(f'"{w}"')
        else:
            # 英文/数字：前缀查询
            fts_tokens.append(f"{w}*")
    fts_query = " ".join(fts_tokens)
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.slug, m.description, m.priority, m.mem_type,
                   snippet(memories_fts, 0, '>>>', '<<<', '...', 24) AS snippet
            FROM memories_fts
            JOIN memories m ON m.rowid = memories_fts.rowid
            WHERE memories_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        )
        return [dict(r) for r in rows]
    except sqlite3.OperationalError as e:
        return [{"error": f"FTS query error: {e}"}]


def touch_memory(slug: str) -> None:
    """更新记忆的访问时间（为淘汰策略提供数据）。"""
    conn = connect()
    conn.execute(
        "UPDATE memories SET access_count = access_count + 1, last_access_at = ? WHERE slug = ?",
        (_now(), slug),
    )
    conn.commit()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 向量存储与检索
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def clear_memory_vectors(memory_id: str) -> None:
    """Remove stale vector chunks for a memory."""
    conn = connect()
    conn.execute("DELETE FROM memory_chunks WHERE memory_id = ?", (memory_id,))
    conn.execute("UPDATE memories SET embedding_model = NULL WHERE id = ?", (memory_id,))
    conn.commit()


def refresh_memory_vectors(
    memory_id: str,
    model_version: str = "bge-small-zh-v1.5",
) -> dict[str, Any]:
    """
    Refresh vector chunks for one memory.

    Embedding is best-effort: failures clear stale chunks so FTS remains the
    fallback instead of returning outdated semantic matches.
    """
    conn = connect()
    mem = conn.execute("SELECT id, content FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if not mem:
        return {"ok": False, "reason": "memory not found"}
    chunks = _chunk_text(mem["content"])
    try:
        from .embedding import embed_batch
        vectors = embed_batch(chunks)
    except Exception as exc:
        clear_memory_vectors(memory_id)
        return {"ok": False, "reason": str(exc)}
    if not vectors:
        clear_memory_vectors(memory_id)
        return {"ok": False, "reason": "embedding unavailable"}
    count = store_memory_vector(memory_id, chunks, vectors, model_version=model_version)
    conn.execute(
        "UPDATE memories SET embedding_model = ? WHERE id = ?",
        (model_version, memory_id),
    )
    conn.commit()
    return {"ok": True, "chunks": count}


def store_memory_vector(
    memory_id: str,
    chunks: list[str],
    vectors: list[list[float]],
    model_version: str = "bge-small-zh-v1.5",
) -> int:
    """存储记忆的分块文本和向量到 memory_chunks 表。"""
    conn = connect()
    conn.execute("DELETE FROM memory_chunks WHERE memory_id = ?", (memory_id,))
    import numpy as np
    for idx, (text, vec) in enumerate(zip(chunks, vectors)):
        conn.execute(
            "INSERT INTO memory_chunks (memory_id, chunk_index, chunk_text, vector, model_version) VALUES (?, ?, ?, ?, ?)",
            (memory_id, idx, text, np.array(vec, dtype=np.float32).tobytes(), model_version),
        )
    conn.commit()
    return len(chunks)


def search_vectors(
    query_vector: list[float],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """
    向量余弦相似度搜索（全量扫描，适合 <1000 条记忆）。
    返回按相似度降序排列的记忆片段列表。
    """
    conn = connect()
    import numpy as np
    rows = conn.execute("""
        SELECT c.id, c.memory_id, c.chunk_text, c.vector,
               m.slug, m.description, m.priority, m.mem_type
        FROM memory_chunks c
        JOIN memories m ON m.id = c.memory_id
    """).fetchall()
    if not rows:
        return []

    query_arr = np.array(query_vector, dtype=np.float32)
    scored: list[tuple[float, dict]] = []
    for r in rows:
        try:
            vec = np.frombuffer(r["vector"], dtype=np.float32)
            sim = float(np.dot(query_arr, vec))
        except Exception:
            continue
        scored.append((sim, {
            "slug": r["slug"], "description": r["description"],
            "priority": r["priority"], "mem_type": r["mem_type"],
            "chunk_text": r["chunk_text"][:200], "similarity": round(sim, 4),
        }))
    scored.sort(key=lambda x: x[0], reverse=True)
    # 每个 slug 只保留最高相似度的 chunk
    seen: set[str] = set()
    deduped: list[dict] = []
    for _, item in scored:
        if item["slug"] not in seen:
            seen.add(item["slug"])
            deduped.append(item)
    return deduped[:top_k]


def ensure_all_vectors(force: bool = False, model_version: str = "bge-small-zh-v1.5") -> dict:
    """为所有还没有向量的记忆生成嵌入（幂等）。"""
    conn = connect()
    from .embedding import embed_batch
    memories = conn.execute("SELECT id, slug, content FROM memories ORDER BY updated_at").fetchall()
    processed = 0; skipped = 0; errors = []
    for mem in memories:
        existing = conn.execute(
            "SELECT COUNT(*) FROM memory_chunks WHERE memory_id = ? AND model_version = ?",
            (mem["id"], model_version),
        ).fetchone()[0]
        if existing > 0 and not force:
            skipped += 1; continue

        chunks = _chunk_text(mem["content"])
        vectors = embed_batch(chunks)
        if not vectors:
            errors.append(f"{mem['slug']}: embedding failed"); continue

        conn.execute("DELETE FROM memory_chunks WHERE memory_id = ?", (mem["id"],))
        import numpy as np
        for idx, (chunk, vec) in enumerate(zip(chunks, vectors)):
            conn.execute(
                "INSERT INTO memory_chunks (memory_id, chunk_index, chunk_text, vector, model_version) VALUES (?, ?, ?, ?, ?)",
                (mem["id"], idx, chunk, np.array(vec, dtype=np.float32).tobytes(), model_version),
            )
        processed += 1
    conn.commit()
    return {"processed": processed, "skipped": skipped, "errors": errors, "total": processed + skipped}


def _chunk_text(text: str, max_chars: int = 500) -> list[str]:
    """按段落分割长文本，每块不超过 max_chars 字符。"""
    if len(text) <= max_chars:
        return [text]
    chunks, current = [], ""
    for para in text.split("\n\n"):
        if len(current) + len(para) + 2 > max_chars:
            if current.strip(): chunks.append(current.strip())
            current = para
        else:
            current = (current + "\n\n" + para) if current else para
    if current.strip(): chunks.append(current.strip())
    return chunks or [text]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 迁移：从 Markdown 文件导入
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _simple_hash(text: str) -> str:
    """轻量 hash（非加密，仅去重）。"""
    import hashlib
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _parse_frontmatter(text: str) -> dict[str, str]:
    """解析 YAML frontmatter（仅 key: value 行，不处理嵌套）。"""
    import re
    result: dict[str, str] = {}
    match = re.match(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n?", text)
    if not match:
        return result
    fm = match.group(1)
    for line in fm.splitlines():
        m = re.match(r"^\s*(\w+)\s*:\s*(.+)$", line)
        if m:
            result[m.group(1)] = m.group(2).strip()
    return result


def _strip_frontmatter(text: str) -> str:
    """去掉 YAML frontmatter，只返回正文。"""
    import re
    return re.sub(r"^---\r?\n[\s\S]*?\r?\n---\r?\n?", "", text).strip()


def migrate_from_markdown() -> dict[str, Any]:
    """
    将 memory/*.md 中所有记忆一次性迁移到 SQLite。

    幂等：已存在相同 hash 的记录跳过，不会重复导入。
    迁移完成后可安全停掉 Markdown 同步写入。
    """
    from pathlib import Path as _Path
    from .config import MEMORY_DIR as _MEMORY_DIR

    conn = connect()
    imported = 0
    skipped = 0
    errors: list[str] = []

    for f in sorted(_MEMORY_DIR.glob("*.md")):
        if f.name == "MEMORY.md":
            continue

        try:
            text = f.read_text(encoding="utf-8")
        except Exception as e:
            errors.append(f"{f.name}: read error: {e}")
            continue

        fm = _parse_frontmatter(text)
        body = _strip_frontmatter(text)
        if not body:
            continue

        slug = fm.get("name", f.stem)
        content_hash = _simple_hash(body)

        # 检查是否已导入（按 hash 去重）
        existing = conn.execute(
            "SELECT id FROM memories WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if existing:
            skipped += 1
            continue

        try:
            upsert_memory(
                slug=slug,
                description=fm.get("description", slug)[:200],
                content=body,
                mem_type=_valid_type(fm.get("type", "user")),
                priority=_valid_priority(fm.get("priority", "normal")),
                event_date=fm.get("event_date", None),
                content_hash=content_hash,
            )
            imported += 1
        except Exception as e:
            errors.append(f"{f.name}: import error: {e}")

    if errors:
        import logging
        logging.getLogger("memory_agent.db").warning(
            "Migration errors: %s", "; ".join(errors)
        )

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "total": imported + skipped,
    }


def count_memories_by_priority() -> dict[str, int]:
    try:
        conn = connect()
        rows = conn.execute(
            "SELECT priority, COUNT(*) FROM memories GROUP BY priority"
        ).fetchall()
        return {r["priority"]: r[1] for r in rows}
    except Exception:
        return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 兼容层：替代旧 store.py 接口
# 这些函数保持与 store.py 相同的返回格式，后端基于 SQLite，
# 以便逐步移除旧的 Markdown 文件操作代码。
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _save_version(memory_id: str, reason: str = "") -> str | None:
    """保存记忆当前快照到 memory_versions 表。"""
    conn = connect()
    row = conn.execute(
        "SELECT slug, content, description, mem_type, priority, event_date FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()
    if not row:
        return None
    cursor = conn.execute(
        """INSERT INTO memory_versions
           (memory_id, slug, content, description, mem_type, priority, event_date, saved_at, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (memory_id, row["slug"], row["content"], row["description"],
         row["mem_type"], row["priority"], row["event_date"],
         _now(), reason),
    )
    conn.commit()
    return f"version://{cursor.lastrowid}"


def read_memory(slug: str) -> str | None:
    """读取记忆正文（纯文本），匹配 store.read_memory。"""
    mem = get_memory(slug)
    return mem["content"] if mem else None


def read_memory_full(slug: str) -> dict[str, Any] | None:
    """返回完整记录，匹配 store.read_memory_full。"""
    mem = get_memory(slug)
    if not mem:
        return None
    return {
        "slug": mem["slug"],
        "path": str(MEMORY_DB_PATH.parent / f"{mem['slug']}.md"),
        "frontmatter": "",
        "body": mem["content"],
        "description": mem.get("description", ""),
        "priority": mem.get("priority", "normal"),
        "type": mem.get("mem_type", "user"),
        "event_date": mem.get("event_date", ""),
        "recorded_date": mem.get("recorded_date", ""),
        "hash": mem.get("content_hash", ""),
        "mtime": mem.get("updated_at", ""),
        "size": len(mem.get("content", "")),
    }


def list_memories_compat(
    priority: str | None = None,
    mem_type: str | None = None,
) -> list[dict[str, Any]]:
    """
    返回兼容 store.list_memories 格式的列表。

    结果字段：slug, description, priority, type, mem_type, mtime, size, content
    """
    records = list_memories(priority=priority, mem_type=mem_type)
    return [
        {
            "slug": r["slug"],
            "description": r.get("description", ""),
            "priority": r.get("priority", "normal"),
            "type": r.get("mem_type", "user"),
            "mem_type": r.get("mem_type", "user"),
            "mtime": r.get("updated_at", ""),
            "size": len(r.get("content", "")),
            "content": r.get("content", ""),
        }
        for r in records
    ]


def replace_memory(
    *,
    slug: str,
    description: str,
    body: str,
    mem_type: str = "user",
    priority: str = "normal",
    event_date: str | None = None,
    reason: str = "",
) -> str | None:
    """覆盖写入记忆（替换 content，非追加），匹配 store.replace_memory。"""
    old = get_memory(slug)
    if not old:
        return None

    _save_version(old["id"], reason=reason or "edit")

    conn = connect()
    now = _now()
    priority = _valid_priority(priority)
    mem_type = _valid_type(mem_type)
    content_hash = _simple_hash(body)

    conn.execute(
        """UPDATE memories
           SET content = ?, description = ?, mem_type = ?, priority = ?,
               event_date = COALESCE(?, event_date),
               content_hash = ?, updated_at = ?
           WHERE slug = ?""",
        (body, description.strip()[:200], mem_type, priority,
         event_date, content_hash, now, slug),
    )
    conn.commit()
    refresh_memory_vectors(old["id"])
    return slug


def archive_memory_by_slug(slug: str) -> str | None:
    """设置 priority = 'archive'，匹配 store.archive_memory。"""
    old = get_memory(slug)
    if not old:
        return None
    _save_version(old["id"], reason="archive")
    conn = connect()
    cur = conn.execute(
        "UPDATE memories SET priority = 'archive', updated_at = ? WHERE slug = ?",
        (_now(), slug),
    )
    conn.commit()
    return slug if cur.rowcount > 0 else None


def delete_memory_compat(slug: str) -> str | None:
    """删除记忆，保存快照后删除，匹配 store.delete_memory_file。"""
    return slug if delete_memory(slug) else None


def merge_memories(
    target_slug: str,
    source_slug: str,
    merged_body: str,
    description: str,
    priority: str | None = None,
    mem_type: str | None = None,
    archive_source: bool = True,
) -> str | None:
    """合并两条记忆，匹配 store.merge_memory_files。"""
    target = get_memory(target_slug)
    source = get_memory(source_slug)
    if not target or not source:
        return None

    _save_version(target["id"], reason=f"merge from {source_slug}")
    _save_version(source["id"], reason=f"merge into {target_slug}")

    new_priority = _valid_priority(priority or target.get("priority", "normal"))
    new_type = _valid_type(mem_type or target.get("mem_type", "user"))
    conn = connect()
    now = _now()
    content_hash = _simple_hash(merged_body)
    conn.execute(
        """UPDATE memories
           SET content = ?, description = ?, mem_type = ?, priority = ?,
               content_hash = ?, updated_at = ?
           WHERE slug = ?""",
        (merged_body, description.strip()[:200], new_type, new_priority,
         content_hash, now, target_slug),
    )
    if archive_source:
        conn.execute(
            "UPDATE memories SET priority = 'archive', updated_at = ? WHERE slug = ?",
            (now, source_slug),
        )
    conn.commit()
    refresh_memory_vectors(target["id"])
    return target_slug


def list_history(slug: str) -> list[dict[str, Any]]:
    """列出记忆的版本历史，匹配 store.list_history。"""
    mem = get_memory(slug)
    conn = connect()
    if mem:
        rows = conn.execute(
            """SELECT id, saved_at, reason, length(content) AS size
               FROM memory_versions
               WHERE memory_id = ? OR slug = ?
               ORDER BY saved_at DESC""",
            (mem["id"], slug),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, saved_at, reason, length(content) AS size
               FROM memory_versions
               WHERE slug = ?
               ORDER BY saved_at DESC""",
            (slug,),
        ).fetchall()
    return [
        {
            "version": r["id"],
            "path": f"version://{r['id']}",
            "mtime": r["saved_at"],
            "size": r["size"] or 0,
            "reason": r["reason"] or "",
        }
        for r in rows
    ]


def read_history_record(slug: str, version: str) -> dict[str, Any] | None:
    """读取指定版本的结构化历史记录。"""
    mem = get_memory(slug)
    try:
        version_id = int(version)
    except (TypeError, ValueError):
        return None
    conn = connect()
    if mem:
        row = conn.execute(
            """SELECT id, memory_id, slug, content, description, mem_type, priority, event_date, saved_at, reason
               FROM memory_versions
               WHERE (memory_id = ? OR slug = ?) AND id = ?""",
            (mem["id"], slug, version_id),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT id, memory_id, slug, content, description, mem_type, priority, event_date, saved_at, reason
               FROM memory_versions
               WHERE slug = ? AND id = ?""",
            (slug, version_id),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def restore_memory_from_history(slug: str, version: str, reason: str = "") -> str | None:
    """Restore a memory snapshot from memory_versions, recreating deleted rows."""
    record = read_history_record(slug, version)
    if not record:
        return None

    target_slug = str(record.get("slug") or slug).strip().lower().replace(" ", "-")[:50] or "memory"
    body = record.get("content") or ""
    description = record.get("description") or ""
    mem_type = _valid_type(record.get("mem_type") or "user")
    priority = _valid_priority(record.get("priority") or "normal")
    event_date = record.get("event_date")

    if get_memory(target_slug):
        return replace_memory(
            slug=target_slug,
            description=description,
            body=body,
            mem_type=mem_type,
            priority=priority,
            event_date=event_date,
            reason=reason or f"restore version {version}",
        )

    conn = connect()
    now = _now()
    memory_id = record.get("memory_id") or str(uuid.uuid4())
    if conn.execute("SELECT 1 FROM memories WHERE id = ?", (memory_id,)).fetchone():
        memory_id = str(uuid.uuid4())

    conn.execute(
        """
        INSERT INTO memories
            (id, slug, description, content, mem_type, priority,
             event_date, recorded_date, content_hash,
             created_at, updated_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            target_slug,
            description.strip()[:200],
            body,
            mem_type,
            priority,
            event_date,
            now,
            _simple_hash(body),
            now,
            now,
            json.dumps(
                {"restored_from": f"version://{version}", "restore_reason": reason},
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()
    refresh_memory_vectors(memory_id)
    return target_slug


def read_history(slug: str, version: str) -> str | None:
    """读取指定版本的历史内容，匹配 store.read_history。"""
    row = read_history_record(slug, version)
    if not row:
        return None
    # 以 Markdown 形式返回，包含旧 frontmatter 风格的元信息
    return (
        f"---\nslug: {slug}\n"
        f"description: {row['description']}\n"
        f"type: {row['mem_type']}\n"
        f"priority: {row['priority']}\n"
        f"---\n\n{row['content']}"
    )


def rebuild_index() -> None:
    """重建 MEMORY.md 索引缓存（写 Markdown 文件，保持兼容）。"""
    from .config import MEMORY_DIR, MEMORY_INDEX

    MEMORY_INDEX.parent.mkdir(parents=True, exist_ok=True)
    memories = list_memories_compat()
    grouped: dict[str, list[dict[str, Any]]] = {
        "core": [], "important": [], "normal": [], "archive": [],
    }
    for mem in memories:
        p = mem.get("priority", "normal")
        grouped.setdefault(p, []).append(mem)

    TIER_HEADINGS = [
        ("core", "L0 · Core（始终加载）"),
        ("important", "L1 · Important（始终加载）"),
        ("normal", "L2 · Normal（按话题触发）"),
        ("archive", "L3 · Archive（深度检索按需）"),
    ]

    lines = [
        "# 记忆索引",
        "",
        "> SQLite 是当前真源；Markdown 文件是只读缓存。",
        ">",
        "> **启动时**：从 SQLite 加载 L0（core）和 L1（important）记忆完整内容；L2/L3 按话题检索。",
        "",
    ]
    for priority, heading in TIER_HEADINGS:
        lines.append(f"## {heading}")
        lines.append("")
        items = sorted(grouped.get(priority, []), key=lambda m: m.get("slug", ""))
        if items:
            for mem in items:
                slug = mem.get("slug", "")
                desc = mem.get("description", slug)
                label = slug.replace("_", " ")
                lines.append(f"- [{label}]({slug}.md) — {desc}")
        else:
            lines.append("- （暂无）")
        lines.append("")

    MEMORY_INDEX.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
