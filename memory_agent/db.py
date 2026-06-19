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

        conn.execute(
            """
            UPDATE memories
            SET content = ?, content_hash = ?, description = ?,
                priority = ?, mem_type = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_body, new_hash, description.strip()[:200],
             priority, mem_type, now, existing["id"]),
        )
        conn.commit()
        # 获取更新后的记录
        updated = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (existing["id"],)
        ).fetchone()
        return dict(updated)

    # ── 创建 ──
    mem_id = str(uuid.uuid4())
    recorded = event_date or now
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
         recorded, recorded, new_hash,
         now, now, "{}"),
    )
    conn.commit()

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


def delete_memory(slug: str) -> bool:
    """删除一条记忆。"""
    conn = connect()
    cur = conn.execute("DELETE FROM memories WHERE slug = ?", (slug,))
    conn.commit()
    return cur.rowcount > 0


def save_memory_candidate(candidate: dict[str, Any]) -> tuple[str, str] | None:
    """
    保存 LLM 返回的一条 create/update 动作到 SQLite。

    与旧 store.py 的 save_memory_candidate 接口兼容，但返回
    (action, slug) 而非 (action, Path)。供 extractor.py 调用。

    Returns:
        (action, slug) 或 None（跳过/忽略）
    """
    action = (candidate.get("action") or "create").strip().lower()
    if action == "ignore":
        return None

    slug = candidate.get("target_slug") or candidate.get("slug") or "memory"

    try:
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
