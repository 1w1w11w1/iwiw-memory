from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path
from typing import Any

from selfecho_session import SessionMemoryService

from ..core import ToolResult
from .registry import ToolRegistry, ToolSpec


WORKSPACE_EXCLUDE_DIRS = {
    ".git",
    ".claude",
    ".codex",
    "__pycache__",
    "node_modules",
    "dist",
    ".venv",
    "venv",
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
APP_ROOT = Path(__file__).resolve().parents[2]
APP_PRIVATE_DIRS = {"memory", "selfecho_data"}


class WorkspaceTools:
    def __init__(self, session_service: SessionMemoryService) -> None:
        self.session_service = session_service

    def register(self, registry: ToolRegistry) -> None:
        registry.register(
            ToolSpec(
                name="workspace.list_directory",
                description="列出当前会话绑定工作目录中的文件和文件夹。",
                required_permission="read_only",
                risk_level="low",
                handler=self.list_directory,
            )
        )
        registry.register(
            ToolSpec(
                name="workspace.read_text_file",
                description="读取当前会话绑定工作目录中的单个文本文件。",
                required_permission="read_only",
                risk_level="low",
                handler=self.read_text_file,
            )
        )
        registry.register(
            ToolSpec(
                name="workspace.search_files",
                description="在当前会话绑定工作目录中按文件名或文本内容搜索。",
                required_permission="read_only",
                risk_level="low",
                handler=self.search_files,
            )
        )
        registry.register(
            ToolSpec(
                name="git.status",
                description="读取当前会话绑定工作目录的 git 状态。",
                required_permission="read_only",
                risk_level="low",
                handler=self.git_status,
            )
        )

    def list_directory(self, session_id: str, arguments: dict[str, Any]) -> ToolResult:
        root = self._workspace_root(session_id)
        relative = str(arguments.get("path") or "")
        limit = int(arguments.get("limit") or 80)
        target = self._resolve(root, relative)
        if not target.exists():
            return self._fail("workspace.list_directory", f"目录不存在：{relative or '.'}")
        if not target.is_dir():
            return self._fail("workspace.list_directory", f"目标不是目录：{relative or '.'}")

        items: list[dict[str, Any]] = []
        try:
            children = list(target.iterdir())
        except OSError as exc:
            return self._fail("workspace.list_directory", f"无法读取目录：{exc}")

        for child in sorted(children, key=lambda item: (not item.is_dir(), item.name.lower())):
            if self._is_excluded(child, root):
                continue
            try:
                stat = child.stat()
                items.append(
                    {
                        "name": child.name,
                        "path": self._relative(root, child),
                        "kind": "directory" if child.is_dir() else "file",
                        "size": stat.st_size if child.is_file() else None,
                    }
                )
            except OSError:
                continue
            if len(items) >= limit:
                break

        return ToolResult(
            name="workspace.list_directory",
            ok=True,
            summary=f"读取 {self._relative(root, target) or '.'}，发现 {len(items)} 项。",
            content={
                "root": str(root),
                "path": self._relative(root, target),
                "items": items,
                "truncated": len(items) >= limit,
            },
            next_actions=["需要具体文件内容时调用 workspace.read_text_file", "需要按关键词定位时调用 workspace.search_files"],
            artifacts=[{"type": "directory", "path": self._relative(root, target)}],
        )

    def read_text_file(self, session_id: str, arguments: dict[str, Any]) -> ToolResult:
        root = self._workspace_root(session_id)
        relative = str(arguments.get("path") or "")
        if not relative.strip():
            return self._fail("workspace.read_text_file", "缺少文件路径。")
        target = self._resolve(root, relative)
        if not target.exists():
            return self._fail("workspace.read_text_file", f"文件不存在：{relative}")
        if not target.is_file():
            return self._fail("workspace.read_text_file", f"目标不是文件：{relative}")
        if target.stat().st_size > 160_000:
            return self._fail("workspace.read_text_file", f"文件过大，暂不自动读取：{relative}")
        if target.suffix.lower() not in TEXT_SUFFIXES and target.name not in TEXT_SUFFIXES:
            return self._fail("workspace.read_text_file", f"文件类型暂不自动读取：{relative}")
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return self._fail("workspace.read_text_file", f"读取失败：{exc}")

        preview = content[:12000]
        return ToolResult(
            name="workspace.read_text_file",
            ok=True,
            summary=f"读取文件 {self._relative(root, target)}，共 {len(content)} 字符。",
            content={
                "root": str(root),
                "path": self._relative(root, target),
                "size": target.stat().st_size,
                "content": preview,
                "truncated": len(preview) < len(content),
            },
            next_actions=["基于文件内容回答用户问题", "内容被截断时说明可继续读取更具体片段"],
            artifacts=[{"type": "file", "path": self._relative(root, target), "size": target.stat().st_size}],
        )

    def search_files(self, session_id: str, arguments: dict[str, Any]) -> ToolResult:
        root = self._workspace_root(session_id)
        query = str(arguments.get("query") or arguments.get("pattern") or "").strip()
        relative = str(arguments.get("path") or "")
        limit = min(max(int(arguments.get("limit") or 40), 1), 100)
        include_content = bool(arguments.get("include_content", True))
        if not query:
            return self._fail(
                "workspace.search_files",
                "缺少搜索关键词。",
                root_cause_hint="workspace.search_files requires a non-empty query",
                safe_retry=True,
                next_actions=["提供明确关键词后重试"],
            )
        target = self._resolve(root, relative)
        if not target.exists():
            return self._fail("workspace.search_files", f"搜索目录不存在：{relative or '.'}")
        if not target.is_dir():
            return self._fail("workspace.search_files", f"搜索目标不是目录：{relative or '.'}")

        query_lower = query.lower()
        results: list[dict[str, Any]] = []
        scanned = 0
        truncated = False
        for file_path in self._walk_files(root, target):
            scanned += 1
            relative_path = self._relative(root, file_path)
            name_match = self._matches_path(query_lower, relative_path)
            content_match = None
            if include_content and self._is_searchable_text(file_path):
                content_match = self._first_content_match(file_path, query_lower)
            if not name_match and content_match is None:
                continue
            try:
                size = file_path.stat().st_size
            except OSError:
                size = None
            item = {
                "path": relative_path,
                "size": size,
                "match": "filename" if name_match else "content",
            }
            if content_match is not None:
                item.update(content_match)
                item["match"] = "filename+content" if name_match else "content"
            results.append(item)
            if len(results) >= limit:
                truncated = True
                break

        summary = f"搜索 `{query}`，扫描 {scanned} 个文件，找到 {len(results)} 个结果。"
        return ToolResult(
            name="workspace.search_files",
            ok=True,
            summary=summary,
            content={
                "root": str(root),
                "path": self._relative(root, target),
                "query": query,
                "results": results,
                "scanned": scanned,
                "truncated": truncated,
            },
            next_actions=(
                ["读取匹配文件的具体内容", "需要更多结果时提高 limit 或缩小路径"]
                if results
                else ["调整关键词、扩大路径或改用目录列表确认文件名"]
            ),
            artifacts=[{"type": "file", "path": item["path"]} for item in results[:20]],
        )

    def git_status(self, session_id: str, arguments: dict[str, Any]) -> ToolResult:
        root = self._workspace_root(session_id)
        try:
            completed = subprocess.run(
                ["git", "status", "--short", "--branch"],
                cwd=root,
                timeout=5,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError:
            return self._fail(
                "git.status",
                "当前环境未找到 git 命令。",
                root_cause_hint="git executable is not available",
                safe_retry=False,
                next_actions=["安装 git 或改用目录观察"],
                stop_condition="没有 git 可执行文件时停止调用 git.status。",
            )
        except subprocess.TimeoutExpired:
            return self._fail(
                "git.status",
                "读取 git 状态超时。",
                root_cause_hint="git status exceeded the read-only timeout",
                safe_retry=True,
                next_actions=["稍后重试", "检查仓库是否存在异常文件系统阻塞"],
                stop_condition="连续超时后停止重试并提示用户手动检查仓库。",
            )

        output = (completed.stdout or "").strip()
        error = (completed.stderr or "").strip()
        if completed.returncode != 0:
            return self._fail(
                "git.status",
                error or "当前工作目录不是可读取的 git 仓库。",
                root_cause_hint="git status returned a non-zero exit code",
                safe_retry=False,
                next_actions=["确认项目路径是否为 git 仓库", "需要时先读取工作目录结构"],
                stop_condition="路径不是 git 仓库时不要继续调用 git.status。",
            )

        lines = output.splitlines()
        branch = lines[0] if lines else ""
        entries = lines[1:]
        clean = len(entries) == 0
        return ToolResult(
            name="git.status",
            ok=True,
            summary="工作区干净。" if clean else f"读取 git 状态，发现 {len(entries)} 条改动。",
            content={
                "root": str(root),
                "branch": branch,
                "entries": entries[:200],
                "clean": clean,
                "truncated": len(entries) > 200,
            },
            next_actions=["只报告状态，不自动提交或改写历史", "需要修改或提交时先请求用户确认"],
            artifacts=[{"type": "git_status", "path": str(root), "clean": clean}],
        )

    def _workspace_root(self, session_id: str) -> Path:
        session = self.session_service.get_session(session_id)
        if not session:
            raise ValueError("会话不存在，无法定位工作目录。")
        if session.get("scope") == "chat":
            raise ValueError("当前是普通对话，没有绑定项目工作目录。")
        project = self.session_service.get_project(str(session.get("project_id") or ""))
        if not project or not str(project.get("path") or "").strip():
            raise ValueError("当前项目没有绑定工作目录。")
        root = Path(str(project.get("path"))).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"工作目录不存在或不是文件夹：{root}")
        return root

    def _resolve(self, root: Path, relative: str) -> Path:
        clean = str(relative or "").strip().replace("\\", "/")
        target = (root / clean).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("工具只能访问当前工作目录内部路径。") from exc
        if self._is_excluded(target, root):
            raise ValueError("该路径属于隐私或忽略范围，工具不会自动读取。")
        return target

    def _relative(self, root: Path, path: Path) -> str:
        value = path.resolve().relative_to(root).as_posix()
        return "" if value == "." else value

    def _walk_files(self, root: Path, start: Path):
        for current, dir_names, file_names in os.walk(start):
            current_path = Path(current)
            dir_names[:] = [
                name
                for name in dir_names
                if not self._is_excluded(current_path / name, root)
            ]
            for file_name in sorted(file_names, key=str.lower):
                file_path = current_path / file_name
                if self._is_excluded(file_path, root):
                    continue
                yield file_path

    def _matches_path(self, query_lower: str, relative_path: str) -> bool:
        path_lower = relative_path.lower()
        name_lower = Path(relative_path).name.lower()
        if "*" in query_lower or "?" in query_lower:
            return fnmatch.fnmatch(path_lower, query_lower) or fnmatch.fnmatch(name_lower, query_lower)
        return query_lower in path_lower

    def _is_searchable_text(self, path: Path) -> bool:
        try:
            if path.stat().st_size > 160_000:
                return False
        except OSError:
            return False
        return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_SUFFIXES

    def _first_content_match(self, path: Path, query_lower: str) -> dict[str, Any] | None:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for idx, line in enumerate(handle, start=1):
                    line_clean = line.rstrip()
                    if query_lower in line_clean.lower():
                        snippet = " ".join(line_clean.split())
                        return {"line": idx, "snippet": snippet[:240]}
        except OSError:
            return None
        return None

    def _is_excluded(self, path: Path, root: Path | None = None) -> bool:
        name = path.name
        if root and root.resolve() == APP_ROOT and name in APP_PRIVATE_DIRS:
            return True
        if name in WORKSPACE_EXCLUDE_NAMES:
            return True
        if path.is_dir() and name in WORKSPACE_EXCLUDE_DIRS:
            return True
        if path.is_file() and path.suffix.lower() in WORKSPACE_EXCLUDE_SUFFIXES:
            return True
        if os.name == "nt" and name.lower() in {"thumbs.db", "desktop.ini"}:
            return True
        return False

    def _fail(
        self,
        name: str,
        message: str,
        *,
        root_cause_hint: str = "",
        safe_retry: bool = False,
        next_actions: list[str] | None = None,
        stop_condition: str = "",
    ) -> ToolResult:
        return ToolResult(
            name=name,
            ok=False,
            summary=message,
            error=message,
            root_cause_hint=root_cause_hint or "workspace observation could not be completed",
            safe_retry=safe_retry,
            next_actions=next_actions or ["向用户说明无法确认该工作区观察"],
            stop_condition=stop_condition or "参数或工作目录没有变化时停止重复调用。",
        )
