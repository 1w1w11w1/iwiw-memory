"""
session_state.py — 会话状态检查点（DSC，Deterministic Session Checkpoint）。

确定性、零 LLM、零向量模型。作为"联想（L2 上下文联想）"与"上下文压缩"的共同基础：

1. 联想：记录会话中讨论过的话题（关联记忆 slug）、已引用记忆、已建立决策/任务。
   - 回指联想（"那件事/那个兴趣班"）不再靠向量相似度，而是匹配会话状态里
     "最近/高频讨论过的话题"所关联的记忆。
2. 压缩（DSC）：把会话状态渲染为检查点视图，上下文超预算时重置历史 + 注入视图。

数据模型（内存态，会话结束即弃，不持久化）：
- topics    : 话题条目（label + 关联记忆 slug + mentions + last_turn）
- recalled  : slug -> last_turn（已联想注入过的记忆，窗口去重用）
- decisions : 用户明确决策（"我决定/打算/计划..."）
- tasks     : 进行中任务（"我要/准备/计划做..."）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ── 决策 / 任务 抽取（确定性规则）──

_DECISION_RE = re.compile(
    r"(?:我)?(?:已经|就|反正)?(决定|打算|计划|准备|确定|约定)"
    r"(?:要|去|做|学|买|参加|开始|放弃|坚持|戒|换|改)?"
    r"[^。！？\n]{2,30}"
)

_TASK_RE = re.compile(
    r"(?:我)?(?:要|打算|计划|准备|决定)(?:去|做|写|学|买|参加|完成|实现)"
    r"[^。！？\n]{2,40}"
)


@dataclass
class TopicEntry:
    label: str                 # 话题主题词，如"马拉松"
    slug: str | None = None    # 关联记忆 slug（词面匹配到）
    mentions: int = 1
    last_turn: int = 0


class SessionState:
    """会话状态检查点。"""

    def __init__(self) -> None:
        self.topics: list[TopicEntry] = []
        self.recalled: dict[str, int] = {}
        self.decisions: list[str] = []
        self.tasks: list[str] = []
        self._turn = 0
        # 记忆词面倒排索引：词 -> [slug]（会话启动时构建）
        self._word_index: dict[str, list[str]] = {}

    # ── 构建记忆词面倒排索引 ──
    def build_word_index(self, memories: list[dict[str, Any]]) -> None:
        """从记忆库构建 词 -> [slug] 倒排索引（jieba 分词）。"""
        from .query_builder import tokenize
        self._word_index = {}
        for m in memories:
            slug = m["slug"]
            text = f"{slug} {m.get('description', '')} {m.get('content', '')}"
            for w in tokenize(text):
                if w not in self._word_index:
                    self._word_index[w] = []
                if slug not in self._word_index[w]:
                    self._word_index[w].append(slug)

    def _match_slug(self, label: str) -> str | None:
        """话题词匹配记忆：整词命中优先，其次 label 含某记忆词的包含匹配。"""
        if not self._word_index:
            return None
        if label in self._word_index:
            return self._word_index[label][0]
        best: tuple[int, str | None] = (0, None)
        for w, slugs in self._word_index.items():
            if len(w) >= 2 and w in label and len(w) > best[0]:
                best = (len(w), slugs[0])
        return best[1]

    # ── 每轮更新 ──
    def update(self, user_msg: str, topic_grams: list[str]) -> None:
        """用户消息后调用：更新话题、决策、任务。"""
        self._turn += 1

        # 话题：topic_grams 里的词，关联记忆 slug
        for g in topic_grams:
            existing = next((t for t in self.topics if t.label == g), None)
            if existing:
                existing.mentions += 1
                existing.last_turn = self._turn
            else:
                entry = TopicEntry(label=g, slug=self._match_slug(g), last_turn=self._turn)
                self.topics.append(entry)

        # 决策 / 任务
        for m in _DECISION_RE.finditer(user_msg):
            s = m.group(0).strip()
            if s and s not in self.decisions:
                self.decisions.append(s)
        for m in _TASK_RE.finditer(user_msg):
            s = m.group(0).strip()
            if s and s not in self.tasks:
                self.tasks.append(s)

    def note_recalled(self, slugs: set[str]) -> None:
        """记录本轮联想注入的记忆（用于窗口去重）。"""
        for s in slugs:
            self.recalled[s] = self._turn

    # ── 联想候选 ──
    def recall_candidates(self, user_msg: str, top_k: int = 3) -> list[str]:
        """回指联想候选（确定性）：

        1. 词面匹配：用户消息关键词命中话题 label 或记忆倒排 → 对应记忆 slug；
        2. 无词面命中时，取"最近 + 高频"讨论过的话题所关联的记忆 slug。
        返回去重后的 slug 列表（按相关性排序）。
        """
        # 1) 词面：用户消息里的词（jieba 分词，与记忆倒排一致）
        from .query_builder import tokenize
        msg_words = set(tokenize(user_msg))
        hits: list[str] = []

        # 用户消息词直接命中记忆倒排
        for w in msg_words:
            for slug in self._word_index.get(w, [])[:2]:
                if slug not in hits:
                    hits.append(slug)

        # 用户消息词命中话题 label → 关联记忆
        for t in self.topics:
            if any(w in t.label for w in msg_words) and t.slug and t.slug not in hits:
                hits.append(t.slug)

        if hits:
            return hits[:top_k]

        # 2) 无词面 → 最近/高频话题关联记忆
        ranked = sorted(
            [t for t in self.topics if t.slug],
            key=lambda t: (t.mentions, t.last_turn),
            reverse=True,
        )
        result: list[str] = []
        for t in ranked:
            if t.slug and t.slug not in result:
                result.append(t.slug)
            if len(result) >= top_k:
                break
        return result

    # ── 压缩检查点视图 ──
    def render_checkpoint(self, max_topics: int = 8) -> str:
        """渲染为压缩后注入的检查点视图（DSC）。"""
        parts: list[str] = ["## 会话状态检查点"]
        if self.decisions:
            parts.append("- 已决定：" + "；".join(self.decisions[-5:]))
        if self.tasks:
            parts.append("- 进行中：" + "；".join(self.tasks[-5:]))
        ranked = sorted(
            [t for t in self.topics if t.slug],
            key=lambda t: (t.mentions, t.last_turn),
            reverse=True,
        )[:max_topics]
        if ranked:
            lines = []
            for t in ranked:
                lines.append(f"- {t.label} -> 记忆 {t.slug}（提及 {t.mentions} 次）")
            parts.append("## 已讨论话题")
            parts.extend(lines)
        return "\n".join(parts) if len(parts) > 1 else ""
