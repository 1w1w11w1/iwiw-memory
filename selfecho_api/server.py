from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.responses import FileResponse

from memory_agent.llm import complete_text
from memory_agent.store import (
    MEMORY_DIR,
    archive_memory,
    delete_memory_file,
    list_history,
    list_memories,
    merge_memory_files,
    read_history,
    read_memory,
    read_memory_full,
    rebuild_index,
    replace_memory,
)
from selfecho_session import SessionMemoryService
from selfecho_session.config import DB_PATH, LEGACY_DB_PATH

from .model_config import list_model_templates, list_providers, save_providers, test_provider
from .prompt_service import list_prompts, preview_prompt, read_prompt, write_prompt

ROOT = Path(__file__).resolve().parents[1]
WEB_DIST = ROOT / "web" / "dist"

app = FastAPI(title="SelfEcho API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

session_service = SessionMemoryService()


class ChatMessageRequest(BaseModel):
    message: str
    private: bool = False


class MemoryEditRequest(BaseModel):
    description: str
    body: str
    mem_type: str = "user"
    priority: str = "normal"
    event_date: str | None = None
    reason: str = "GUI edit"


class MemoryMergeRequest(BaseModel):
    target_slug: str
    source_slug: str
    merged_body: str
    description: str
    priority: str | None = None
    mem_type: str | None = None


class PromptWriteRequest(BaseModel):
    content: str


class PromptPreviewRequest(BaseModel):
    user_message: str = ""


class ProvidersWriteRequest(BaseModel):
    providers: list[dict[str, Any]]


def _diff(old: str, new: str) -> str:
    return "\n".join(difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile="old",
        tofile="new",
        lineterm="",
    ))


def _latest_backup(slug: str) -> str | None:
    history = list_history(slug)
    return history[0]["path"] if history else None


def _audit(
    *,
    action: str,
    target_slug: str | None,
    reason: str,
    backup_path: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session_service._record_audit(
        action=action,
        target_slug=target_slug,
        source_session_id=None,
        cycle_no=None,
        reason=reason,
        backup_path=backup_path,
        details=details or {},
    )
    session_service.conn.commit()


def _core_important_context() -> str:
    parts = []
    for priority in ("core", "important"):
        for mem in list_memories(priority=priority):
            body = read_memory(mem["slug"]) or ""
            parts.append(f"## {mem['slug']} ({priority})\n{body[:1800]}")
    return "\n\n".join(parts)


async def _reply(session_id: str, message: str) -> str:
    from .prompt_service import read_prompt
    prompt = "\n\n".join([
        read_prompt("treehole_reply"),
        "## L0/L1 记忆",
        _core_important_context(),
        "## 当前会话上下文",
        session_service.recent_context(session_id),
    ])
    try:
        return await complete_text(
            system_prompt=prompt,
            user_prompt=message,
            max_tokens=1200,
            temperature=0.7,
            timeout=30,
        )
    except Exception as exc:
        return (
            "我先把这句话接住。当前模型连接不可用，所以我没法生成完整回复；"
            f"但你的原始消息已经保存在本地会话里。错误信息：{exc}"
        )


@app.get("/api/health")
def health():
    memories = list_memories()
    return {
        "ok": True,
        "memory": {
            "count": len(memories),
            "priority": {p: len([m for m in memories if m.get("priority") == p]) for p in ["core", "important", "normal", "archive"]},
        },
        "session_memory": {
            "db_path": str(DB_PATH),
            "exists": DB_PATH.exists(),
            "sessions": len(session_service.list_sessions(limit=100000)),
        },
        "legacy_import": {
            "available": LEGACY_DB_PATH.exists(),
        },
        "providers": list_providers(),
        "prompts": list_prompts(),
    }


@app.post("/api/chat/sessions")
def create_chat_session():
    return session_service.create_session()


@app.get("/api/chat/sessions")
def list_chat_sessions(source: str | None = None):
    return {"sessions": session_service.list_sessions(source=source)}


@app.post("/api/chat/{session_id}/message")
async def chat_message(session_id: str, req: ChatMessageRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is empty")
    session_service.append_message(session_id, "user", req.message, private=req.private)
    answer = await _reply(session_id, req.message)
    session_service.append_message(session_id, "assistant", answer, private=req.private)
    session = session_service.get_session(session_id)
    consolidation = None
    if session and not req.private and int(session["turn_count"]) % 12 == 0:
        result = session_service.consolidate(session_id)
        consolidation = result.__dict__
    return {"answer": answer, "session": session_service.get_session(session_id), "consolidation": consolidation}


@app.post("/api/chat/{session_id}/consolidate")
def consolidate(session_id: str):
    result = session_service.consolidate(session_id, force=True)
    return result.__dict__


@app.get("/api/chat/{session_id}/summary")
def get_chat_summary(session_id: str):
    return session_service.get_summary(session_id)


@app.get("/api/session-memory/search")
def search_session_memory(q: str, source: str | None = None, limit: int = 20):
    return {"results": session_service.search(q, limit=limit, source=source)}


@app.get("/api/session-memory/{session_id}")
def replay_session(session_id: str):
    session = session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return {"session": session, "messages": session_service.get_messages(session["id"])}


@app.post("/api/session-memory/migrate-legacy")
def migrate_legacy():
    return session_service.migrate_legacy()


@app.get("/api/memories")
def memories(priority: str | None = None):
    return {"memories": list_memories(priority=priority)}


@app.get("/api/memories/search")
def search_memories_api(q: str, limit: int = 20):
    from memory_agent.search import search_memories
    return {"results": search_memories(q, top_k=limit)}


@app.get("/api/memories/{slug}")
def memory_detail(slug: str):
    item = read_memory_full(slug)
    if not item:
        raise HTTPException(status_code=404, detail="memory not found")
    item["history"] = list_history(slug)
    return item


@app.put("/api/memories/{slug}")
def edit_memory(slug: str, req: MemoryEditRequest):
    old = read_memory_full(slug)
    if not old:
        raise HTTPException(status_code=404, detail="memory not found")
    path = replace_memory(
        slug=slug,
        description=req.description,
        body=req.body,
        mem_type=req.mem_type,
        priority=req.priority,
        event_date=req.event_date,
        reason=req.reason,
    )
    _audit(
        action="manual_edit",
        target_slug=slug,
        reason=req.reason,
        backup_path=_latest_backup(slug),
        details={"priority": req.priority, "type": req.mem_type},
    )
    return {"ok": True, "path": str(path) if path else None, "diff": _diff(old["body"], req.body), "memory": read_memory_full(slug)}


@app.post("/api/memories/{slug}/archive")
def archive_memory_api(slug: str):
    path = archive_memory(slug)
    _audit(action="archive", target_slug=slug, reason="GUI archive", backup_path=_latest_backup(slug))
    return {"ok": bool(path), "memory": read_memory_full(slug)}


@app.delete("/api/memories/{slug}")
def delete_memory_api(slug: str):
    path = delete_memory_file(slug)
    _audit(action="delete", target_slug=slug, reason="GUI delete", backup_path=_latest_backup(slug))
    return {"ok": bool(path), "deleted": slug}


@app.post("/api/memories/merge")
def merge_memory_api(req: MemoryMergeRequest):
    old_target = read_memory_full(req.target_slug)
    path = merge_memory_files(
        target_slug=req.target_slug,
        source_slug=req.source_slug,
        merged_body=req.merged_body,
        description=req.description,
        priority=req.priority,
        mem_type=req.mem_type,
    )
    _audit(
        action="merge",
        target_slug=req.target_slug,
        reason=f"GUI merge from {req.source_slug}",
        backup_path=_latest_backup(req.target_slug),
        details={
            "source_slug": req.source_slug,
            "target_diff": _diff(old_target["body"], req.merged_body) if old_target else "",
        },
    )
    return {"ok": bool(path), "target": read_memory_full(req.target_slug), "source": read_memory_full(req.source_slug)}


@app.post("/api/memories/rebuild-index")
def rebuild_memory_index_api():
    rebuild_index()
    return {"ok": True}


@app.get("/api/memories/{slug}/history/{version}")
def memory_history(slug: str, version: str):
    text = read_history(slug, version)
    if text is None:
        raise HTTPException(status_code=404, detail="history not found")
    return {"slug": slug, "version": version, "content": text}


@app.get("/api/memory-audit")
def memory_audit(limit: int = 100):
    return {"events": session_service.audit_events(limit=limit)}


@app.post("/api/memory-audit/{audit_id}/rollback")
def rollback_audit(audit_id: str):
    row = session_service.conn.execute(
        "SELECT * FROM memory_audit WHERE id = ?",
        (audit_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="audit event not found")
    event = dict(row)
    slug = event.get("target_slug")
    backup = event.get("backup_path")
    if not slug or not backup:
        raise HTTPException(status_code=400, detail="audit event has no restorable backup")
    backup_path = Path(backup)
    if not backup_path.exists():
        raise HTTPException(status_code=404, detail="backup file not found")
    target = MEMORY_DIR / f"{Path(slug).stem}.md"
    if target.exists():
        current = target.read_text(encoding="utf-8")
        archive_dir = MEMORY_DIR / ".history" / target.stem
        archive_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        archive_path = archive_dir / f"rollback-current-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        archive_path.write_text(current, encoding="utf-8")
    target.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
    rebuild_index()
    _audit(
        action="rollback",
        target_slug=slug,
        reason=f"Rollback audit event {audit_id}",
        backup_path=str(backup_path),
        details={"rolled_back_event": audit_id},
    )
    return {"ok": True, "memory": read_memory_full(slug)}


@app.get("/api/prompts")
def prompts():
    return {"prompts": list_prompts()}


@app.get("/api/prompts/{name}")
def prompt_detail(name: str):
    try:
        return {"name": name, "content": read_prompt(name)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.put("/api/prompts/{name}")
def prompt_write(name: str, req: PromptWriteRequest):
    try:
        return {"name": name, "content": write_prompt(name, req.content)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/prompts/preview")
def prompt_preview(req: PromptPreviewRequest):
    return {"preview": preview_prompt(req.user_message)}


@app.get("/api/models/providers")
def model_providers():
    return list_providers()


@app.get("/api/models/templates")
def model_templates():
    return list_model_templates()


@app.put("/api/models/providers")
def model_providers_write(req: ProvidersWriteRequest):
    return save_providers(req.providers)


@app.post("/api/models/providers/{provider_id}/test")
async def model_provider_test(provider_id: str):
    return await test_provider(provider_id)


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(WEB_DIST / "index.html")


def main() -> None:
    uvicorn.run("selfecho_api.server:app", host="127.0.0.1", port=8765, reload=False)
