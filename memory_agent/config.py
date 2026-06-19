"""
记忆代理配置。

设计目标：
- 项目目录可重命名，不把 e:\\desktop\\111 写死在代码里。
- API key 只从环境变量或本地 .env 读取，不提交到仓库文件。
- 记忆系统复用同一套轻量 LLM 配置。
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    """加载项目根目录的 .env。"""
    project_root = Path(__file__).resolve().parents[1]
    for env_file in (project_root / ".env", project_root / "memory_agent" / ".env"):
        if not env_file.exists():
            continue
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
        except Exception:
            # 配置读取失败不应该中断主流程
            continue


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


_load_dotenv()

# ── 项目路径 ──
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR = PROJECT_ROOT / "memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"

# ── 数据库（与会话层合并）──
MEMORY_DB_PATH = PROJECT_ROOT / "selfecho_data" / "sessions.db"

# ── LLM API 配置 ──
LLM_API_STYLE = _env("MEMORY_AGENT_LLM_API_STYLE", "anthropic").strip().lower()
LLM_BASE_URL = _env("MEMORY_AGENT_LLM_BASE_URL", "https://api.deepseek.com/anthropic").rstrip("/")
LLM_API_KEY = _env("MEMORY_AGENT_LLM_API_KEY", _env("DEEPSEEK_API_KEY", ""))
LLM_MODEL = _env("MEMORY_AGENT_LLM_MODEL", "deepseek-v4-flash")

# ── 提取配置 ──
EXTRACT_MAX_TOKENS = _env_int("MEMORY_AGENT_EXTRACT_MAX_TOKENS", 1200)
EXTRACT_TEMPERATURE = _env_float("MEMORY_AGENT_EXTRACT_TEMPERATURE", 0.2)
EXTRACT_TIMEOUT = _env_float("MEMORY_AGENT_EXTRACT_TIMEOUT", 15.0)

# ── 记忆分级（来自 MemPalace 分层设计） ──
PRIORITY_TIERS = {
    "core": {
        "description": "L0 — 始终加载的核心身份和偏好",
        "max_chars": 800,
        "always_load": True,
    },
    "important": {
        "description": "L1 — 重要但非核心（健康、关系、重大决策）",
        "max_chars": 2000,
        "always_load": True,
    },
    "normal": {
        "description": "L2 — 按话题触发的日常信息",
        "max_chars": 5000,
        "always_load": False,
    },
    "archive": {
        "description": "L3 — 深度搜索按需获取的历史信息",
        "max_chars": 10000,
        "always_load": False,
    },
}

# ── 前端模板 ──
MEMORY_FRONTMATTER_TEMPLATE = """---
name: {slug}
description: {description}
metadata:
  type: {mem_type}
  priority: {priority}
  event_date: {event_date}
  recorded_date: {recorded_date}
  hash: {content_hash}
---
"""

# ── Hash 去重 ──
HASH_ALGORITHM = "md5"  # 速度优先，不涉及安全场景

# ── 日志 ──
LOG_FILE = PROJECT_ROOT / "memory_agent" / "extractor.log"
