"""
memory_agent 配置。

设计目标：
- 项目目录可重命名，不把绝对路径写死在代码里。
- API key 只从环境变量或本地 .env 读取，不提交到仓库文件。
- 数据真源：data/memory.db（SQLite 单一真源，Markdown 已废弃）。
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

# ── 数据真源 ──
DATA_DIR = PROJECT_ROOT / "data"
# MEMORY_AGENT_DB_PATH：测试隔离钩子（E2E 用临时库覆盖真源路径）
MEMORY_DB_PATH = Path(_env("MEMORY_AGENT_DB_PATH", str(DATA_DIR / "memory.db")))

# ── LLM API 配置 ──
LLM_API_STYLE = _env("MEMORY_AGENT_LLM_API_STYLE", "anthropic").strip().lower()
LLM_BASE_URL = _env("MEMORY_AGENT_LLM_BASE_URL", "https://api.deepseek.com/anthropic").rstrip("/")
LLM_API_KEY = _env("MEMORY_AGENT_LLM_API_KEY", _env("DEEPSEEK_API_KEY", ""))
LLM_MODEL = _env("MEMORY_AGENT_LLM_MODEL", "deepseek-v4-flash")

# ── 记忆工具轮（模型自主 function calling；回复前执行，失败降级为纯文本对话）──
TOOL_MAX_TOKENS = _env_int("MEMORY_AGENT_TOOL_MAX_TOKENS", 400)
TOOL_TEMPERATURE = _env_float("MEMORY_AGENT_TOOL_TEMPERATURE", 0.2)
TOOL_TIMEOUT = _env_float("MEMORY_AGENT_TOOL_TIMEOUT", 30.0)
TOOL_MAX_LOOPS = _env_int("MEMORY_AGENT_TOOL_MAX_LOOPS", 3)

# ── reflect steering（写入密度兜底）──
# 连续 REFLECT_TURNS 轮未写入记忆时，向 system 注入一次性回顾提示
# （提示后归零；轮内实际写入也会归零）。0 = 关闭。
REFLECT_TURNS = _env_int("MEMORY_AGENT_REFLECT_TURNS", 7)

# ── 常驻层（模式配置）──
# 常驻层的记忆每轮全量注入 system（类型 × 模式的策略外置）：
#   chat 模式（默认）: profile + rules —— 身份画像与准则每轮在场
#   dev 模式: ["rules"] —— 身份降级为检索召回，不污染开发上下文
# 其余层（fact/lesson/project）一律按话题检索召回。
STANDING_LAYERS = _env("MEMORY_AGENT_STANDING_LAYERS", "profile,rules").split(",")
STANDING_LAYERS = [s.strip() for s in STANDING_LAYERS if s.strip() in ("profile", "fact", "lesson", "rules", "project")]

# ── 检索 ──
# 分数尺度：base = Σ(FTS 命中数×bm25 权重) + 状态候选×state 权重，再乘时间/优先级因子
# （权重真源是 retrieval.DEFAULT_WEIGHTS）。单次 FTS 命中约 0.6，阈值默认 0.15 过滤无关结果。
SEARCH_RELEVANCE_THRESHOLD = _env_float("MEMORY_AGENT_SEARCH_RELEVANCE_THRESHOLD", 0.15)
MEMORY_RECALL_MAX_CHARS = _env_int("MEMORY_AGENT_RECALL_MAX_CHARS", 2500)

# ── 会话（chat 工作台）──
CHAT_CONTEXT_TURNS = _env_int("MEMORY_AGENT_CHAT_CONTEXT_TURNS", 12)
# 注入精度优先：top_k 过大会带出无关记忆（误注入挤占去重窗口，阻塞后续回指）
CHAT_RECALL_TOP_K = _env_int("MEMORY_AGENT_CHAT_RECALL_TOP_K", 3)
CHAT_MAX_REPLY_TOKENS = _env_int("MEMORY_AGENT_CHAT_MAX_REPLY_TOKENS", 1024)
# DSC 压缩：历史超过 CHAT_CONTEXT_TURNS 轮时，旧上下文压成 SessionState 检查点，
# 保留最近 CHAT_COMPACT_TAIL_TURNS 轮完整原文（尾部保留，借鉴 Codex compaction tail）
CHAT_COMPACT_TAIL_TURNS = _env_int("MEMORY_AGENT_CHAT_COMPACT_TAIL_TURNS", 6)
