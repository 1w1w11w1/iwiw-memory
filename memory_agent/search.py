"""
search.py — 混合搜索（BM25 全文 + 关键词加权 + 规划中向量语义检索）

设计决策：
- 纯 Python 实现 BM25（零外部依赖），适配 markdown 记忆文件
- 双层排序：BM25 初筛 → 时间衰减重排（时间轴概念来自 Zep 设计）
- TODO: 接入向量嵌入语义检索（embedding + cosine similarity 混合排序）
"""

import re
import math
from collections import defaultdict
from datetime import datetime
from typing import Optional

from .config import MEMORY_DIR
from .store import get_all_content_for_search


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BM25 实现（纯 Python，零依赖）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _tokenize(text: str) -> list[str]:
    """中文/英文混合分词"""
    # 英文：按空白和标点分割
    # 中文：按单字 + 2-gram
    tokens = []
    # 提取英文词
    en_words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    tokens.extend(en_words)
    # 提取中文连续片段
    cn_segments = re.findall(r"[一-鿿]+", text)
    for seg in cn_segments:
        # 单字
        tokens.extend(list(seg))
        # bigram
        if len(seg) >= 2:
            tokens.extend(seg[i:i+2] for i in range(len(seg) - 1))
    return tokens


class BM25:
    """
    Okapi BM25 全文检索

    k1: 词频饱和参数（默认 1.5）
    b:  文档长度归一化参数（默认 0.75）
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs: list[dict] = []
        self.doc_tokens: list[list[str]] = []
        self.doc_freqs: dict[str, int] = defaultdict(int)
        self.avgdl: float = 0.0
        self.N: int = 0

    def index(self, docs: list[dict]) -> None:
        """建立索引"""
        self.docs = docs
        self.doc_tokens = []
        self.doc_freqs = defaultdict(int)

        for doc in docs:
            text = f"{doc.get('description', '')} {doc.get('content', '')}"
            tokens = _tokenize(text)
            self.doc_tokens.append(tokens)

            unique = set(tokens)
            for t in unique:
                self.doc_freqs[t] += 1

        self.N = len(docs)
        total_len = sum(len(t) for t in self.doc_tokens)
        self.avgdl = total_len / self.N if self.N > 0 else 1.0

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """BM25 搜索，返回带分数的文档列表"""
        if self.N == 0:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores = []
        for i, tokens in enumerate(self.doc_tokens):
            score = self._score(query_tokens, tokens)
            if score > 0:
                scores.append((i, score))

        scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scores[:top_k]:
            doc = dict(self.docs[idx])
            doc["bm25_score"] = round(score, 4)
            results.append(doc)
        return results

    def _score(self, query_tokens: list[str], doc_tokens: list[str]) -> float:
        score = 0.0
        doc_len = len(doc_tokens)
        tf_counts = defaultdict(int)
        for t in doc_tokens:
            tf_counts[t] += 1

        for qt in query_tokens:
            tf = tf_counts.get(qt, 0)
            if tf == 0:
                continue
            df = self.doc_freqs.get(qt, 0)
            if df == 0:
                continue

            idf = math.log((self.N - df + 0.5) / (df + 0.5) + 1.0)
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)
            score += idf * numerator / denominator

        return score


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 时间衰减重排
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _time_decay_weight(mtime, half_life_days: int = 90) -> float:
    """
    时间衰减权重。
    half_life_days: 半衰期（天），90 天后权重降为 0.5
    """
    if isinstance(mtime, str):
        mtime = datetime.fromisoformat(mtime)
    now = datetime.now()
    age_days = (now - mtime).total_seconds() / 86400.0
    return math.exp(-math.log(2) * age_days / half_life_days)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 混合搜索入口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_bm25_index: Optional[BM25] = None
_indexed_docs: list[dict] = []


def _get_index() -> BM25:
    """懒加载 BM25 索引"""
    global _bm25_index, _indexed_docs
    current_docs = get_all_content_for_search()

    # 检查是否需要重建索引（通过比较 slug 集合）
    if _bm25_index is None or len(current_docs) != len(_indexed_docs):
        _bm25_index = BM25()
        _bm25_index.index(current_docs)
        _indexed_docs = current_docs

    return _bm25_index


def search_memories(
    query: str,
    top_k: int = 10,
    time_weight: float = 0.3,
    priority_boost: dict[str, float] | None = None,
) -> list[dict]:
    """
    混合搜索：BM25 全文检索 + 时间衰减 + Priority 加权。

    Args:
        query: 搜索查询
        top_k: 返回结果数
        time_weight: 时间衰减在最终分数中的权重（0-1）
        priority_boost: priority → 乘数，默认 {"core": 1.5, "important": 1.2, "normal": 1.0, "archive": 0.8}
    """
    if priority_boost is None:
        priority_boost = {"core": 1.5, "important": 1.2, "normal": 1.0, "archive": 0.8}

    bm25 = _get_index()
    bm25_results = bm25.search(query, top_k=top_k * 2)  # 扩大候选池

    # 混合打分：BM25 × time_decay × priority_boost
    for doc in bm25_results:
        bm25_score = doc.get("bm25_score", 0)
        time_decay = _time_decay_weight(doc.get("mtime", datetime.now()))
        p_boost = priority_boost.get(doc.get("priority", "normal"), 1.0)

        # 混合分数
        doc["hybrid_score"] = round(
            bm25_score * (1.0 - time_weight + time_weight * time_decay) * p_boost,
            4,
        )
        doc["time_decay"] = round(time_decay, 4)

    # 按混合分数重排
    bm25_results.sort(key=lambda d: d.get("hybrid_score", 0), reverse=True)

    return bm25_results[:top_k]
