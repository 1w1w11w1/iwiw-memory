from __future__ import annotations

import difflib
import json
import mimetypes
import os
import string
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.responses import FileResponse, StreamingResponse

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
from memory_agent.db import (
    get_memory,
    list_memories as db_list_memories,
    list_pending_actions,
    approve_pending_action,
    reject_pending_action,
    get_stats,
)
from selfecho_agent import AgentResponse, AgentRunner
from selfecho_session import SessionMemoryService
from selfecho_session.config import DB_PATH, LEGACY_DB_PATH

from .model_config import list_model_templates, list_providers, save_providers, test_provider, test_provider_config
from .prompt_service import list_prompts, preview_prompt, read_prompt, write_prompt

ROOT = Path(__file__).resolve().parents[1]
WEB_DIST = ROOT / "web" / "dist"
PROJECT_FAVORITES_PATH = ROOT / "selfecho_data" / "project_favorites.json"
WORKSPACE_EXCLUDE_DIRS = {
    ".git",
    ".claude",
    ".codex",
    "__pycache__",
    "node_modules",
    "dist",
}
WORKSPACE_PRIVATE_DIRS = {
    "memory",
    "selfecho_data",
}
WORKSPACE_EXCLUDE_NAMES = {
    ".env",
    "providers.local.json",
}
WORKSPACE_EXCLUDE_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pdf",
    ".mp3",
    ".wav",
    ".pyc",
    ".pyo",
    ".log",
}
TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".py",
    ".ts",
    ".vue",
    ".js",
    ".json",
    ".html",
    ".css",
    ".toml",
    ".yaml",
    ".yml",
    ".gitignore",
}

app = FastAPI(title="IwIw API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

session_service = SessionMemoryService()
agent_runner = AgentRunner(session_service, read_prompt)


class ChatMessageRequest(BaseModel):
    message: str
    private: bool = False


class ChatSessionCreateRequest(BaseModel):
    title: str | None = None
    project_id: str | None = None
    scope: str = "project"


class ChatSessionUpdateRequest(BaseModel):
    title: str | None = None
    project_id: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


class ChatProjectCreateRequest(BaseModel):
    name: str
    path: str = ""


class ChatProjectUpdateRequest(BaseModel):
    name: str | None = None
    path: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


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
    active_provider_id: str | None = None


class ProviderTestRequest(BaseModel):
    provider: dict[str, Any]


class WorkspaceFileWriteRequest(BaseModel):
    content: str


class FileSystemDirectoryCreateRequest(BaseModel):
    parent: str = ""
    name: str | None = None


class FileSystemDirectoryRenameRequest(BaseModel):
    path: str
    name: str


class FileSystemDirectoryDeleteRequest(BaseModel):
    path: str


class FileSystemDirectoryFavoriteRequest(BaseModel):
    path: str


class FileSystemDirectoryPickRequest(BaseModel):
    initial: str = ""


class FileSystemDirectoryRevealRequest(BaseModel):
    path: str


def _workspace_root(project_id: str | None = None) -> Path:
    if project_id and project_id.strip():
        project = session_service.get_project(project_id.strip())
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        raw_path = str(project.get("path") or "").strip()
        if not raw_path:
            raise HTTPException(status_code=400, detail="project has no workspace path")
        root = Path(raw_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise HTTPException(status_code=404, detail="project workspace not found")
        return root
    return ROOT


def _workspace_path(relative: str | None = None, project_id: str | None = None) -> Path:
    root = _workspace_root(project_id)
    rel = (relative or "").strip().replace("\\", "/")
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="path outside workspace")
    if _is_excluded_path(target, root):
        raise HTTPException(status_code=403, detail="path is private or ignored")
    return target


def _is_excluded_path(path: Path, root: Path | None = None) -> bool:
    base = root or ROOT
    try:
        rel_parts = path.resolve().relative_to(base).parts
    except ValueError:
        return True
    for part in rel_parts:
        if part in WORKSPACE_EXCLUDE_DIRS or (base == ROOT and part in WORKSPACE_PRIVATE_DIRS):
            return True
    name = path.name
    if name in WORKSPACE_EXCLUDE_NAMES:
        return True
    if path.suffix.lower() in WORKSPACE_EXCLUDE_SUFFIXES:
        return True
    return False


def _rel(path: Path, root: Path | None = None) -> str:
    base = root or ROOT
    value = path.resolve().relative_to(base).as_posix()
    return "" if value == "." else value


def _file_item(path: Path, root: Path | None = None) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": _rel(path, root),
        "kind": "directory" if path.is_dir() else "file",
        "size": stat.st_size if path.is_file() else None,
        "updated_at": stat.st_mtime,
    }


def _fs_path(value: str) -> Path:
    raw = (value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="path is required")
    target = Path(raw).expanduser().resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail="path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="path is not a directory")
    return target


def _fs_dir_item(path: Path, label: str | None = None, favorite: bool = False) -> dict[str, Any]:
    try:
        stat = path.stat()
        updated_at = stat.st_mtime
    except OSError:
        updated_at = None
    name = label or path.name or str(path)
    return {
        "name": name,
        "path": str(path),
        "kind": "directory",
        "size": None,
        "updated_at": updated_at,
        "favorite": favorite,
    }


def _read_project_favorites() -> list[str]:
    if not PROJECT_FAVORITES_PATH.exists():
        return []
    try:
        data = json.loads(PROJECT_FAVORITES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    favorites: list[str] = []
    seen: set[str] = set()
    for raw in data:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            path = Path(raw).expanduser().resolve()
        except OSError:
            continue
        key = str(path)
        if key not in seen and path.exists() and path.is_dir():
            favorites.append(key)
            seen.add(key)
    return favorites


def _write_project_favorites(paths: list[str]) -> None:
    PROJECT_FAVORITES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROJECT_FAVORITES_PATH.write_text(
        json.dumps(paths, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _project_favorite_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in _read_project_favorites():
        path = Path(raw)
        items.append(_fs_dir_item(path, favorite=True))
    return items


def _filesystem_roots() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    items.extend(_project_favorite_items())
    for label, path in [
        ("Desktop", _default_project_parent()),
    ]:
        if path.exists() and path.is_dir():
            items.append(_fs_dir_item(path, label))
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            if letter == "C":
                continue
            drive = Path(f"{letter}:\\")
            if drive.exists():
                items.append(_fs_dir_item(drive, f"{letter}:"))
    else:
        items.append(_fs_dir_item(Path("/"), "/"))
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        if item["path"] not in unique:
            unique[item["path"]] = item
    return list(unique.values())


def _default_project_parent() -> Path:
    if os.name == "nt":
        try:
            import winreg

            registry_paths = [
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            ]
            for registry_path in registry_paths:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
                    raw, _ = winreg.QueryValueEx(key, "Desktop")
                    desktop = Path(os.path.expandvars(str(raw))).expanduser()
                    if desktop.exists() and desktop.is_dir():
                        return desktop
        except OSError:
            pass
    desktop = Path.home() / "Desktop"
    return desktop if desktop.exists() and desktop.is_dir() else Path.home()


def _valid_dir_name(name: str) -> str:
    value = name.strip()
    if not value:
        raise HTTPException(status_code=400, detail="directory name is required")
    if any(sep in value for sep in ("/", "\\")) or value in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid directory name")
    if os.name == "nt" and any(ch in value for ch in '<>:"|?*'):
        raise HTTPException(status_code=400, detail="invalid directory name")
    return value


def _next_project_dir(parent: Path) -> Path:
    index = 1
    while True:
        candidate = parent / f"Project {index}"
        if not candidate.exists():
            return candidate
        index += 1


def _pick_directory(initial: Path) -> str:
    script = r"""
import os
import tkinter as tk
from tkinter import filedialog

initial = os.environ.get("IWIW_INITIAL_DIR", "")
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
root.update()
selected = filedialog.askdirectory(
    parent=root,
    initialdir=initial,
    mustexist=True,
    title="选择 IwIw 工作目录",
)
root.destroy()
print(selected or "", end="")
"""
    env = os.environ.copy()
    env["IWIW_INITIAL_DIR"] = str(initial)
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"directory picker unavailable: {exc}")
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "directory picker failed"
        raise HTTPException(status_code=500, detail=detail)
    return completed.stdout.strip()


def _reveal_directory(path: Path) -> None:
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"file manager unavailable: {exc}")


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


async def _reply(session_id: str, message: str) -> AgentResponse:
    return await agent_runner.respond(session_id, message)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _estimate_tokens(text: str) -> int:
    compact = "".join(str(text or "").split())
    if not compact:
        return 0
    ascii_count = sum(1 for char in compact if char in string.printable)
    cjk_count = len(compact) - ascii_count
    return max(1, round(cjk_count + ascii_count / 4))


def _message_metrics(message: str, answer: str, elapsed_ms: int, trace: dict[str, Any]) -> dict[str, Any]:
    input_tokens = _estimate_tokens(message)
    output_tokens = _estimate_tokens(answer)
    return {
        "run_id": trace.get("run_id"),
        "status": trace.get("status"),
        "thinking_ms": elapsed_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "token_source": "estimate",
    }


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
            "sessions": len(session_service.list_sessions(limit=100000, include_archived=True)),
            "projects": len(session_service.list_projects(include_archived=True)),
        },
        "legacy_import": {
            "available": LEGACY_DB_PATH.exists(),
        },
        "providers": list_providers(),
        "prompts": list_prompts(),
    }


@app.get("/api/workspace/files")
def workspace_files(path: str = "", project_id: str | None = None):
    root = _workspace_root(project_id)
    target = _workspace_path(path, project_id)
    if not target.exists():
        raise HTTPException(status_code=404, detail="path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="path is not a directory")
    children = [
        child
        for child in target.iterdir()
        if not _is_excluded_path(child, root)
    ]
    dirs = sorted([_file_item(child, root) for child in children if child.is_dir()], key=lambda item: item["name"].lower())
    files = sorted([_file_item(child, root) for child in children if child.is_file()], key=lambda item: item["name"].lower())
    return {
        "root": str(root),
        "path": _rel(target, root),
        "directories": dirs,
        "files": files,
    }


@app.get("/api/filesystem/directories")
def filesystem_directories(path: str = ""):
    if not path.strip():
        return {
            "path": "",
            "parent_path": "",
            "default_path": str(_default_project_parent()),
            "directories": _filesystem_roots(),
            "favorites": _read_project_favorites(),
        }
    target = _fs_path(path)
    directories: list[dict[str, Any]] = []
    try:
        children = list(target.iterdir())
    except OSError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    for child in children:
        try:
            if child.is_dir():
                directories.append(_fs_dir_item(child))
        except OSError:
            continue
    directories.sort(key=lambda item: item["name"].lower())
    parent = target.parent
    parent_path = "" if parent == target else str(parent)
    return {
        "path": str(target),
        "parent_path": parent_path,
        "directories": directories,
        "favorites": _read_project_favorites(),
    }


@app.post("/api/filesystem/directory")
def filesystem_directory_create(req: FileSystemDirectoryCreateRequest):
    parent = _fs_path(req.parent) if req.parent.strip() else _default_project_parent()
    if req.name and req.name.strip():
        target = (parent / _valid_dir_name(req.name)).resolve()
    else:
        target = _next_project_dir(parent)
    if target.exists():
        raise HTTPException(status_code=409, detail="directory already exists")
    try:
        target.mkdir()
    except OSError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return _fs_dir_item(target)


@app.patch("/api/filesystem/directory")
def filesystem_directory_rename(req: FileSystemDirectoryRenameRequest):
    target = _fs_path(req.path)
    new_name = _valid_dir_name(req.name)
    next_path = (target.parent / new_name).resolve()
    if next_path.exists() and next_path != target:
        raise HTTPException(status_code=409, detail="directory already exists")
    try:
        target.rename(next_path)
    except OSError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return _fs_dir_item(next_path)


@app.delete("/api/filesystem/directory")
def filesystem_directory_delete(req: FileSystemDirectoryDeleteRequest):
    target = _fs_path(req.path)
    if target.parent == target:
        raise HTTPException(status_code=400, detail="cannot delete filesystem root")
    try:
        if any(target.iterdir()):
            raise HTTPException(status_code=409, detail="directory is not empty")
        target.rmdir()
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {"ok": True, "deleted": str(target)}


@app.post("/api/filesystem/pick-directory")
def filesystem_directory_pick(req: FileSystemDirectoryPickRequest):
    initial = _default_project_parent()
    if req.initial.strip():
        try:
            initial = _fs_path(req.initial)
        except HTTPException:
            initial = _default_project_parent()
    selected = _pick_directory(initial)
    if not selected:
        return {"cancelled": True, "path": ""}
    return {"cancelled": False, **_fs_dir_item(_fs_path(selected))}


@app.post("/api/filesystem/reveal-directory")
def filesystem_directory_reveal(req: FileSystemDirectoryRevealRequest):
    target = _fs_path(req.path)
    _reveal_directory(target)
    return {"ok": True, "path": str(target)}


@app.post("/api/filesystem/favorites")
def filesystem_favorite_add(req: FileSystemDirectoryFavoriteRequest):
    target = _fs_path(req.path)
    favorites = _read_project_favorites()
    value = str(target)
    if value not in favorites:
        favorites.append(value)
        _write_project_favorites(favorites)
    return {"favorites": _project_favorite_items()}


@app.get("/api/workspace/file")
def workspace_file(path: str, project_id: str | None = None):
    root = _workspace_root(project_id)
    target = _workspace_path(path, project_id)
    if not target.exists():
        raise HTTPException(status_code=404, detail="file not found")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="path is not a file")
    if target.stat().st_size > 600_000:
        raise HTTPException(status_code=413, detail="file is too large to preview")
    suffix = target.suffix.lower()
    if suffix not in TEXT_SUFFIXES and target.name not in TEXT_SUFFIXES:
        guessed = mimetypes.guess_type(target.name)[0] or ""
        if not guessed.startswith("text/"):
            raise HTTPException(status_code=415, detail="file type is not previewable")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = target.read_text(encoding="utf-8", errors="replace")
    return {
        "name": target.name,
        "path": _rel(target, root),
        "full_path": str(target),
        "content": content,
        "size": target.stat().st_size,
    }


@app.put("/api/workspace/file")
def workspace_file_write(path: str, req: WorkspaceFileWriteRequest, project_id: str | None = None):
    root = _workspace_root(project_id)
    target = _workspace_path(path, project_id)
    if not target.exists():
        raise HTTPException(status_code=404, detail="file not found")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="path is not a file")
    suffix = target.suffix.lower()
    if suffix not in TEXT_SUFFIXES and target.name not in TEXT_SUFFIXES:
        guessed = mimetypes.guess_type(target.name)[0] or ""
        if not guessed.startswith("text/"):
            raise HTTPException(status_code=415, detail="file type is not editable")
    encoded = req.content.encode("utf-8")
    if len(encoded) > 600_000:
        raise HTTPException(status_code=413, detail="file is too large to save")
    target.write_text(req.content, encoding="utf-8")
    return {
        "name": target.name,
        "path": _rel(target, root),
        "full_path": str(target),
        "content": req.content,
        "size": target.stat().st_size,
    }


@app.get("/api/chat/projects")
def list_chat_projects(include_archived: bool = False):
    return {"projects": session_service.list_projects(include_archived=include_archived)}


@app.post("/api/chat/projects")
def create_chat_project(req: ChatProjectCreateRequest):
    return session_service.create_project(name=req.name, path=req.path)


@app.patch("/api/chat/projects/{project_id}")
def update_chat_project(project_id: str, req: ChatProjectUpdateRequest):
    try:
        return session_service.update_project(
            project_id,
            name=req.name,
            path=req.path,
            pinned=req.pinned,
            archived=req.archived,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/chat/sessions")
def create_chat_session(req: ChatSessionCreateRequest | None = Body(default=None)):
    return session_service.create_session(
        title=req.title if req else None,
        project_id=req.project_id if req else None,
        scope=req.scope if req else "project",
    )


@app.get("/api/chat/sessions")
def list_chat_sessions(
    source: str | None = None,
    project_id: str | None = None,
    scope: str | None = None,
    include_archived: bool = False,
):
    return {
        "sessions": session_service.list_sessions(
            source=source,
            project_id=project_id,
            scope=scope,
            include_archived=include_archived,
        )
    }


@app.patch("/api/chat/sessions/{session_id}")
def update_chat_session(session_id: str, req: ChatSessionUpdateRequest):
    try:
        return session_service.update_session(
            session_id,
            title=req.title,
            project_id=req.project_id,
            pinned=req.pinned,
            archived=req.archived,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.delete("/api/chat/sessions/{session_id}")
def delete_chat_session(session_id: str):
    if not session_service.delete_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True, "deleted": session_id}


@app.post("/api/chat/{session_id}/message")
async def chat_message(session_id: str, req: ChatMessageRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is empty")
    session_service.append_message(session_id, "user", req.message, private=req.private)
    started = time.perf_counter()
    response = await _reply(session_id, req.message)
    elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
    answer = response.answer
    assistant_message = session_service.append_message(
        session_id,
        "assistant",
        answer,
        private=req.private,
        metadata=_message_metrics(req.message, answer, elapsed_ms, response.trace),
    )
    session = session_service.get_session(session_id)
    consolidation = None
    if session and not req.private and int(session["turn_count"]) % 12 == 0:
        result = session_service.consolidate(session_id)
        consolidation = result.__dict__
    return {
        "answer": answer,
        "message": assistant_message,
        "session": session_service.get_session(session_id),
        "consolidation": consolidation,
        "trace": response.trace,
    }


@app.post("/api/chat/{session_id}/message/stream")
async def chat_message_stream(session_id: str, req: ChatMessageRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is empty")

    async def events():
        session_service.append_message(session_id, "user", req.message, private=req.private)
        started = time.perf_counter()
        final_answer = ""
        final_trace: dict[str, Any] = {}
        try:
            async for event in agent_runner.respond_stream(session_id, req.message):
                if event.get("type") == "delta":
                    final_answer += str(event.get("text") or "")
                    yield _sse(event)
                    continue
                if event.get("type") == "done":
                    final_answer = str(event.get("answer") or final_answer)
                    final_trace = event.get("trace") if isinstance(event.get("trace"), dict) else {}
                    elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
                    assistant_message = session_service.append_message(
                        session_id,
                        "assistant",
                        final_answer,
                        private=req.private,
                        metadata=_message_metrics(req.message, final_answer, elapsed_ms, final_trace),
                    )
                    session = session_service.get_session(session_id)
                    consolidation = None
                    if session and not req.private and int(session["turn_count"]) % 12 == 0:
                        result = session_service.consolidate(session_id)
                        consolidation = result.__dict__
                    event = {
                        **event,
                        "message": assistant_message,
                        "session": session_service.get_session(session_id),
                        "consolidation": consolidation,
                    }
                    yield _sse(event)
                    continue
                yield _sse(event)
        except Exception as exc:
            yield _sse({"type": "error", "error": str(exc)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat/{session_id}/consolidate")
def consolidate(session_id: str):
    result = session_service.consolidate(session_id, force=True)
    return result.__dict__


@app.get("/api/chat/{session_id}/summary")
def get_chat_summary(session_id: str):
    return session_service.get_summary(session_id)


@app.get("/api/agent/runs")
def list_agent_runs(session_id: str | None = None, limit: int = 20):
    return {"runs": session_service.list_agent_runs(session_id=session_id, limit=limit)}


@app.get("/api/agent/runs/{run_id}")
def get_agent_run(run_id: str):
    run = session_service.get_agent_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="agent run not found")
    return {
        "run": run,
        "events": session_service.list_agent_events(run["id"]),
        "timeline": session_service.list_agent_timeline(run_id=run["id"], limit=50),
    }


@app.get("/api/agent/timeline")
def list_agent_timeline(session_id: str | None = None, run_id: str | None = None, limit: int = 100):
    return {"timeline": session_service.list_agent_timeline(session_id=session_id, run_id=run_id, limit=limit)}


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


@app.get("/api/memory/pending-actions")
def memory_pending():
    return list_pending_actions()


@app.post("/api/memory/pending-actions/{pending_id}/approve")
def memory_pending_approve(pending_id: str):
    ok = approve_pending_action(pending_id)
    return {"ok": ok}


@app.post("/api/memory/pending-actions/{pending_id}/reject")
def memory_pending_reject(pending_id: str):
    ok = reject_pending_action(pending_id)
    return {"ok": ok}


@app.get("/api/memory/stats")
def memory_stats():
    return get_stats()


@app.get("/api/models/providers")
def model_providers():
    return list_providers()


@app.get("/api/models/templates")
def model_templates():
    return list_model_templates()


@app.put("/api/models/providers")
def model_providers_write(req: ProvidersWriteRequest):
    return save_providers(req.providers, active_provider_id=req.active_provider_id)


@app.post("/api/models/providers/{provider_id}/test")
async def model_provider_test(provider_id: str):
    return await test_provider(provider_id)


@app.post("/api/models/providers/test")
async def model_provider_config_test(req: ProviderTestRequest):
    return await test_provider_config(req.provider)


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(WEB_DIST / "index.html")


def main() -> None:
    uvicorn.run("selfecho_api.server:app", host="127.0.0.1", port=8765, reload=False)
