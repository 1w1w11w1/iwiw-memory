"""
triggers.py — 高信号消息实时触发检测

检测用户消息中是否包含应立刻提取为长期记忆的信号。
纯规则驱动（零 LLM 调用），低开销、高精度。

触发规则：
- 显式偏好声明："我喜欢""我讨厌""我更倾向"
- 纠正/反馈："不对""你错了""你不要"
- 决策/计划："我决定""我打算""我计划"
- 重要生活事件："我找到工作了""我分手了"
- 明确叫 AI 记住的话："记住""记一下"

返回 True 时调用方应立即执行 extract_and_save()。
"""

from __future__ import annotations

import re

# ── 高速信号模式 ──
# 每条规则匹配后立即触发提取，不做二次判断

_HIGH_SIGNAL_PATTERNS: list[re.Pattern] = [
    # ── 偏好转折 ──
    re.compile(r"(其实|说实话|坦白说|认真说)\s*我\s*(更|比较|特别|非常|有点|有些)"),
    re.compile(r"我更(喜欢|倾向|愿意|习惯|擅长)"),
    re.compile(r"我(最|特别|非常|很)(喜欢|讨厌|反感|抗拒|享受|痴迷)\w{0,8}"),
    re.compile(r"(好吃|难吃|好看|难看|好用|难用|好玩|无聊)\w{0,4}，(但|不过|可是)"),

    # ── 决策 ──
    re.compile(r"我(决定|打算|计划|准备|想试着)\s*.+"),
    re.compile(r"(以后|从今以后|再也不|从此)\s*.+"),
    re.compile(r"还是\s*.{1,6}\s*(吧|算了)"),
    re.compile(r"(放弃|坚持|开始|停止)\s*.+"),
    re.compile(r"^(决定|打算|准备|计划)\s*.{2,}"),

    # ── 反馈/纠正 ──
    re.compile(r"(不对|错了|不是这样|不要这样|你别|别这样)"),
    re.compile(r"你(别|不要|不该|不能|少)\s*\w{2,}"),
    re.compile(r"你(太|总是|老是|动不动|天天)\s*\w{2,}"),
    re.compile(r"不要(用|说|提|聊|问)\s*\w"),

    # ── 重要事件 ──
    re.compile(r"(找到|拿到|收到|获得|通过)\s*.{1,6}(工作|offer|实习|录取|通知)"),
    re.compile(r"(分手|和好|复合|脱单|在一起)\s*(了|啦)"),
    re.compile(r"(确诊|住院|住院|手术|体检)\w{0,4}"),
    re.compile(r"(爸妈|父母|家人)\s*(说|让|叫|要)\s*\w{2,}"),

    # ── 明确要求记忆 ──
    re.compile(r"(记住|记一下|你先记着|记住我说的话)[：:\s,，]"),
    re.compile(r"(这个人|这东西|这个地方|这家店)\s*(是|叫|在)"),
    re.compile(r"以后(买|送|给|做)\s*.{1,8}(的时候|的话)"),

    # ── 情绪转折 ──
    re.compile(r"(最近|这段时间)\s*(好|很|特别|真的)\s*(累|烦|焦虑|开心|难受|压力大|焦虑|迷茫)"),
    re.compile(r"(最近|这段时间)\s*(压力|情绪)\s*(好大|很大|不好|低落)"),
    re.compile(r"好\s*(累|烦|焦虑|开心|难受|迷茫|抑郁|压抑|困)"),
    re.compile(r"(压力|焦虑|失眠|难受)\s*(好大|很大|严重|频繁)"),
    re.compile(r"(终于|总算)\s*\w{2,8}\s*(了|啦)"),
]

# ── 低信号排除模式（否定匹配）──
_LOW_SIGNAL_PATTERNS: list[re.Pattern] = [
    # 纯技术提问
    re.compile(r"^(怎么|如何|怎样)\s*(写|做|实现|配置|安装|部署|调试|测试).{0,20}$"),
    # 简短确认
    re.compile(r"^(好的|好|嗯|对|是|行|可以|ok|yes|no|没错|确实|明白|懂了)$", re.IGNORECASE),
    # 代码提问
    re.compile(r"[{}().;=#]"),  # 含常见代码符号
]


def should_trigger(message: str) -> bool:
    """
    判断一条消息是否应触发实时提取。

    Args:
        message: 用户最新消息

    Returns:
        True → 应立刻调 extract_and_save()
    """
    if not message or len(message.strip()) < 4:
        return False

    stripped = message.strip()

    # 低信号排除
    for pat in _LOW_SIGNAL_PATTERNS:
        if pat.match(stripped):
            return False

    # 高信号匹配
    for pat in _HIGH_SIGNAL_PATTERNS:
        if pat.search(stripped):
            return True

    return False
