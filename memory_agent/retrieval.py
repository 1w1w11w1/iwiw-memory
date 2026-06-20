"""
retrieval.py — 混合检索：向量 + FTS + 时间衰减 + Priority 加权

架构：
1. 对每条 query 同时做向量检索和 FTS5 检索
2. RRF（Reciprocal Rank Fusion）合并多 query 结果
3. 应用时间衰减和 priority 加权
4. 相关性阈值过滤 → 返回 top-K

数据流：
  user_message → query_builder.build_queries()
                      ↓
              ┌───────┴───────┐
          向量检索          FTS5 检索
              └───────┬───────┘
                      ↓
              RRF 融合排序
                      ↓
            时间衰减 × priority 加权
                      ↓
             相关性阈值过滤 → top-K 结果
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from .db import search_vectors, search_fts, touch_memory
from .embedding import embed_text, cosine_similarity

# ── 默认权重（可通过 config 覆盖）──
DEFAULT_WEIGHTS = {
    "semantic": 0.5,   # 向量语义权重
    "bm25": 0.2,       # FTS5 关键词权重
    "time": 0.15,      # 时间衰减权重
    "priority": 0.15,  # Priority 加权
}

PRIORITY_BOOST = {
    "core": 1.5,
    "important": 1.2,
    "normal": 1.0,
    "archive": 0.8,
}

TIME_HALF_LIFE_DAYS = 90  # 90 天半衰期


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


def hybrid_search(
    user_message: str,
    context_messages: list[str] | None = None,
    top_k: int = 5,
    relevance_threshold: float = 0.35,
    weights: dict[str, float] | None = None,
    exclude_slugs: set[str] | None = None,
) -> list[dict[str, Any]]:
    """
    混合检索入口。

    Args:
        user_message: 用户当前消息
        context_messages: 最近几轮对话
        top_k: 返回结果数
        relevance_threshold: 相关性阈值，低于此值不返回
        weights: 各因子权重，默认 semantic=0.5, bm25=0.2, time=0.15, priority=0.15
        exclude_slugs: 排除的记忆 slug（避免重复注入已加载的 L0/L1）

    Returns:
        每条含 slug/description/priority/similarity/source 等字段
    """
    from .query_builder import build_queries

    w = weights or DEFAULT_WEIGHTS
    queries = build_queries(user_message, context_messages)
    if not queries:
        return []

    # ── Stage 1：对每条 query 做向量检索和 FTS5 检索 ──
    vector_scores: dict[str, list[float]] = {}  # slug → [score_per_query]
    fts_scores: dict[str, list[float]] = {}

    for query in queries:
        # 向量检索
        q_vec = embed_text(query)
        if q_vec:
            v_results = search_vectors(q_vec, top_k=top_k * 2)
            for r in v_results:
                slug = r["slug"]
                if exclude_slugs and slug in exclude_slugs:
                    continue
                if slug not in vector_scores:
                    vector_scores[slug] = []
                vector_scores[slug].append(r.get("similarity", 0))

        # FTS5 检索
        f_results = search_fts(query, limit=top_k * 2)
        for r in f_results:
            if "error" in r:
                continue
            slug = r["slug"]
            if exclude_slugs and slug in exclude_slugs:
                continue
            if slug not in fts_scores:
                fts_scores[slug] = []
            # FTS5 不返回分数，使用 rank 倒数作为近似分数
            fts_scores[slug].append(1.0)

    if not vector_scores and not fts_scores:
        return []

    # ── Stage 2：RRF 融合 ──
    all_slugs = set(vector_scores.keys()) | set(fts_scores.keys())

    from .db import get_memory
    scored: list[tuple[float, dict[str, Any]]] = []

    for slug in all_slugs:
        mem = get_memory(slug)
        if not mem:
            continue

        # 向量 RRF 分数
        v_rank_score = 0.0
        if slug in vector_scores:
            for score in vector_scores[slug]:
                v_rank_score += score * w["semantic"]

        # FTS RRF 分数
        f_rank_score = 0.0
        if slug in fts_scores:
            f_rank_score += len(fts_scores[slug]) * w["bm25"]

        # 基础分数
        base_score = v_rank_score + f_rank_score
        if base_score == 0:
            continue

        # 时间衰减
        td = _time_decay(mem.get("updated_at"))
        time_factor = 1.0 - w["time"] + w["time"] * td

        # Priority 加权
        p_boost = PRIORITY_BOOST.get(mem.get("priority", "normal"), 1.0)
        priority_factor = 1.0 + (p_boost - 1.0) * w["priority"]

        # 最终分数
        final_score = base_score * time_factor * priority_factor

        scored.append((final_score, {
            "slug": slug,
            "description": mem.get("description", ""),
            "priority": mem.get("priority", "normal"),
            "mem_type": mem.get("mem_type", "user"),
            "content": mem.get("content", "")[:500],
            "score": round(final_score, 4),
            "time_decay": round(td, 3),
            "source": "hybrid",
        }))

    # ── Stage 3：排序 + 阈值过滤 ──
    scored.sort(key=lambda x: x[0], reverse=True)
    results = [item for _, item in scored[:top_k * 3]]
    # 只返回超过阈值的结果
    if relevance_threshold > 0:
        results = [r for r in results if r["score"] >= relevance_threshold]

    return results[:top_k]


def format_memory_context(
    memories: list[dict[str, Any]],
    max_total_chars: int = 2000,
) -> str:
    """
    将检索到的记忆格式化为注入到 system prompt 的文本。

    格式：
    ## 相关记忆（语义检索）
    - [slug] (priority) — description
      content 摘要...
    """
    if not memories:
        return ""

    lines: list[str] = [
        "## 相关记忆（语义检索）",
        "> 以下是从长期记忆中检索到的与当前话题相关的内容。",
        "> 如需查看更多或修改记忆，请打开记忆管理面板。",
        "",
    ]

    chars_used = 0
    for mem in memories:
        header = f"- **{mem['slug']}** ({mem['priority']}, {mem['mem_type']}) — {mem.get('description', '')}  [score={mem.get('score', 0)}]"
        body = mem.get("content", "")[:300]
        entry = f"{header}\n  {body}\n"

        if chars_used + len(entry) > max_total_chars:
            break
        lines.append(entry)
        chars_used += len(entry)

    if len(lines) <= 4:
        return ""

    return "\n".join(lines)
