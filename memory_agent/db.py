"""
SQLite storage for long-term memory.

The source of truth is selfecho_data/sessions.db. Markdown files under
memory/ are legacy/export caches only; runtime writes must go through this
module so versions, audit events, FTS, and vector chunks stay in sync.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
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
    mem_type        TEXT NOT NULL DEFAULT 'fact'
                    CHECK(mem_type IN ('profile','fact','lesson','rules','project')),
    priority        TEXT NOT NULL DEFAULT 'active'
                    CHECK(priority IN ('active','archived')),
    event_date      TEXT,
    recorded_date   TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    access_count    INTEGER NOT NULL DEFAULT 0,
    last_access_at  TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    metadata        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS memory_pending_actions (
    id              TEXT PRIMARY KEY,
    ts              TEXT NOT NULL,
    session_id      TEXT,
    cycle_no        INTEGER,
    action          TEXT NOT NULL
                    CHECK(action IN ('archive','merge','delete')),
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
    mem_type        TEXT NOT NULL DEFAULT 'fact',
    priority        TEXT NOT NULL DEFAULT 'active',
    event_date      TEXT,
    saved_at        TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS memory_audit (
    id                TEXT PRIMARY KEY,
    ts                TEXT NOT NULL,
    action            TEXT NOT NULL,
    target_slug       TEXT,
    source_session_id TEXT,
    cycle_no          INTEGER,
    reason            TEXT,
    backup_path       TEXT,
    details           TEXT NOT NULL DEFAULT '{}'
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


def _migrate_classify(conn: sqlite3.Connection) -> None:
    """分类体系 v3 幂等迁移（用户 2026-09-08 拍板：废除重要程度分类，全标签化）。

    - mem_type 五层：profile（身份画像，全量注入）/ fact / lesson / rules / project
    - priority 降为生命周期二态：active（在役，可被检索/注入）/ archived（归档留痕）
    - 旧值映射：v1 user→profile、feedback→lesson、reference→fact；v2 直通
      priority：core/standing/normal/important→active，archive→archived
    重建表后 FTS rowid 映射失效，由 rebuild 修复；触发器随旧表删除后重建。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memories'"
    ).fetchone()
    if row is None:
        return  # 空库：首次建表直接走新 SCHEMA
    if "'archived'" in (row[0] or ""):
        return  # 已是 v3（CHECK 含 archived 值）
    conn.executescript("ALTER TABLE memories RENAME TO memories_old;")
    conn.executescript(SCHEMA)
    # mem_type 映射：v1 feedback→lesson、reference→fact；v2 直通；user/fact/lesson/rules/project 直通
    mem_type_expr = (
        "CASE mem_type WHEN 'user' THEN 'profile' WHEN 'feedback' THEN 'lesson' WHEN 'reference' THEN 'fact' ELSE mem_type END"
    )
    # priority 映射：v1/v2 的 core/standing/normal/important → active，archive → archived
    status_expr = (
        "CASE priority WHEN 'archive' THEN 'archived' ELSE 'active' END"
    )
    conn.execute(
        f"""
        INSERT INTO memories (
            id, slug, description, content, mem_type, priority,
            event_date, recorded_date, content_hash, access_count, last_access_at,
            created_at, updated_at, metadata
        )
        SELECT
            id, slug, description, content, {mem_type_expr}, {status_expr},
            event_date, recorded_date, content_hash, access_count, last_access_at,
            created_at, updated_at, metadata
        FROM memories_old
        """
    )
    conn.execute("DROP TABLE memories_old")
    # external-content FTS：rowid 映射随表重建失效，全量重建索引
    conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
    conn.commit()


def connect() -> sqlite3.Connection:
    """
    获取或创建到 data/memory.db 的连接（单一真源）。

    记忆系统独立持有该库，不与其他层共享文件。
    多线程读写由 SQLite WAL + busy_timeout 保证安全。
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
    conn.execute("PRAGMA busy_timeout=30000")

    # 执行 schema
    conn.executescript(SCHEMA)
    conn.commit()

    # 幂等迁移：分类体系 v3（用户拍板废除重要程度分类，全标签化：profile/fact/lesson/rules/project × active/archived）
    # 旧值映射见 _migrate_classify
    _migrate_classify(conn)
    # 幂等清理：旧库残留的向量表（去向量化）
    conn.execute("DROP TABLE IF EXISTS memory_chunks")
    try:
        # 旧库 embedding_model 列（SQLite 3.35+ 支持 DROP COLUMN）
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(memories)").fetchall()]
        if "embedding_model" in cols:
            conn.execute("ALTER TABLE memories DROP COLUMN embedding_model")
    except sqlite3.OperationalError:
        pass  # 旧 SQLite 不支持 DROP COLUMN，保留列（无害）
    conn.commit()

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


@dataclass
class MemoryMutationResult:
    ok: bool
    action: str
    target_slug: str | None = None
    changed_rows: int = 0
    version_id: str | None = None
    audit_id: str | None = None
    backup_path: str | None = None
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "target_slug": self.target_slug,
            "changed_rows": self.changed_rows,
            "version_id": self.version_id,
            "audit_id": self.audit_id,
            "backup_path": self.backup_path,
            "error": self.error,
            "details": self.details,
        }


def _valid_priority(p: str) -> str:
    # 生命周期二态：active=在役（可被检索/注入）/ archived=归档（留痕可回滚）
    # 旧值兼容映射：core/standing/normal/important → active，archive → archived
    p = {"core": "active", "standing": "active", "normal": "active",
         "important": "active", "archive": "archived"}.get(p, p)
    return p if p in {"active", "archived"} else "active"


def _valid_type(t: str) -> str:
    # 类型五层：profile=身份画像（模式常驻）/ fact=事实 / lesson=教训 / rules=准则 / project=项目
    # 旧值兼容映射：user → profile，feedback → lesson，reference → fact
    t = {"feedback": "lesson", "reference": "fact", "user": "profile"}.get(t, t)
    return t if t in {"profile", "fact", "lesson", "rules", "project"} else "fact"


def upsert_memory(
    *,
    slug: str,
    description: str,
    content: str,
    mem_type: str = "fact",
    priority: str = "active",
    event_date: str | None = None,
    content_hash: str = "",
    metadata: dict[str, Any] | None = None,
    recorded_date: str | None = None,
) -> dict[str, Any]:
    """
    写入一条记忆（INSERT OR REPLACE 语义）。

    - slug 不存在：创建新记忆。
    - slug 已存在：以 content 全文替换旧正文，变更前快照写入 memory_versions，
      变更事件写入 memory_audit，并同步 FTS 索引。

    任何失败都会 raise（不允许静默成功）；调用方如需可恢复结果，
    请使用 replace_memory_result / delete_memory_result 等 *result 入口。
    """
    slug = slug.strip().lower().replace(" ", "-")[:50] or "memory"
    existing = get_memory(slug)

    if existing:
        result = replace_memory_result(
            slug=slug,
            description=description,
            body=content,
            mem_type=mem_type,
            priority=priority,
            event_date=event_date,
            reason="upsert replace",
            audit_action="upsert_replace",
            details={"mem_type": mem_type, "priority": priority},
        )
        if not result.ok:
            raise RuntimeError(f"memory update failed: {result.error}")
        return dict(get_memory(slug))

    conn = connect()
    now = _now()
    priority = _valid_priority(priority)
    mem_type = _valid_type(mem_type)
    mem_id = str(uuid.uuid4())
    new_hash = content_hash or _simple_hash(content)

    cur = conn.execute(
        """
        INSERT INTO memories
            (id, slug, description, content, mem_type, priority,
             event_date, recorded_date, content_hash,
             created_at, updated_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (mem_id, slug, description.strip()[:200], content, mem_type, priority,
         event_date, recorded_date or now, new_hash,
         now, now, json.dumps(metadata or {}, ensure_ascii=False)),
    )
    audit_id = _record_audit(
        action="create",
        target_slug=slug,
        reason="create extracted memory",
        details={"changed_rows": cur.rowcount, "mem_type": mem_type, "priority": priority},
        commit=False,
    )
    if not audit_id or cur.rowcount <= 0:
        conn.rollback()
        raise RuntimeError("memory create audit failed")
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


def delete_memory_result(
    slug: str,
    *,
    save_version: bool = True,
    reason: str = "delete",
    audit_action: str = "delete",
    session_id: str | None = None,
    cycle_no: int | None = None,
    details: dict[str, Any] | None = None,
) -> MemoryMutationResult:
    """Delete a memory through the audited mutation path."""
    conn = connect()
    old = get_memory(slug)
    if not old:
        return MemoryMutationResult(False, audit_action, slug, error="memory not found")
    try:
        backup_path = None
        if save_version:
            backup_path = _save_version(old["id"], reason=reason, commit=False)
            if not backup_path:
                conn.rollback()
                return MemoryMutationResult(False, audit_action, slug, error="version snapshot failed")
        cur = conn.execute("DELETE FROM memories WHERE slug = ?", (slug,))
        changed_rows = cur.rowcount
        if changed_rows <= 0:
            conn.rollback()
            return MemoryMutationResult(False, audit_action, slug, changed_rows=changed_rows, error="no rows changed")
        audit_details = {"changed_rows": changed_rows, **(details or {})}
        audit_id = _record_audit(
            action=audit_action,
            target_slug=slug,
            reason=reason,
            backup_path=backup_path,
            session_id=session_id,
            cycle_no=cycle_no,
            details=audit_details,
            commit=False,
        )
        if not audit_id:
            conn.rollback()
            return MemoryMutationResult(False, audit_action, slug, changed_rows=changed_rows, backup_path=backup_path, error="audit write failed")
        conn.commit()
        return MemoryMutationResult(
            True,
            audit_action,
            slug,
            changed_rows=changed_rows,
            version_id=backup_path,
            audit_id=audit_id,
            backup_path=backup_path,
            details=audit_details,
        )
    except Exception as exc:
        conn.rollback()
        return MemoryMutationResult(False, audit_action, slug, error=str(exc))


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
    commit: bool = True,
) -> str | None:
    """Write an audit event and return its id."""
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
        if commit:
            conn.commit()
    except sqlite3.OperationalError:
        return None
    return audit_id


def approve_pending_action_result(pending_id: str) -> MemoryMutationResult:
    """确认执行待确认动作，并返回版本、审计和改动行数。"""
    conn = connect()
    row = conn.execute(
        "SELECT * FROM memory_pending_actions WHERE id = ? AND status = 'pending'",
        (pending_id,),
    ).fetchone()
    if not row:
        return MemoryMutationResult(False, "pending_approve", error="pending action not found")

    action = row['action']
    target_id = row['target_memory_id']
    if action == "merge":
        return MemoryMutationResult(False, "pending_merge", error="pending merge is not executable")
    if not target_id:
        return MemoryMutationResult(False, f"pending_{action}", error="pending action has no target")

    target = conn.execute("SELECT * FROM memories WHERE id = ?", (target_id,)).fetchone()
    if not target:
        return MemoryMutationResult(False, f"pending_{action}", error="target memory not found")

    details = {}
    try:
        details = json.loads(row["details"] or "{}")
    except json.JSONDecodeError:
        details = {}

    mutation_action = f"pending_{action}"
    try:
        backup_path = _save_version(target_id, reason=f"pending {action}: {row['reason'] or ''}".strip(), commit=False)
        if not backup_path:
            conn.rollback()
            return MemoryMutationResult(False, mutation_action, target["slug"], error="version snapshot failed")

        changed_rows = 0
        if action == 'archive':
            cur = conn.execute(
                "UPDATE memories SET priority = 'archived', updated_at = ? WHERE id = ?",
                (_now(), target_id),
            )
            changed_rows = cur.rowcount
        elif action == 'delete':
            cur = conn.execute('DELETE FROM memories WHERE id = ?', (target_id,))
            changed_rows = cur.rowcount
        else:
            conn.rollback()
            return MemoryMutationResult(False, mutation_action, target["slug"], backup_path=backup_path, error="unsupported pending action")

        if changed_rows <= 0:
            conn.rollback()
            return MemoryMutationResult(False, mutation_action, target["slug"], changed_rows=changed_rows, backup_path=backup_path, error="no rows changed")

        audit_details = {"pending_id": pending_id, "changed_rows": changed_rows, "details": details}
        audit_id = _record_audit(
            action=mutation_action,
            target_slug=target["slug"],
            reason=row["reason"] or f"approve pending {action}",
            backup_path=backup_path,
            session_id=row["session_id"],
            cycle_no=row["cycle_no"],
            details=audit_details,
            commit=False,
        )
        if not audit_id:
            conn.rollback()
            return MemoryMutationResult(False, mutation_action, target["slug"], changed_rows=changed_rows, backup_path=backup_path, error="audit write failed")

        details["backup_path"] = backup_path
        details["version_id"] = backup_path
        details["audit_id"] = audit_id
        details["changed_rows"] = changed_rows
        conn.execute(
            'UPDATE memory_pending_actions SET status = "executed", details = ? WHERE id = ?',
            (json.dumps(details, ensure_ascii=False), pending_id),
        )
        conn.commit()
        return MemoryMutationResult(
            True,
            mutation_action,
            target["slug"],
            changed_rows=changed_rows,
            version_id=backup_path,
            audit_id=audit_id,
            backup_path=backup_path,
            details=audit_details,
        )
    except Exception as exc:
        conn.rollback()
        return MemoryMutationResult(False, mutation_action, target["slug"], error=str(exc))


def approve_pending_action(pending_id: str) -> bool:
    """确认执行待确认动作。"""
    return approve_pending_action_result(pending_id).ok


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
    """获取适合维护审查的记忆候选（访问最少、更新最早的 active）。"""
    conn = connect()
    rows = conn.execute(
        """SELECT slug, description, mem_type, priority, access_count, last_access_at, updated_at, content
            FROM memories
            WHERE priority = 'active'
            ORDER BY access_count ASC, updated_at ASC
            LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_changed(since_iso: str, limit: int = 20) -> list[dict[str, Any]]:
    """获取窗口内创建/更新的 active 记忆（巩固审查范围）。"""
    conn = connect()
    rows = conn.execute(
        """SELECT slug, description, mem_type, priority, access_count, last_access_at, updated_at, content
            FROM memories
            WHERE priority = 'active' AND updated_at >= ?
            ORDER BY updated_at DESC
            LIMIT ?""",
        (since_iso, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def touch_memories(slugs: list[str]) -> int:
    """命中自增（使用强化）：记忆被联想注入/读取时更新访问统计。

    统计字段非内容变更，不走 version/audit：mutation 契约约束的是内容与
    生命周期，访问计数可再生且只服务维护排序，进审计只会稀释信噪比。
    """
    slugs = [s for s in dict.fromkeys(slugs or []) if s]
    if not slugs:
        return 0
    conn = connect()
    cur = conn.execute(
        "UPDATE memories SET access_count = access_count + 1, last_access_at = ? "
        f"WHERE slug IN ({','.join('?' * len(slugs))})",
        (_now(), *slugs),
    )
    conn.commit()
    return cur.rowcount


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
    return {
        "total_memories": total,
        "by_priority": by_priority,
        "by_type": by_type,
    }


def search_fts(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    FTS5 关键词搜索 + CJK 子串兜底（确定性检索的词面通道）。
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
        ).fetchall()
        results = [dict(r) for r in rows]
    except sqlite3.OperationalError:
        # MATCH 语法/分词失败（如中文无匹配 token）
        results = []
    if results:
        return results
    # FTS5 空结果（常见于中文分词 miss）→ 子串匹配兜底
    return _search_like(query, limit=limit)


def _search_like(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Substring fallback for CJK terms when FTS tokenization misses them."""
    tokens = _fallback_query_tokens(query)
    if not tokens:
        return []
    conn = connect()
    rows = conn.execute(
        "SELECT id, slug, description, priority, mem_type, content, updated_at FROM memories"
    ).fetchall()
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for row in rows:
        content = row["content"] or ""
        description = row["description"] or ""
        haystack = f"{description}\n{content}".lower()
        score = sum(1 for token in tokens if token.lower() in haystack)
        if score <= 0:
            continue
        scored.append((
            score,
            row["updated_at"] or "",
            {
                "id": row["id"],
                "slug": row["slug"],
                "description": description,
                "priority": row["priority"],
                "mem_type": row["mem_type"],
                "snippet": _make_snippet(content, tokens),
                "fallback": "like",
            },
        ))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item for _, __, item in scored[:limit]]


def _fallback_query_tokens(query: str) -> list[str]:
    stop_tokens = {
        "什么", "是什", "怎么", "如何", "为什", "为什么", "这个", "那个",
        "这些", "那些", "我们", "你们", "他们", "用户", "最近", "之前",
        "觉得", "知道", "记得", "有没有",
    }
    tokens: list[str] = []
    for raw in query.replace('"', " ").replace("*", " ").split():
        token = raw.strip().strip("，。！？；：,.!?;:")
        if not token or token in stop_tokens:
            continue
        # 中文实义单字（吃/辣/跑）参与子串匹配；纯英文/数字单字丢弃
        if len(token) < 2 and not any("一" <= c <= "鿿" for c in token):
            continue
        tokens.append(token)
        if any("一" <= c <= "鿿" for c in token) and len(token) >= 4:
            tokens.extend(
                token[i:i + 2]
                for i in range(len(token) - 1)
                if token[i:i + 2] not in stop_tokens
            )
    seen: set[str] = set()
    deduped: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            deduped.append(token)
    return deduped[:12]


def _make_snippet(content: str, tokens: list[str], radius: int = 80) -> str:
    lower = content.lower()
    hit_at = -1
    hit_token = ""
    for token in tokens:
        hit_at = lower.find(token.lower())
        if hit_at >= 0:
            hit_token = token
            break
    if hit_at < 0:
        return content[: radius * 2]
    start = max(hit_at - radius, 0)
    end = min(hit_at + len(hit_token) + radius, len(content))
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Hash helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _simple_hash(text: str) -> str:
    """轻量 hash（非加密，仅去重）。"""
    import hashlib
    return hashlib.md5(text.encode("utf-8")).hexdigest()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 兼容层：替代旧 store.py 接口
# 这些函数保持与 store.py 相同的返回格式，后端基于 SQLite，
# 以便逐步移除旧的 Markdown 文件操作代码。
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _save_version(memory_id: str, reason: str = "", *, commit: bool = True) -> str | None:
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
    if commit:
        conn.commit()
    return f"version://{cursor.lastrowid}"


def read_memory(slug: str) -> str | None:
    """读取记忆正文（纯文本），匹配 store.read_memory。"""
    mem = get_memory(slug)
    return mem["content"] if mem else None


def replace_memory_result(
    *,
    slug: str,
    description: str,
    body: str,
    mem_type: str = "fact",
    priority: str = "active",
    event_date: str | None = None,
    reason: str = "",
    audit_action: str = "manual_edit",
    details: dict[str, Any] | None = None,
) -> MemoryMutationResult:
    """覆盖写入记忆，并返回审计结果。"""
    old = get_memory(slug)
    if not old:
        return MemoryMutationResult(False, audit_action, slug, error="memory not found")

    conn = connect()
    now = _now()
    priority = _valid_priority(priority)
    mem_type = _valid_type(mem_type)
    content_hash = _simple_hash(body)

    try:
        backup_path = _save_version(old["id"], reason=reason or "edit", commit=False)
        if not backup_path:
            conn.rollback()
            return MemoryMutationResult(False, audit_action, slug, error="version snapshot failed")
        cur = conn.execute(
            """UPDATE memories
               SET content = ?, description = ?, mem_type = ?, priority = ?,
                   event_date = COALESCE(?, event_date),
                   content_hash = ?, updated_at = ?
               WHERE slug = ?""",
            (body, description.strip()[:200], mem_type, priority,
             event_date, content_hash, now, slug),
        )
        changed_rows = cur.rowcount
        if changed_rows <= 0:
            conn.rollback()
            return MemoryMutationResult(False, audit_action, slug, changed_rows=changed_rows, backup_path=backup_path, error="no rows changed")
        audit_details = {"changed_rows": changed_rows, **(details or {})}
        audit_id = _record_audit(
            action=audit_action,
            target_slug=slug,
            reason=reason or audit_action,
            backup_path=backup_path,
            details=audit_details,
            commit=False,
        )
        if not audit_id:
            conn.rollback()
            return MemoryMutationResult(False, audit_action, slug, changed_rows=changed_rows, backup_path=backup_path, error="audit write failed")
        conn.commit()
        return MemoryMutationResult(
            True,
            audit_action,
            slug,
            changed_rows=changed_rows,
            version_id=backup_path,
            audit_id=audit_id,
            backup_path=backup_path,
            details=audit_details,
        )
    except Exception as exc:
        conn.rollback()
        return MemoryMutationResult(False, audit_action, slug, error=str(exc))


def archive_memory_result(slug: str, *, reason: str = "archive", audit_action: str = "archive") -> MemoryMutationResult:
    """设置 priority = 'archived'，并返回审计结果。"""
    old = get_memory(slug)
    if not old:
        return MemoryMutationResult(False, audit_action, slug, error="memory not found")
    conn = connect()
    try:
        backup_path = _save_version(old["id"], reason=reason, commit=False)
        if not backup_path:
            conn.rollback()
            return MemoryMutationResult(False, audit_action, slug, error="version snapshot failed")
        cur = conn.execute(
            "UPDATE memories SET priority = 'archived', updated_at = ? WHERE slug = ?",
            (_now(), slug),
        )
        changed_rows = cur.rowcount
        if changed_rows <= 0:
            conn.rollback()
            return MemoryMutationResult(False, audit_action, slug, changed_rows=changed_rows, backup_path=backup_path, error="no rows changed")
        audit_details = {"changed_rows": changed_rows}
        audit_id = _record_audit(
            action=audit_action,
            target_slug=slug,
            reason=reason,
            backup_path=backup_path,
            details=audit_details,
            commit=False,
        )
        if not audit_id:
            conn.rollback()
            return MemoryMutationResult(False, audit_action, slug, changed_rows=changed_rows, backup_path=backup_path, error="audit write failed")
        conn.commit()
        return MemoryMutationResult(
            True,
            audit_action,
            slug,
            changed_rows=changed_rows,
            version_id=backup_path,
            audit_id=audit_id,
            backup_path=backup_path,
            details=audit_details,
        )
    except Exception as exc:
        conn.rollback()
        return MemoryMutationResult(False, audit_action, slug, error=str(exc))


def merge_memories_result(
    target_slug: str,
    source_slug: str,
    merged_body: str,
    description: str,
    priority: str | None = None,
    mem_type: str | None = None,
    archive_source: bool = True,
) -> MemoryMutationResult:
    """合并两条记忆，并返回审计结果。"""
    target = get_memory(target_slug)
    source = get_memory(source_slug)
    if not target or not source:
        return MemoryMutationResult(False, "merge", target_slug, error="target or source memory not found")

    new_priority = _valid_priority(priority or target.get("priority", "active"))
    new_type = _valid_type(mem_type or target.get("mem_type", "profile"))
    conn = connect()
    now = _now()
    content_hash = _simple_hash(merged_body)
    try:
        target_backup = _save_version(target["id"], reason=f"merge from {source_slug}", commit=False)
        source_backup = _save_version(source["id"], reason=f"merge into {target_slug}", commit=False)
        if not target_backup or not source_backup:
            conn.rollback()
            return MemoryMutationResult(False, "merge", target_slug, error="version snapshot failed")
        target_cur = conn.execute(
            """UPDATE memories
               SET content = ?, description = ?, mem_type = ?, priority = ?,
                   content_hash = ?, updated_at = ?
               WHERE slug = ?""",
            (merged_body, description.strip()[:200], new_type, new_priority,
             content_hash, now, target_slug),
        )
        changed_rows = target_cur.rowcount
        source_rows = 0
        if archive_source:
            source_cur = conn.execute(
                "UPDATE memories SET priority = 'archived', updated_at = ? WHERE slug = ?",
                (now, source_slug),
            )
            source_rows = source_cur.rowcount
        changed_rows += source_rows
        if changed_rows <= 0:
            conn.rollback()
            return MemoryMutationResult(False, "merge", target_slug, changed_rows=changed_rows, backup_path=target_backup, error="no rows changed")
        audit_details = {
            "changed_rows": changed_rows,
            "source_slug": source_slug,
            "target_version_id": target_backup,
            "source_version_id": source_backup,
            "source_archived": archive_source,
        }
        audit_id = _record_audit(
            action="merge",
            target_slug=target_slug,
            reason=f"merge from {source_slug}",
            backup_path=target_backup,
            details=audit_details,
            commit=False,
        )
        if not audit_id:
            conn.rollback()
            return MemoryMutationResult(False, "merge", target_slug, changed_rows=changed_rows, backup_path=target_backup, error="audit write failed")
        conn.commit()
        return MemoryMutationResult(
            True,
            "merge",
            target_slug,
            changed_rows=changed_rows,
            version_id=target_backup,
            audit_id=audit_id,
            backup_path=target_backup,
            details=audit_details,
        )
    except Exception as exc:
        conn.rollback()
        return MemoryMutationResult(False, "merge", target_slug, error=str(exc))


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


def restore_memory_from_history_result(slug: str, version: str, reason: str = "") -> MemoryMutationResult:
    """Restore a memory snapshot from memory_versions, recreating deleted rows."""
    record = read_history_record(slug, version)
    if not record:
        return MemoryMutationResult(False, "rollback_restore", slug, error="history version not found")

    target_slug = str(record.get("slug") or slug).strip().lower().replace(" ", "-")[:50] or "memory"
    body = record.get("content") or ""
    description = record.get("description") or ""
    mem_type = _valid_type(record.get("mem_type") or "profile")
    priority = _valid_priority(record.get("priority") or "active")
    event_date = record.get("event_date")

    if get_memory(target_slug):
        return replace_memory_result(
            slug=target_slug,
            description=description,
            body=body,
            mem_type=mem_type,
            priority=priority,
            event_date=event_date,
            reason=reason or f"restore version {version}",
            audit_action="rollback_restore",
            details={"restored_from": f"version://{version}"},
        )

    conn = connect()
    now = _now()
    memory_id = record.get("memory_id") or str(uuid.uuid4())
    if conn.execute("SELECT 1 FROM memories WHERE id = ?", (memory_id,)).fetchone():
        memory_id = str(uuid.uuid4())

    try:
        cur = conn.execute(
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
        changed_rows = cur.rowcount
        if changed_rows <= 0:
            conn.rollback()
            return MemoryMutationResult(False, "rollback_restore", target_slug, changed_rows=changed_rows, error="no rows changed")
        audit_details = {"changed_rows": changed_rows, "restored_from": f"version://{version}"}
        audit_id = _record_audit(
            action="rollback_restore",
            target_slug=target_slug,
            reason=reason or f"restore version {version}",
            details=audit_details,
            commit=False,
        )
        if not audit_id:
            conn.rollback()
            return MemoryMutationResult(False, "rollback_restore", target_slug, changed_rows=changed_rows, error="audit write failed")
        conn.commit()
        return MemoryMutationResult(
            True,
            "rollback_restore",
            target_slug,
            changed_rows=changed_rows,
            audit_id=audit_id,
            details=audit_details,
        )
    except Exception as exc:
        conn.rollback()
        return MemoryMutationResult(False, "rollback_restore", target_slug, error=str(exc))

