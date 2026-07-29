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

# ── 数据库 ──
MEMORY_DB_PATH = PROJECT_ROOT / "selfecho_data" / "sessions.db"

# ── LLM 调用配置 ──
EXTRACT_MAX_TOKENS = _env_int("MEMORY_AGENT_EXTRACT_MAX_TOKENS", 1200)
EXTRACT_TEMPERATURE = _env_float("MEMORY_AGENT_EXTRACT_TEMPERATURE", 0.2)
EXTRACT_TIMEOUT = _env_float("MEMORY_AGENT_EXTRACT_TIMEOUT", 15.0)

# ── 向量嵌入配置 ──
EMBEDDING_MODEL = _env("MEMORY_AGENT_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
EMBEDDING_DIMENSIONS = _env_int("MEMORY_AGENT_EMBEDDING_DIMENSIONS", 512)
EMBEDDING_BATCH_SIZE = _env_int("MEMORY_AGENT_EMBEDDING_BATCH_SIZE", 16)
EMBEDDING_DEVICE = _env("MEMORY_AGENT_EMBEDDING_DEVICE", "cpu")

# ── 检索评分权重（对齐设计文档）──
RETRIEVAL_SEMANTIC_WEIGHT = _env_float("MEMORY_AGENT_RETRIEVAL_SEMANTIC_WEIGHT", 0.55)
RETRIEVAL_KEYWORD_WEIGHT = _env_float("MEMORY_AGENT_RETRIEVAL_KEYWORD_WEIGHT", 0.25)
RETRIEVAL_RECENCY_WEIGHT = _env_float("MEMORY_AGENT_RETRIEVAL_RECENCY_WEIGHT", 0.10)
RETRIEVAL_SCOPE_WEIGHT = _env_float("MEMORY_AGENT_RETRIEVAL_SCOPE_WEIGHT", 0.10)

# ── 检索门控 ──
HYBRID_TOP_K = _env_int("MEMORY_AGENT_HYBRID_TOP_K", 10)
HYBRID_RELEVANCE_THRESHOLD = _env_float("MEMORY_AGENT_HYBRID_RELEVANCE_THRESHOLD", 0.45)
HYBRID_STRONG_SIGNAL_THRESHOLD = _env_float("MEMORY_AGENT_HYBRID_STRONG_SIGNAL_THRESHOLD", 0.35)
RELEVANCE_GATE_FOCUSED_THRESHOLD = _env_float("MEMORY_AGENT_RELEVANCE_GATE_FOCUSED_THRESHOLD", 0.65)
MEMORY_RECALL_MAX_CHARS = _env_int("MEMORY_AGENT_RECALL_MAX_CHARS", 2500)

# ── 日志 ──
LOG_FILE = PROJECT_ROOT / "memory_agent" / "extractor.log"
