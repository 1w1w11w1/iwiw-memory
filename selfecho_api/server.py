from __future__ import annotations

import asyncio
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
from urllib.parse import urlsplit

import uvicorn
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse

from memory_agent.engine import MemoryMutationContext, default_memory_engine
from memory_agent.scopes import ActiveMemoryScope
from selfecho_agent import AgentResponse, AgentRunner
from selfecho_agent.file_mutations import FileMutationService
from selfecho_config.model_profiles import (
    LOCAL_CONFIG as PROVIDERS_CONFIG_PATH,
    list_model_templates,
    list_providers,
    render_providers_config,
    test_provider,
)
from selfecho_session import SessionMemoryService
from selfecho_session.config import DB_PATH

from .prompt_service import list_prompts, preview_prompt, prompt_path, read_prompt

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

ALLOWED_BROWSER_ORIGINS = {
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
}
PERMISSION_PROFILE_HEADER = "x-iwiw-permission-profile"
PERMISSION_PROFILES = {"read_only", "guided", "workspace", "full_access"}

app = FastAPI(title="IwIw API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(ALLOWED_BROWSER_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def validate_browser_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and origin not in ALLOWED_BROWSER_ORIGINS:
        return JSONResponse({"detail": "origin not allowed"}, status_code=403)
    path = request.url.path
    memory_management = (
        path.startswith("/api/memories")
        or path.startswith("/api/memory/")
        or path.startswith("/api/memory-")
        or path.startswith("/api/session-memory/")
        or (request.method in {"DELETE", "POST"} and path.startswith("/api/chat/sessions/"))
        or (request.method in {"POST", "PATCH", "DELETE"} and path == "/api/filesystem/directory")
        or (request.method == "PUT" and path == "/api/workspace/file")
        or (request.method == "PUT" and path.startswith("/api/prompts/"))
        or (request.method in {"PUT", "POST"} and path.startswith("/api/models/providers"))
    )
    if memory_management and not _trusted_browser_source(request):
        return JSONResponse({"detail": "trusted browser source required"}, status_code=403)
    return await call_next(request)


def _trusted_browser_source(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin:
        return origin in ALLOWED_BROWSER_ORIGINS
    referer = request.headers.get("referer")
    if not referer:
        return False
    parsed = urlsplit(referer)
    return f"{parsed.scheme}://{parsed.netloc}" in ALLOWED_BROWSER_ORIGINS


def _memory_mutation_context(
    request: Request,
    *,
    confirmed: bool,
    action: str,
    target_id: str,
) -> MemoryMutationContext:
    permission_profile = request.headers.get(PERMISSION_PROFILE_HEADER, "read_only").strip().lower()
    if permission_profile not in PERMISSION_PROFILES:
        permission_profile = "read_only"
    return MemoryMutationContext(
        permission_profile=permission_profile,
        confirmed=confirmed,
        source="trusted_gui" if _trusted_browser_source(request) else "untrusted",
        action=action,
        target_id=target_id,
    )

session_service = SessionMemoryService()
agent_runner = AgentRunner(session_service, read_prompt)
file_mutation_service = FileMutationService()


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
    body: str
    description: str = ""
    reason: str = "GUI edit"
    confirmed: bool = False


class TendencyCompileRequest(BaseModel):
    scope_kind: str = "agent_global"
    project_id: str | None = None
    session_id: str | None = None
    action: str = "compile"
    confirmed: bool = False


class PromptWriteRequest(BaseModel):
    content: str
    confirmed: bool = False


class PromptPreviewRequest(BaseModel):
    user_message: str = ""


class ProvidersWriteRequest(BaseModel):
    providers: list[dict[str, Any]]
    role_defaults: dict[str, Any] | None = None
    confirmed: bool = False


class ProviderTestRequest(BaseModel):
    provider: dict[str, Any]
    confirmed: bool = False


class WorkspaceFileWriteRequest(BaseModel):
    content: str
    confirmed: bool = False


class FileSystemDirectoryCreateRequest(BaseModel):
    parent: str = ""
    name: str | None = None
    confirmed: bool = False


class FileSystemDirectoryRenameRequest(BaseModel):
    path: str
    name: str
    confirmed: bool = False


class FileSystemDirectoryDeleteRequest(BaseModel):
    path: str
    confirmed: bool = False


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


def _active_memory_scope(project_id: str | None = None, session_id: str | None = None) -> ActiveMemoryScope:
    session = session_service.get_session(session_id) if session_id else None
    scoped_project_id = project_id or (str(session.get("project_id") or "") if session else "")
    if session and session.get("scope") != "project":
        scoped_project_id = ""

    workspace_root: str | None = None
    if scoped_project_id:
        project = session_service.get_project(scoped_project_id)
        if project:
            workspace_root = str(project.get("path") or "").strip() or None
    return ActiveMemoryScope.for_session(
        session_id=str(session.get("id")) if session else session_id,
        project_id=scoped_project_id or None,
        workspace_root=workspace_root,
    )


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
    try:
        stats = {"available": True, **default_memory_engine.stats()}
    except Exception as exc:
        stats = {"available": False, "error": str(exc)}
    return {
        "ok": True,
        "memory": stats,
        "session_memory": {
            "db_path": str(DB_PATH),
            "exists": DB_PATH.exists(),
            "sessions": len(session_service.list_sessions(limit=100000, include_archived=True)),
            "projects": len(session_service.list_projects(include_archived=True)),
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
def filesystem_directory_create(request: Request, req: FileSystemDirectoryCreateRequest):
    parent = _fs_path(req.parent) if req.parent.strip() else _default_project_parent()
    if req.name and req.name.strip():
        target = (parent / _valid_dir_name(req.name)).resolve()
    else:
        target = _next_project_dir(parent)
    if target.exists():
        raise HTTPException(status_code=409, detail="directory already exists")
    result = file_mutation_service.create_directory(
        target,
        mutation_context=_memory_mutation_context(
            request,
            confirmed=req.confirmed,
            action="filesystem.directory.create",
            target_id=str(target),
        ),
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.to_dict())
    return {**_fs_dir_item(target), "mutation": result.to_dict()}


@app.patch("/api/filesystem/directory")
def filesystem_directory_rename(request: Request, req: FileSystemDirectoryRenameRequest):
    target = _fs_path(req.path)
    new_name = _valid_dir_name(req.name)
    next_path = (target.parent / new_name).resolve()
    if next_path.exists() and next_path != target:
        raise HTTPException(status_code=409, detail="directory already exists")
    target_id = f"{target} -> {next_path}"
    result = file_mutation_service.rename_directory(
        target,
        next_path,
        mutation_context=_memory_mutation_context(
            request,
            confirmed=req.confirmed,
            action="filesystem.directory.rename",
            target_id=target_id,
        ),
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.to_dict())
    return {**_fs_dir_item(next_path), "mutation": result.to_dict()}


@app.delete("/api/filesystem/directory")
def filesystem_directory_delete(request: Request, req: FileSystemDirectoryDeleteRequest):
    target = _fs_path(req.path)
    if target.parent == target:
        raise HTTPException(status_code=400, detail="cannot delete filesystem root")
    if any(target.iterdir()):
        raise HTTPException(status_code=409, detail="directory is not empty")
    result = file_mutation_service.delete_empty_directory(
        target,
        mutation_context=_memory_mutation_context(
            request,
            confirmed=req.confirmed,
            action="filesystem.directory.delete",
            target_id=str(target),
        ),
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.to_dict())
    return {"ok": True, "deleted": str(target), "mutation": result.to_dict()}


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
def workspace_file_write(request: Request, path: str, req: WorkspaceFileWriteRequest, project_id: str | None = None):
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
    result = file_mutation_service.write_file(
        target,
        req.content,
        mutation_context=_memory_mutation_context(
            request,
            confirmed=req.confirmed,
            action="workspace.file.write",
            target_id=str(target),
        ),
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.to_dict())
    return {
        "name": target.name,
        "path": _rel(target, root),
        "full_path": str(target),
        "content": req.content,
        "size": target.stat().st_size,
        "mutation": result.to_dict(),
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
        if req.archived is True:
            session_service.consolidate(session_id, force=True)
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
def delete_chat_session(request: Request, session_id: str, confirmed: bool = False):
    result = session_service.delete_session(
        session_id,
        mutation_context=_memory_mutation_context(
            request,
            confirmed=confirmed,
            action="session.delete",
            target_id=session_id,
        ),
    )
    if not result.ok:
        status = 404 if result.error == "session not found" else 400
        raise HTTPException(status_code=status, detail=result.to_dict())
    return {"ok": True, "mutation": result.to_dict(), "deleted": result.target_id}


@app.post("/api/chat/sessions/{session_id}/restore/{version}")
def restore_chat_session(request: Request, session_id: str, version: str, confirmed: bool = False):
    result = session_service.restore_session_version(
        session_id,
        version,
        mutation_context=_memory_mutation_context(
            request,
            confirmed=confirmed,
            action="session.restore",
            target_id=session_id,
        ),
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.to_dict())
    return {"ok": True, "mutation": result.to_dict(), "session": session_service.get_session(session_id)}


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
    if session and not req.private and session_service.should_consolidate(session_id):
        result = await asyncio.to_thread(session_service.consolidate, session_id)
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
        assistant_saved = False
        try:
            async for event in agent_runner.respond_stream(session_id, req.message):
                if event.get("type") == "meta":
                    final_trace = {
                        "run_id": event.get("run_id"),
                        "status": event.get("status", "running"),
                    }
                    yield _sse(event)
                    continue
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
                    assistant_saved = True
                    session = session_service.get_session(session_id)
                    consolidation = None
                    if session and not req.private and session_service.should_consolidate(session_id):
                        result = await asyncio.to_thread(session_service.consolidate, session_id)
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
        except (GeneratorExit, asyncio.CancelledError):
            if final_answer and not assistant_saved:
                elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
                session_service.append_message(
                    session_id,
                    "assistant",
                    final_answer,
                    private=req.private,
                    metadata={
                        **_message_metrics(req.message, final_answer, elapsed_ms, final_trace),
                        "status": "cancelled",
                    },
                )
            raise
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


@app.get("/api/memories")
def memories(source_type: str | None = None, limit: int = 200):
    records = default_memory_engine.list_records(source_type=source_type, limit=limit, include_all=True)
    return {"memories": records}


@app.get("/api/memories/search")
def search_memories_api(q: str, limit: int = 20):
    results = default_memory_engine.search_records(q, limit=limit)
    for item in results:
        item.setdefault("source", "memory_engine")
    return {"results": results, "trace": {"decision": "management", "total": len(results)}}


@app.get("/api/memories/{record_id}")
def memory_detail(record_id: str):
    record = default_memory_engine.get_record(record_id, include_all=True)
    if not record:
        raise HTTPException(status_code=404, detail="memory not found")
    record["history"] = default_memory_engine.list_record_versions(record_id)
    return record


@app.put("/api/memories/{record_id}")
def edit_memory(request: Request, record_id: str, req: MemoryEditRequest):
    old = default_memory_engine.get_record(record_id, include_all=True)
    if not old:
        raise HTTPException(status_code=404, detail="memory not found")
    result = default_memory_engine.update_record(
        record_id,
        content=req.body,
        description=req.description,
        reason=req.reason or "manual_edit",
        mutation_context=_memory_mutation_context(
            request,
            confirmed=req.confirmed,
            action="memory.update",
            target_id=record_id,
        ),
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.to_dict())
    updated = default_memory_engine.get_record(record_id, include_all=True)
    return {
        "ok": True,
        "mutation": result.to_dict(),
        "diff": _diff(old.get("content", ""), req.body),
        "memory": updated,
    }


@app.delete("/api/memories/{record_id}")
def delete_memory_api(request: Request, record_id: str, confirmed: bool = False):
    result = default_memory_engine.delete_record(
        record_id,
        mutation_context=_memory_mutation_context(
            request,
            confirmed=confirmed,
            action="memory.delete",
            target_id=record_id,
        ),
        reason="GUI delete",
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.to_dict())
    return {"ok": True, "mutation": result.to_dict(), "deleted": record_id}


@app.get("/api/memories/{record_id}/history/{version}")
def memory_history(record_id: str, version: str):
    record = default_memory_engine.get_record_version(record_id, version)
    if not record:
        raise HTTPException(status_code=404, detail="history not found")
    return {"record_id": record_id, "version": version, "record": record}


@app.get("/api/memory-audit")
def memory_audit(limit: int = 100):
    return {"events": default_memory_engine.audit_events(limit=limit)}


@app.post("/api/memory-audit/{audit_id}/rollback")
def rollback_audit(request: Request, audit_id: str, confirmed: bool = False):
    mutation_context = _memory_mutation_context(
        request,
        confirmed=confirmed,
        action="memory.rollback",
        target_id=audit_id,
    )
    event = default_memory_engine.get_audit_event(audit_id)
    is_session_rollback = bool(
        event
        and event.get("target_type") == "session"
        and str(event.get("backup_path") or "").startswith("session_version://")
        and event.get("target_id")
    )
    is_workspace_file_rollback = bool(
        event
        and event.get("target_type") == "workspace_file"
        and str(event.get("backup_path") or "").startswith("workspace_version://")
    )
    is_directory_rollback = bool(
        event
        and event.get("target_type") == "directory"
        and str(event.get("backup_path") or "").startswith("directory_version://")
    )
    if is_session_rollback:
        if error := mutation_context.authorization_error("memory.rollback", audit_id):
            raise HTTPException(status_code=400, detail=error)
        session_id = str(event["target_id"])
        result = session_service.restore_session_version(
            session_id,
            str(event["backup_path"]),
            mutation_context=mutation_context.delegated("session.restore", session_id),
        )
    elif is_workspace_file_rollback or is_directory_rollback:
        result = file_mutation_service.rollback_audit(
            audit_id,
            mutation_context=mutation_context,
        )
    else:
        result = default_memory_engine.rollback_audit(
            audit_id,
            mutation_context=mutation_context,
        )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.to_dict())
    response = {
        "ok": True,
        "mutation": result.to_dict(),
    }
    if is_session_rollback:
        response["session"] = session_service.get_session(result.target_id or "")
    elif is_workspace_file_rollback:
        response["workspace_file"] = {"path": result.target_id}
    elif is_directory_rollback:
        response["workspace_directory"] = {"path": result.target_id}
    else:
        response["memory"] = default_memory_engine.get_record(result.target_id or "", include_all=True)
    return response


@app.get("/api/memory/stats")
def memory_stats():
    return default_memory_engine.stats()


@app.get("/api/memory/tendency")
def memory_tendency(project_id: str | None = None, session_id: str | None = None, limit: int = 50):
    active_scope = _active_memory_scope(project_id=project_id, session_id=session_id)
    return default_memory_engine.tendency_overview(active_scope, limit=limit)


@app.post("/api/memory/tendency/compile")
async def compile_tendency(request: Request, req: TendencyCompileRequest):
    scope_kind = req.scope_kind.strip() or "agent_global"
    action = req.action.strip() or "compile"
    if scope_kind not in {"agent_global", "workspace", "session"}:
        raise HTTPException(status_code=400, detail="scope_kind must be agent_global, workspace, or session")
    if action not in {"compile", "rebuild"}:
        raise HTTPException(status_code=400, detail="action must be compile or rebuild")
    active_scope = _active_memory_scope(project_id=req.project_id, session_id=req.session_id)
    if scope_kind == "agent_global":
        target_scope = active_scope.agent_global()
    elif scope_kind == "workspace":
        target_scope = active_scope.workspace()
        if not target_scope:
            raise HTTPException(status_code=400, detail="workspace scope requires a project with workspace path")
    else:
        target_scope = active_scope.session()
        if not target_scope:
            raise HTTPException(status_code=400, detail="session scope requires session_id")
    result = await default_memory_engine.compile_tendency(
        scope_kind=target_scope.kind,
        scope_key=target_scope.key,
        workspace_id=target_scope.workspace_id,
        project_id=target_scope.project_id,
        session_id=req.session_id,
        rebuild=action == "rebuild",
        mutation_context=_memory_mutation_context(
            request,
            confirmed=req.confirmed,
            action="tendency.rebuild" if action == "rebuild" else "tendency.promote",
            target_id=f"{target_scope.kind}:{target_scope.key}",
        ),
    )
    if not result.get("ok") and result.get("error") not in {"no observations", "no active observations"}:
        raise HTTPException(status_code=400, detail=result)
    return {"ok": bool(result.get("ok")), "result": result}


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
def prompt_write(request: Request, name: str, req: PromptWriteRequest):
    try:
        target = prompt_path(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    result = file_mutation_service.write_config_file(
        target,
        req.content,
        action="prompt.write",
        reason=f"prompt write: {name}",
        mutation_context=_memory_mutation_context(
            request,
            confirmed=req.confirmed,
            action="prompt.write",
            target_id=str(target),
        ),
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.to_dict())
    return {"name": name, "content": req.content, "mutation": result.to_dict()}


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
def model_providers_write(request: Request, req: ProvidersWriteRequest):
    result = file_mutation_service.write_config_file(
        PROVIDERS_CONFIG_PATH,
        render_providers_config(req.providers, role_defaults=req.role_defaults),
        action="model.providers.write",
        reason="provider configuration write",
        mutation_context=_memory_mutation_context(
            request,
            confirmed=req.confirmed,
            action="model.providers.write",
            target_id=str(PROVIDERS_CONFIG_PATH),
        ),
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.to_dict())
    return {**list_providers(), "mutation": result.to_dict()}


@app.post("/api/models/providers/{provider_id}/test")
async def model_provider_test(request: Request, provider_id: str, confirmed: bool = False):
    context = _memory_mutation_context(
        request,
        confirmed=confirmed,
        action="model.provider.test",
        target_id=provider_id,
    )
    if error := context.authorization_error("model.provider.test", provider_id):
        raise HTTPException(status_code=400, detail=error)
    return await test_provider(provider_id)


@app.post("/api/models/providers/test")
async def model_provider_config_test(request: Request, req: ProviderTestRequest):
    provider_id = str(req.provider.get("id") or "draft")
    context = _memory_mutation_context(
        request,
        confirmed=req.confirmed,
        action="model.provider.test",
        target_id=provider_id,
    )
    if error := context.authorization_error("model.provider.test", provider_id):
        raise HTTPException(status_code=400, detail=error)
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
