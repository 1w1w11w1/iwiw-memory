"""
store.py — 记忆文件读写 + Hash 去重（ADD-only 模式）

设计原则（来自深度研究）：
- ADD-only：只追加新文件，不修改/删除已有文件
- Hash 去重：新内容 MD5 比对已有记忆，重复则跳过
- 双时间轴：event_date（事实发生时间）+ recorded_date（记录时间）
- 分层：core / important / normal / archive
"""

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config import (
    MEMORY_DIR,
    MEMORY_INDEX,
    MEMORY_FRONTMATTER_TEMPLATE,
    HASH_ALGORITHM,
)

VALID_PRIORITIES = {"core", "important", "normal", "archive"}
VALID_TYPES = {"user", "feedback", "project", "reference"}

TIER_HEADINGS = [
    ("core", "L0 · Core（始终加载）"),
    ("important", "L1 · Important（始终加载）"),
    ("normal", "L2 · Normal（按话题触发）"),
    ("archive", "L3 · Archive（深度检索按需）"),
]


def _ensure_dir() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _slugify(text: str, max_len: int = 50) -> str:
    """把中文/英文文本转成合法的文件名 slug"""
    # 保留中英文字符、数字、连字符
    slug = re.sub(r"[^\w一-鿿-]", "-", text.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:max_len] if slug else "memory"


def _compute_hash(content: str) -> str:
    """计算内容 hash（用于去重）"""
    h = hashlib.new(HASH_ALGORITHM)
    h.update(content.encode("utf-8"))
    return h.hexdigest()


def _normalize_priority(priority: Optional[str]) -> str:
    value = (priority or "normal").strip().lower()
    return value if value in VALID_PRIORITIES else "normal"


def _normalize_type(mem_type: Optional[str]) -> str:
    value = (mem_type or "user").strip().lower()
    return value if value in VALID_TYPES else "user"


def _clean_inline(text: str, fallback: str = "") -> str:
    text = (text or fallback or "").replace("\r", " ").replace("\n", " ").strip()
    return re.sub(r"\s+", " ", text)


def _split_frontmatter(text: str) -> tuple[str, str]:
    match = re.match(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n?", text)
    if not match:
        return "", text
    return match.group(1), text[match.end():]


def _extract_yaml_field(filepath: Path, field: str) -> Optional[str]:
    """从记忆文件 frontmatter 中提取字段值（兼容缩进和非缩进 YAML）"""
    try:
        text = filepath.read_text(encoding="utf-8")
        match = re.search(rf"^\s*{field}:\s*(.+)$", text, re.MULTILINE)
        return match.group(1).strip() if match else None
    except Exception:
        return None


def get_existing_hashes() -> dict[str, Path]:
    """
    扫描 memory/ 下所有 .md 文件，返回 {hash: filepath} 映射
    """
    _ensure_dir()
    hashes: dict[str, Path] = {}
    for f in sorted(MEMORY_DIR.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        h = _extract_yaml_field(f, "hash")
        if h:
            hashes[h] = f
    return hashes


def get_existing_slugs() -> set[str]:
    """返回所有已有记忆文件的 slug 集合"""
    _ensure_dir()
    slugs: set[str] = set()
    for f in sorted(MEMORY_DIR.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        slugs.add(f.stem)
    return slugs


def write_memory(
    *,
    slug: str,
    description: str,
    content: str,
    mem_type: str = "user",
    priority: str = "normal",
    event_date: Optional[str] = None,
) -> Optional[Path]:
    """
    ADD-only 写入：
    1. 计算 content hash → 比对已有记忆，重复则返回 None
    2. 生成 frontmatter + body
    3. 写入新 .md 文件
    4. 更新 MEMORY.md 索引
    返回写入的文件路径，或 None（重复跳过）
    """
    _ensure_dir()

    # ── Hash 去重 ──
    content_hash = _compute_hash(content)
    existing_hashes = get_existing_hashes()
    if content_hash in existing_hashes:
        return None  # 完全重复，跳过

    # ── 确保 slug 唯一 ──
    existing_slugs = get_existing_slugs()
    base_slug = _slugify(slug)
    final_slug = base_slug
    counter = 1
    while final_slug in existing_slugs:
        final_slug = f"{base_slug}-{counter}"
        counter += 1

    priority = _normalize_priority(priority)
    mem_type = _normalize_type(mem_type)
    description = _clean_inline(description, fallback=final_slug)

    # ── 时间戳 ──
    now = datetime.now()
    recorded_date_str = now.strftime("%Y-%m-%d")
    event_date_str = event_date or recorded_date_str

    # ── 构建 frontmatter ──
    frontmatter = MEMORY_FRONTMATTER_TEMPLATE.format(
        slug=final_slug,
        description=description,
        mem_type=mem_type,
        priority=priority,
        event_date=event_date_str,
        recorded_date=recorded_date_str,
        content_hash=content_hash,
    )

    # ── 写入文件 ──
    filepath = MEMORY_DIR / f"{final_slug}.md"
    full_content = f"{frontmatter}\n{content}\n"
    filepath.write_text(full_content, encoding="utf-8")

    # ── 更新索引 ──
    rebuild_index()

    return filepath


def update_memory(
    *,
    target_slug: str,
    description: str,
    content: str,
    mem_type: Optional[str] = None,
    priority: Optional[str] = None,
    event_date: Optional[str] = None,
) -> Optional[Path]:
    """
    更新已有记忆。

    更新前会把旧文件备份到 memory/.history/<slug>/，避免自动化误写后无法回退。
    """
    _ensure_dir()
    safe_slug = Path(target_slug).stem
    filepath = MEMORY_DIR / f"{safe_slug}.md"
    if not filepath.exists():
        return write_memory(
            slug=safe_slug or "memory",
            description=description,
            content=content,
            mem_type=mem_type or "user",
            priority=priority or "normal",
            event_date=event_date,
        )

    old_text = filepath.read_text(encoding="utf-8")
    _, old_body = _split_frontmatter(old_text)
    # 计算新正文：旧 body + 追加的新内容（而非替换）
    from datetime import datetime as dt
    timestamp = dt.now().strftime("%Y-%m-%d %H:%M")
    merged_body = f"{old_body.strip()}\n\n（以下为 {timestamp} 追加）\n\n{content.strip()}\n"
    new_hash = _compute_hash(merged_body)
    old_hash = _extract_yaml_field(filepath, "hash")
    if old_hash == new_hash:
        return None

    _archive_previous(filepath, old_text)

    description = _clean_inline(description, fallback=_extract_yaml_field(filepath, "description") or safe_slug)
    priority = _normalize_priority(priority or _extract_yaml_field(filepath, "priority"))
    mem_type = _normalize_type(mem_type or _extract_yaml_field(filepath, "type"))
    recorded_date_str = _extract_yaml_field(filepath, "recorded_date") or datetime.now().strftime("%Y-%m-%d")
    event_date_str = event_date or _extract_yaml_field(filepath, "event_date") or recorded_date_str

    frontmatter = MEMORY_FRONTMATTER_TEMPLATE.format(
        slug=safe_slug,
        description=description,
        mem_type=mem_type,
        priority=priority,
        event_date=event_date_str,
        recorded_date=recorded_date_str,
        content_hash=new_hash,
    )
    filepath.write_text(f"{frontmatter}\n{merged_body}\n", encoding="utf-8")
    rebuild_index()
    return filepath


def save_memory_candidate(candidate: dict[str, Any]) -> Optional[tuple[str, Path]]:
    """保存 LLM 返回的一条 create/update 动作。"""
    action = (candidate.get("action") or "create").strip().lower()
    if action == "ignore":
        return None

    if action == "update" and candidate.get("target_slug"):
        path = update_memory(
            target_slug=candidate.get("target_slug", ""),
            description=candidate.get("description", ""),
            content=candidate.get("content", ""),
            mem_type=candidate.get("mem_type"),
            priority=candidate.get("priority"),
            event_date=candidate.get("event_date"),
        )
        return ("update", path) if path else None

    path = write_memory(
        slug=candidate.get("slug", "memory"),
        description=candidate.get("description", ""),
        content=candidate.get("content", ""),
        mem_type=candidate.get("mem_type", "user"),
        priority=candidate.get("priority", "normal"),
        event_date=candidate.get("event_date"),
    )
    return ("create", path) if path else None


def _archive_previous(filepath: Path, text: str) -> None:
    archive_dir = MEMORY_DIR / ".history" / filepath.stem
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = archive_dir / f"{stamp}.md"
    archive_path.write_text(text, encoding="utf-8")


def rebuild_index() -> None:
    """根据所有记忆文件的 frontmatter 重建 MEMORY.md。"""
    MEMORY_INDEX.parent.mkdir(parents=True, exist_ok=True)
    memories = list_memories()
    grouped: dict[str, list[dict[str, Any]]] = {priority: [] for priority, _ in TIER_HEADINGS}
    for mem in memories:
        priority = _normalize_priority(mem.get("priority"))
        grouped.setdefault(priority, []).append(mem)

    lines = [
        "# 记忆索引",
        "",
        "> Markdown 长期记忆是真源；GUI 会话和外部输入会在整理后生成候选记忆变更。",
        ">",
        "> **启动时**：读取本索引，然后加载 L0（core）和 L1（important）记忆完整内容；L2/L3 按话题检索。",
        "",
    ]

    for priority, heading in TIER_HEADINGS:
        lines.append(f"## {heading}")
        lines.append("")
        items = sorted(grouped.get(priority, []), key=lambda m: m.get("slug", ""))
        if items:
            for mem in items:
                slug = mem.get("slug", "")
                description = _clean_inline(mem.get("description", ""), fallback=slug)
                label = _display_label(slug)
                lines.append(f"- [{label}]({slug}.md) — {description}")
        else:
            lines.append("- （暂无）")
        lines.append("")

    MEMORY_INDEX.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _display_label(slug: str) -> str:
    return slug.replace("_", " ")


def read_memory(slug: str) -> Optional[str]:
    """读取指定记忆文件内容（不含 frontmatter）"""
    filepath = MEMORY_DIR / f"{slug}.md"
    if not filepath.exists():
        return None
    text = filepath.read_text(encoding="utf-8")
    # 去掉 YAML frontmatter
    _, body = _split_frontmatter(text)
    body = body.strip()
    return body


def read_memory_full(slug: str) -> Optional[dict[str, Any]]:
    """读取完整记忆，返回 frontmatter、正文和文件元信息。"""
    safe_slug = Path(slug).stem
    filepath = MEMORY_DIR / f"{safe_slug}.md"
    if not filepath.exists() or filepath.name == "MEMORY.md":
        return None
    text = filepath.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    return {
        "slug": safe_slug,
        "path": str(filepath),
        "frontmatter": frontmatter,
        "body": body.strip(),
        "description": _extract_yaml_field(filepath, "description") or "",
        "priority": _extract_yaml_field(filepath, "priority") or "normal",
        "type": _extract_yaml_field(filepath, "type") or "unknown",
        "event_date": _extract_yaml_field(filepath, "event_date") or "",
        "recorded_date": _extract_yaml_field(filepath, "recorded_date") or "",
        "hash": _extract_yaml_field(filepath, "hash") or "",
        "mtime": datetime.fromtimestamp(filepath.stat().st_mtime).isoformat(),
        "size": filepath.stat().st_size,
    }


def replace_memory(
    *,
    slug: str,
    description: str,
    body: str,
    mem_type: str = "user",
    priority: str = "normal",
    event_date: Optional[str] = None,
    reason: str = "manual edit",
) -> Optional[Path]:
    """人工覆盖编辑记忆。保存前备份旧版本，然后重建索引。"""
    _ensure_dir()
    safe_slug = Path(slug).stem
    if not safe_slug or safe_slug == "MEMORY":
        raise ValueError("Invalid memory slug")
    filepath = MEMORY_DIR / f"{safe_slug}.md"
    if not filepath.exists():
        raise FileNotFoundError(f"Memory not found: {safe_slug}")

    old_text = filepath.read_text(encoding="utf-8")
    _, old_body = _split_frontmatter(old_text)
    if old_body.strip() == body.strip():
        return None

    _archive_previous(filepath, old_text)

    priority = _normalize_priority(priority)
    mem_type = _normalize_type(mem_type)
    description = _clean_inline(description, fallback=safe_slug)
    recorded_date_str = _extract_yaml_field(filepath, "recorded_date") or datetime.now().strftime("%Y-%m-%d")
    event_date_str = event_date or _extract_yaml_field(filepath, "event_date") or recorded_date_str
    new_hash = _compute_hash(body.strip())
    frontmatter = MEMORY_FRONTMATTER_TEMPLATE.format(
        slug=safe_slug,
        description=description,
        mem_type=mem_type,
        priority=priority,
        event_date=event_date_str,
        recorded_date=recorded_date_str,
        content_hash=new_hash,
    )
    filepath.write_text(f"{frontmatter}\n{body.strip()}\n", encoding="utf-8")
    rebuild_index()
    return filepath


def archive_memory(slug: str) -> Optional[Path]:
    """把记忆降级为 archive。"""
    full = read_memory_full(slug)
    if not full:
        return None
    return replace_memory(
        slug=full["slug"],
        description=full["description"],
        body=full["body"],
        mem_type=full["type"],
        priority="archive",
        event_date=full["event_date"],
        reason="archive",
    )


def delete_memory_file(slug: str) -> Optional[Path]:
    """删除记忆文件。删除前备份到 .history。"""
    safe_slug = Path(slug).stem
    filepath = MEMORY_DIR / f"{safe_slug}.md"
    if not filepath.exists() or filepath.name == "MEMORY.md":
        return None
    old_text = filepath.read_text(encoding="utf-8")
    _archive_previous(filepath, old_text)
    filepath.unlink()
    rebuild_index()
    return filepath


def merge_memory_files(
    *,
    target_slug: str,
    source_slug: str,
    merged_body: str,
    description: str,
    priority: Optional[str] = None,
    mem_type: Optional[str] = None,
    archive_source: bool = True,
) -> Optional[Path]:
    """合并两条记忆：覆盖目标，默认归档来源。"""
    target = read_memory_full(target_slug)
    source = read_memory_full(source_slug)
    if not target or not source:
        raise FileNotFoundError("Target or source memory not found")
    path = replace_memory(
        slug=target["slug"],
        description=description,
        body=merged_body,
        mem_type=mem_type or target["type"],
        priority=priority or target["priority"],
        event_date=target["event_date"],
        reason=f"merge from {source['slug']}",
    )
    if archive_source:
        archive_memory(source["slug"])
    return path


def list_history(slug: str) -> list[dict[str, Any]]:
    """列出某条记忆的历史备份。"""
    safe_slug = Path(slug).stem
    archive_dir = MEMORY_DIR / ".history" / safe_slug
    if not archive_dir.exists():
        return []
    rows = []
    for f in sorted(archive_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        rows.append({
            "version": f.stem,
            "path": str(f),
            "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            "size": f.stat().st_size,
        })
    return rows


def read_history(slug: str, version: str) -> Optional[str]:
    """读取某条历史备份全文。"""
    safe_slug = Path(slug).stem
    safe_version = Path(version).stem
    path = MEMORY_DIR / ".history" / safe_slug / f"{safe_version}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def list_memories(priority: Optional[str] = None) -> list[dict[str, Any]]:
    """列出所有记忆文件，可选按 priority 过滤"""
    _ensure_dir()
    results: list[dict[str, Any]] = []
    for f in sorted(MEMORY_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.name == "MEMORY.md":
            continue
        mem = {
            "slug": f.stem,
            "path": str(f),
            "size": f.stat().st_size,
            "mtime": datetime.fromtimestamp(f.stat().st_mtime),
            "priority": _extract_yaml_field(f, "priority") or "normal",
            "type": _extract_yaml_field(f, "type") or "unknown",
            "description": _extract_yaml_field(f, "description") or "",
        }
        if priority and mem["priority"] != priority:
            continue
        results.append(mem)
    return results


def get_all_content_for_search() -> list[dict[str, Any]]:
    """获取所有记忆文件的原始内容，供搜索使用"""
    _ensure_dir()
    docs: list[dict[str, Any]] = []
    for f in sorted(MEMORY_DIR.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        body = read_memory(f.stem)
        if body:
            docs.append({
                "slug": f.stem,
                "content": body,
                "description": _extract_yaml_field(f, "description") or "",
                "priority": _extract_yaml_field(f, "priority") or "normal",
                "type": _extract_yaml_field(f, "type") or "unknown",
                "mtime": datetime.fromtimestamp(f.stat().st_mtime),
            })
    return docs
