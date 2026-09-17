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

import jieba

# ── 轻量同义词/别名表（L1 词面近义扩展，确定性、可维护）──
# 覆盖高频近义：检索时关键词命中即扩展同义词 query，替代向量语义的部分能力
ALIASES: dict[str, list[str]] = {
    "编程": ["写代码", "开发", "代码", "程序员", "软件工程师"],
    "写代码": ["编程", "开发", "代码", "软件工程师", "后端"],
    "代码": ["编程", "开发", "软件工程师"],
    "程序员": ["编程", "写代码", "软件工程师"],
    "语言": ["编程", "Python", "代码"],
    "弟": ["弟弟", "哥哥", "家人"],
    "读书": ["上学", "读大学", "大学", "上海"],
    "上学": ["读书", "读大学", "学校"],
    "弟": ["弟弟"],
    "读书": ["上学", "读大学", "大学"],
    "上学": ["读书", "读大学"],
    "练琴": ["钢琴", "弹琴", "学琴"],
    "钢琴": ["练琴", "学琴", "弹琴"],
    "跑步": ["马拉松", "晨跑", "半马", "跑"],
    "马拉松": ["跑步", "半马"],
    "吃辣": ["辣", "辣椒", "火锅"],
    "胃": ["肠胃", "胃疼"],
    "德语": ["学德语", "语言", "德语课"],
    "语言": ["德语", "英语"],
    "工作": ["职业", "上班", "公司", "后端"],
    "后端": ["工作", "代码", "Python"],
    "回复": ["回答", "说话", "回话"],
    "长文": ["长篇大论", "很长", "啰嗦"],
    "简洁": ["简单", "直接", "结论先行", "短"],
    "兴趣": ["爱好", "兴趣班", "爱好"],
    "老师": ["教练", "课程", "上课"],
    "计划": ["打算", "目标", "安排"],
    "坚持": ["毅力", "持续", "保持"],
    "累": ["疲惫", "辛苦", "压力"],
    "健康": ["身体", "生病", "哮喘"],
    "哮喘": ["呼吸", "过敏", "生病"],
    "嘴馋": ["爱吃辣", "馋", "吃辣", "管不住嘴"],
    "馋": ["爱吃辣", "嘴馋", "吃辣"],
    "加量": ["跑步", "马拉松", "训练", "跑量"],
    "毛病": ["老毛病", "习惯", "问题"],
    "兴趣班": ["钢琴", "兴趣", "爱好", "上课"],
    "退堂鼓": ["放弃", "坚持", "半途而废"],
    "语言班": ["德语", "学习", "学德语"],
    "视唱练耳": ["钢琴", "练琴", "弹琴"],
}


# ── 语气叠词（"嗯嗯/哦哦/哈哈"等，不构成检索关键词）──

_SOUND_WORDS = re.compile(r"^(嗯|哦|啊|哈|呀|呐|嘛|欸|唉|嗨|哼|啧)\1{0,3}$")


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
    "今天", "明天", "昨天", "现在", "当时", "最近",
    "天气", "感觉", "情况", "怎么样", "如何",
    "自己", "一个", "一次", "一点", "一下", "一起",
    "其实", "反正", "确实", "当然", "真的", "比较", "特别", "非常", "有点", "有些",
    "可以", "应该", "可能", "大概", "也许", "已经", "一直", "总是", "还是", "就是",
    "但是", "因为", "所以", "如果", "然后", "而且", "不过", "只是", "只要", "比如",
    "这个", "那个", "这些", "那些", "这样", "那样", "怎么", "什么", "为什么",
    "上次", "这次", "上次", "打算", "准备", "觉得", "知道", "明白", "理解",
    "事情", "时候", "地方", "方式", "办法", "想法", "看法", "方面", "问题", "原因",
    "东西", "朋友", "时间", "阶段", "方向", "目的", "目标", "计划",
    "想", "说", "做", "去", "来", "看", "知", "觉得",
    "人", "东西", "时候", "地方", "方式", "事情",
    "多", "少", "大", "小", "长", "短", "高", "低",
})


_STOP_CHARS = frozenset(
    "的了在是我你他她它们这那哪吗吧呢啊哦嗯哈"
    "有没不就也都还要会能可以应该可能很太真好让给把被"
    "和与或但而因为所以如果虽然但是而且然后还是或者对在从向把被跟"
    "想说做去来看知觉得人东西时候地方方式事情多少大小长短高低"
    "今天明天昨天现在当时最近天气感觉情况怎么样如何"
)


def tokenize(text: str) -> list[str]:
    """jieba 分词 + 过滤（确定性词典分词，零模型）。

    替代手写 bigram：正确切出"写代码/马拉松"等词，不再产生碎片。
    过滤：停用词、单字停用、纯标点/数字片段。
    """
    tokens: list[str] = []
    for w in jieba.cut(text):
        w = w.strip()
        if not w:
            continue
        if w in _STOPWORDS or _SOUND_WORDS.match(w):
            continue
        if len(w) == 1 and w in _STOP_CHARS:
            continue
        if not re.search(r"[\w\u4e00-\u9fff]", w):
            continue
        tokens.append(w)
    # 去重（保留顺序）
    seen: set[str] = set()
    result: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def _is_meaningful_phrase(phrase: str) -> bool:
    """短语去掉停用字后是否还有实义核心（至少 2 个实字）。"""
    remaining = [c for c in phrase if c not in _STOP_CHARS]
    return len(remaining) >= 2


def extract_topic_grams(text: str, max_grams: int = 12) -> list[str]:
    """提取主题词（jieba 分词结果），供会话主题轨迹使用。"""
    return tokenize(text)[:max_grams]


def filter_topic_words(freq: dict[str, int], limit: int = 20) -> list[str]:
    """按频率降序取主题词，过滤被更长词覆盖的子串半词。

    半词（"己定/马拉"）会挤占主题词位置，需在累计后丢弃。
    """
    # 长词优先：完整词（"自己定/马拉松"）先保留，子串半词（"己定/马拉"）后处理并丢弃
    ranked = sorted(freq, key=lambda w: (freq[w], len(w)), reverse=True)
    kept: list[str] = []
    for w in ranked:
        if any(w in o for o in kept):
            continue
        kept.append(w)
        if len(kept) >= limit:
            break
    return kept


def extract_phrases(text: str) -> list[str]:
    """实义短语（jieba 分词结果，不含碎片）。"""
    return tokenize(text)


def _extract_keywords(text: str, max_words: int = 8) -> list[str]:
    """检索关键词（jieba 分词 + 同义词扩展）。"""
    return tokenize(text)[:max_words]


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

    # 同义词扩展（L1 近义）：关键词命中 ALIASES 时追加同义词
    for k in list(keywords[:5]):
        for alias in ALIASES.get(k, []):
            if alias not in keywords:
                keywords.append(alias)

    # 对话主题联想：从最近几轮对话合成主题关键词
    # （联想核心 —— 人脑的联想由"对话主题 + 当前话语"共同触发）
    ctx_keywords: list[str] = []
    if context_messages:
        combined = " ".join(context_messages[-4:])  # 最近 4 轮
        ctx_keywords = _extract_keywords(combined)

    # 当前消息无关键词 → 完全靠对话主题联想兜底（"然后呢？/继续"等）
    if not keywords and not ctx_keywords:
        return []

    if not keywords:
        queries.append(" ".join(ctx_keywords[:4]))
        return _dedupe(queries)[:5]

    # Query 1：完整用户消息（去停用词后）
    queries.append(" ".join(keywords))

    # Query 2-3：用户消息中最强的 3-4 个词（缩小范围提高精度）
    if len(keywords) >= 3:
        queries.append(" ".join(keywords[:3]))
    if len(keywords) >= 4:
        queries.append(" ".join(keywords[:4]))

    # Query 4：对话主题联想（独立 query，捕捉话赶话的关联）
    if ctx_keywords:
        queries.append(" ".join(ctx_keywords[:4]))

    # Query 5：个人话题（如果检测到）
    if _is_personal_query(user_message):
        queries.append(" ".join(keywords[:2]))

    return _dedupe(queries)[:5]


def _dedupe(queries: list[str]) -> list[str]:
    """去重并去除空白（保留顺序）。"""
    seen: set[str] = set()
    deduped: list[str] = []
    for q in queries:
        qn = q.strip()
        if qn and qn not in seen:
            seen.add(qn)
            deduped.append(qn)
    return deduped
