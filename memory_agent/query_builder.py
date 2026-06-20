"""
query_builder.py — 从对话上下文生成检索查询

将用户消息转换为 2-5 条检索 query，覆盖不同角度。
当前使用启发式规则（零 LLM 调用），后续可加 LLM 扩展。

设计：
- Query 1-2：提取用户消息中的关键词/名词短语
- Query 3：如果消息是个人相关，加入通用前缀
- 中文/英文混合处理
"""

from __future__ import annotations

import re
from typing import Any

# ── 中文停用词（检索时不应作为关键词）──
_STOPWORDS = frozenset({
    "的", "了", "在", "是", "我", "你", "他", "她", "它",
    "们", "这", "那", "哪", "什么", "怎么", "为什么",
    "吗", "吧", "呢", "啊", "哦", "嗯", "哈",
    "有", "没", "不", "就", "也", "还", "都", "要",
    "会", "能", "可以", "应该", "可能",
    "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
    "个", "种", "样", "些", "点",
    "很", "太", "真", "好", "让", "给", "把", "被",
    "和", "与", "或", "但", "而", "因为", "所以", "如果",
    "虽然", "但是", "而且", "然后", "还是", "或者",
    "这个", "那个", "这些", "那些",
    "想", "说", "做", "去", "来", "看", "知", "觉得",
    "人", "东西", "时候", "地方", "方式", "事情",
    "多", "少", "大", "小", "长", "短", "高", "低",
})


def _extract_keywords(text: str, max_words: int = 8) -> list[str]:
    """
    从文本中提取有意义的检索关键词。

    策略：
    1. 移除停用词和标点
    2. 按长度 + 信息量排序
    3. 保留中文 bilgram 和英文完整词
    """
    # 清理标点
    clean = re.sub(r"[^\w\s一-鿿]", " ", text)

    # 提取英文词
    en_words = re.findall(r"[a-zA-Z][a-zA-Z0-9]{1,}", clean)
    # 提取中文连续片段（至少 2 字才有信息量）
    cn_phrases = re.findall(r"[一-鿿]{2,}", clean)

    candidates: list[str] = []
    for w in en_words:
        wl = w.lower()
        if wl not in _STOPWORDS and len(wl) >= 2:
            candidates.append(wl)
    for p in cn_phrases:
        if p not in _STOPWORDS:
            candidates.append(p)
            # 也拆成 bilgram 提高召回
            if len(p) >= 4:
                candidates.extend(p[i:i+2] for i in range(len(p)-1))
                candidates.append(p[:3])  # trigram 前缀
                candidates.append(p[-3:])  # trigram 后缀

    # 去重、保留顺序
    seen: set[str] = set()
    result: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            result.append(c)

    return result[:max_words]


def _is_personal_query(text: str) -> bool:
    """判断是否涉及个人话题（需要检索用户记忆）。"""
    personal_indicators = [
        r"我\S*", r"自己", r"我的", r"感觉", r"觉得",
        r"压力", r"焦虑", r"担心", r"喜欢", r"讨厌",
        r"同学", r"朋友", r"家人", r"室友",
        r"最近", r"以前", r"之前", r"过去",
        r"想", r"打算", r"计划", r"决定",
        r"学校", r"专业", r"工作", r"实习",
    ]
    return any(re.search(p, text) for p in personal_indicators)


def build_queries(
    user_message: str,
    context_messages: list[str] | None = None,
) -> list[str]:
    """
    从用户消息和上下文生成 2-5 条检索 query。

    Args:
        user_message: 当前用户消息
        context_messages: 最近几轮对话（可选）

    Returns:
        检索 query 列表，按优先级降序
    """
    queries: list[str] = []
    keywords = _extract_keywords(user_message)

    # Query 1：完整用户消息（去停用词后）
    if keywords:
        queries.append(" ".join(keywords))

    # Query 2-3：用户消息中最强的 3-4 个词（缩小范围提高精度）
    if len(keywords) >= 3:
        queries.append(" ".join(keywords[:3]))
    if len(keywords) >= 4:
        queries.append(" ".join(keywords[:4]))

    # Query 4：个人话题（如果检测到）
    if _is_personal_query(user_message):
        personal_prefixes = [
            " ".join(keywords[:2]) if keywords else user_message,
        ]
        queries.extend(personal_prefixes)

    # Query 5：从上文提取关键词（如有上下文）
    if context_messages:
        combined = " ".join(context_messages[-2:])  # 最近两轮
        ctx_keywords = _extract_keywords(combined)
        if ctx_keywords:
            queries.append(" ".join(ctx_keywords[:4]))

    # 去重（保留顺序）
    seen: set[str] = set()
    deduped: list[str] = []
    for q in queries:
        qn = q.strip()
        if qn and qn not in seen:
            seen.add(qn)
            deduped.append(qn)

    return deduped[:5]
