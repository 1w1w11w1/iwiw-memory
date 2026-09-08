"""
retrieval.py — 确定性检索：FTS + 词面 + 会话状态 + 结构化加权。

已删除向量嵌入通道（见 docs/associative-recall-architecture.md）：
- L1 词面：FTS5 + 子串兜底 + 同义词扩展（query_builder）
- L2 状态：SessionState 回指候选（最近/高频讨论话题关联的记忆）
- 结构化：priority 加权 + 时间衰减 + 阈值过滤

全部确定性、零模型、可测试。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from .db import search_fts, get_memory
from .query_builder import build_queries

# ── 权重（真源是 config，这里做默认值兜底）──
DEFAULT_WEIGHTS = {
    "bm25": 0.6,      # FTS 词面命中
    "state": 0.4,     # 会话状态候选（回指联想）
    "time": 0.15,     # 时间衰减
}

TIME_HALF_LIFE_DAYS = 90  # 90 天半衰期

# 会话状态候选的基础分（高于单次 FTS 命中，低于多次命中）
STATE_CANDIDATE_BASE = 1.2


def _time_decay(mtime_str: str | None) -> float:
    """时间衰减因子（90 天半衰期）。"""
    if not mtime_str:
        return 1.0
    try:
        mtime = datetime.fromisoformat(mtime_str)
    except (ValueError, TypeError):
        return 1.0
    age_days = (datetime.now() - mtime).total_seconds() / 86400.0
    return math.exp(-math.log(2) * age_days / TIME_HALF_LIFE_DAYS)


def search_memories(
    user_message: str,
    context_messages: list[str] | None = None,
    top_k: int = 5,
    relevance_threshold: float | None = None,
    weights: dict[str, float] | None = None,
    exclude_slugs: set[str] | None = None,
    topic_words: list[str] | None = None,
    session_state: Any | None = None,
) -> list[dict[str, Any]]:
    """
    确定性检索入口。

    Args:
        user_message: 用户当前消息
        context_messages: 最近几轮对话（含主题词拼接，由调用方传入）
        top_k: 返回结果数
        relevance_threshold: 相关性阈值，None 时取 config
        weights: 各因子权重
        exclude_slugs: 排除的记忆 slug（常驻/窗口内已注入）
        topic_words: 会话主题词（用于联想 query 扩展）
        session_state: SessionState 实例（回指联想候选）

    Returns:
        每条含 slug/description/priority/source/score 等字段
    """
    from .config import SEARCH_RELEVANCE_THRESHOLD
    threshold = SEARCH_RELEVANCE_THRESHOLD if relevance_threshold is None else relevance_threshold
    w = weights or DEFAULT_WEIGHTS

    queries = build_queries(user_message, context_messages)
    if topic_words and topic_words[:6]:
        queries = queries + [" ".join(topic_words[:6])]
    if not queries:
        return []

    # ── Stage 1：FTS 词面召回（多 query 累加）──
    scores: dict[str, float] = {}
    for q in queries:
        for r in search_fts(q, limit=top_k * 2):
            slug = r["slug"]
            if exclude_slugs and slug in exclude_slugs:
                continue
            base = 1.8 if r.get("fallback") == "like" else 1.0
            scores[slug] = scores.get(slug, 0.0) + base * w["bm25"]

    # ── Stage 2：会话状态候选（回指联想）──
    if session_state is not None:
        for slug in session_state.recall_candidates(user_message, top_k=top_k):
            if exclude_slugs and slug in exclude_slugs:
                continue
            if slug not in scores:
                scores[slug] = 0.0
            scores[slug] += STATE_CANDIDATE_BASE * w["state"]

    if not scores:
        return []

    # ── Stage 3：时间衰减 → 排序 → 阈值过滤（单因子：排序不按重要程度标签，
    #  召回交给场景相关性；常驻性由 mem_type=profile/rules 的全量注入承担）──
    scored: list[tuple[float, dict[str, Any]]] = []
    for slug, base in scores.items():
        mem = get_memory(slug)
        if not mem:
            continue
        td = _time_decay(mem.get("updated_at"))
        time_factor = 1.0 - w["time"] + w["time"] * td
        final_score = base * time_factor
        scored.append((final_score, {
            "slug": slug,
            "description": mem.get("description", ""),
            "priority": mem.get("priority", "active"),
            "mem_type": mem.get("mem_type", "profile"),
            "content": mem.get("content", "")[:500],
            "recorded_date": mem.get("recorded_date", ""),
            "score": round(final_score, 4),
            "source": "deterministic",
        }))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = [item for _, item in scored[: top_k * 3]]
    if threshold > 0:
        results = [r for r in results if r["score"] >= threshold]
    return results[:top_k]
