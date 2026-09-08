import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MEOW_DB = r"C:\Users\YS\.dsh-meow\memory.db"
LEVELS = ["soul", "user", "project", "fact", "lesson", "topic", "rules"]


def ms_to_iso(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def make_slug(level, meow_id):
    h = hashlib.md5(meow_id.encode("utf-8")).hexdigest()[:8]
    return "meow-{}-{}".format(level, h)


def make_description(row):
    if row.get("title"):
        return str(row["title"])[:80]
    return (row.get("content") or "").strip().replace("\n", " ")[:60]


def make_content(row):
    body = (row.get("content") or "").strip()
    try:
        kws = json.loads(row.get("keywords") or "[]")
        if kws:
            body += "\n\n关键词: " + ", ".join(kws)
    except json.JSONDecodeError:
        pass
    return body


def map_type_level(level):
    mem_type = {"project": "project", "lesson": "feedback"}.get(level, "user")
    priority = "core" if level in ("rules", "user") else "normal"
    return mem_type, priority


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--meow-db", default=MEOW_DB)
    args = ap.parse_args()

    meow = sqlite3.connect(args.meow_db)
    meow.row_factory = sqlite3.Row

    from memory_agent.db import upsert_memory

    migrated = 0
    for level in LEVELS:
        try:
            rows = meow.execute(
                "SELECT * FROM {} WHERE status = 'active' ORDER BY updated_at".format(level)
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            r = dict(row)
            slug = make_slug(level, r["id"])
            description = make_description(r)
            content = make_content(r)
            mem_type, priority = map_type_level(level)
            metadata = {
                "source": "meow-migration",
                "meow_id": r["id"],
                "meow_level": level,
                "meow_importance": r.get("importance"),
                "meow_keywords": json.loads(r.get("keywords") or "[]"),
                "meow_project": r.get("project"),
                "meow_hit_count": r.get("hit_count"),
            }
            if args.dry_run:
                print("[{}] {} | {}".format(level, slug, description))
                continue
            upsert_memory(
                slug=slug,
                description=description,
                content=content,
                mem_type=mem_type,
                priority=priority,
                recorded_date=ms_to_iso(r.get("created_at")),
                metadata=metadata,
            )
            migrated += 1
    meow.close()

    if args.dry_run:
        print("\n(dry-run) 预览结束，未写库")
        return 0
    print("\n迁移完成: {} 条写入".format(migrated))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
