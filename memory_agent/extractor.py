"""LLM 记忆提取核心。"""

import json
import logging
import re
from typing import Any, cast

import httpx

from .config import (
    EXTRACT_MAX_TOKENS,
    EXTRACT_TEMPERATURE,
    EXTRACT_TIMEOUT,
    LOG_FILE,
)
from .llm import LLMConfigurationError, complete_text
from .db import list_memories, save_memory_candidate

# ── 日志 ──
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    encoding="utf-8",
    errors="replace",  # 遇到 surrogate 等非法字符时用 ? 替换，不崩溃
)
logger = logging.getLogger("extractor")


# ── Surrogate 清理 ──
# DeepSeek API 有时返回含 lone surrogate 的文本，Python UTF-8 编码器拒绝处理。
_SURROGATE_RE = re.compile(r'[\ud800-\udfff]')


def _sanitize_surrogates(text: str) -> str:
    """移除字符串中的 lone surrogate 字符"""
    return _SURROGATE_RE.sub('', text)


def _memory_catalog_text(limit: int = 80) -> str:
    """给提取模型的轻量记忆目录，只含 slug/类型/级别/描述。"""
    try:
        memories = list_memories()
    except Exception as exc:
        logger.warning("Could not load memory catalog: %s", exc)
        return "(记忆目录读取失败，本次只判断是否创建新记忆。)"

    priority_order = {"core": 0, "important": 1, "normal": 2, "archive": 3}
    memories = sorted(
        memories,
        key=lambda m: (priority_order.get(m.get("priority", "normal"), 9), m.get("slug", "")),
    )

    lines: list[str] = []
    for mem in memories[:limit]:
        lines.append(
            "- {slug} | {priority} | {mem_type} | {description}".format(
                slug=mem.get("slug", ""),
                priority=mem.get("priority", "normal"),
                mem_type=mem.get("mem_type", "unknown"),
                description=mem.get("description", ""),
            )
        )
    return "\n".join(lines) if lines else "(当前没有已有记忆。)"

# ── 提取 Prompt ──
EXTRACT_SYSTEM_PROMPT = """你是一个「个人记忆维护器」。你的任务是分析用户最新消息，判断是否需要创建、更新或忽略长期记忆。

## 需要提取的信息类型
- **个人身份/经历**：年龄、职业、学历、居住地、家庭背景、重要人生事件
- **偏好/习惯**：有意识陈述的喜好厌恶、思维模式、交流偏好
- **困难/困扰**：健康问题（身体或心理）、正在面对的挑战、情感状态变化
- **反馈/指正**：用户对 AI 行为的批评、纠正、要求
- **决策/计划**：用户明确做出的重要决定或计划

## 不需要提取
- 纯技术讨论（代码、架构、工具用法）
- 闲聊中无持久价值的内容（但用户对他人的评价、对他人的性格分析不在此列——这些**需要**提取）
- 用户明确说「不用记住」的内容
- IDE 自动注入内容、文件打开提示、工具输出、选中文本上下文

## 关键规则：用户承接或认同 AI 观点时同样提取

当用户最新消息较短，但从对话上文可判断用户是在**承接、认同、细化或采纳** AI 此前的分析/观点时，应将这些观点视为用户已接受的认知，提取为记忆。

**识别信号**：
- 用户明确说"你说得对""我同意""我认同""确实""没错""我也觉得"
- 用户在 AI 分析基础上追加了自己的观察或修正（如"实际上他…""可能她…""就像我说的…"）
- 用户用"我同意你的方案""这个思路可以""我觉得可以"等肯定性表述回应 AI 的建议
- 用户用"你的担心我理解了""这个分析很到位"表示认可

**提取策略**：
1. 看对话上文（[assistant] / [ai] 标签），找到 AI 的完整分析段落
2. 判断用户是否在认可这些分析——用户追加修正时以修正版为准
3. 将分析中的**核心洞察**转化为用户视角的记忆，而非照抄 AI 措辞
4. content 用中文自然语言，以第三人称描述用户
5. 用户对 AI 分析做了修正或限定时（如"实际上他确实有爱好，但性格高傲"），以修正版为准

**示例**：
- AI 分析了某人的性格后，用户说"实际上他的性格有动漫高手倾向…" → 保存对该人物的性格观察
- AI 分析了用户的思维模式，用户说"对，我需要清晰框架" → 保存为认知偏好
- AI 提出方案，用户说"我同意你的方案" → 保存该决策

## 输出格式
只返回 JSON 数组。每个元素是一条维护动作：
```json
[
  {
    "action": "create|update|ignore|archive|merge",
    "slug": "短英文slug",
    "target_slug": "仅 update/merge 时填写已有记忆 slug",
    "description": "一句话描述（用于索引）",
    "content": "用中文自然语言写成的完整记忆内容。create 时写完整正文；update 时只需写新增内容，系统会追加到末尾，不要复述已有内容。",
    "mem_type": "user|feedback|project|reference"（见下方 mem_type 选择规则）,
    "priority": "core|important|normal|archive",
    "event_date": "YYYY-MM-DD（事实发生日期，从上下文推算；实在无法推算则用 null）",
    "reason": "一句话说明为什么执行此动作"
  }
]
```

## 决策规则
- 新信息明显属于已有记忆：使用 update，target_slug 必须是已有 slug。
- 新信息是独立主题：使用 create。
- 信息含糊、只是临时情绪、或和现有内容无新增：返回 []。
- 不要把不同的人合并到同一条记忆；同学/朋友等人物不确定时宁可 create 或 ignore，不要猜测合并。
- update 时 content 只需提供新增内容（不要复述或总结已有内容），系统会自动追加到正文末尾。

## mem_type 选择规则
根据信息类型选择最合适的 mem_type：

- **user**：用户的身份、经历、偏好、习惯、困扰、计划、对他人的评价
  — 这是默认值。AI 的倾听和记录对象是用户，因此大部分记忆属于此类
  - 示例："我今年大三，学计算机" → user
  - 示例："我最近压力很大" → user
  - 示例："我觉得我同学是个幻想型人格" → user（对他人性格的分析）

- **feedback**：用户对 AI 行为的批评、纠正、要求改变
  - 示例："你写得太学术了，我看不懂" → feedback
  - 示例："不要用这种语气跟我说话" → feedback
  - 示例："以后别给我推荐动漫周边了" → feedback

- **project**：项目、代码、架构级别的决策和偏好
  - 示例："我决定把数据库从 Markdown 迁到 SQLite" → project
  - 示例："以后不要用 xxx 库，换 yyy" → project
  - 示例："这个功能先不做" → project

- **reference**：用户提到的外部资料、引用、第三方信息，不直接关于用户自身
  - 示例："有一篇论文说..." → reference
  - 示例："xxx 工具的文档里写着..." → reference

当信息同时符合多个类型时，优先级：feedback > project > user > reference。


## Priority 分级标准
- **core**：身份标识、核心偏好、认知模式 — 每次会话必须加载
- **important**：健康、关系、重大决策 — 每次会话必须加载
- **normal**：日常信息、计划、一般偏好 — 按话题触发加载
- **archive**：已完成的历史事件、过时的偏好 — 深度搜索按需获取

如果没有值得保存的信息，返回空数组 []。不要编造、不要过度解读、不要保存聊天中已明显重复的信息。

注意：当前日期是 {current_date}。event_date 应根据对话上下文中提到的时间线索来推算（如"昨天""上周""上个月"），不要默认用当前日期。"""


async def extract_from_message(message: str, context: str = "") -> list[dict[str, Any]]:
    """
    从用户消息中提取记忆候选。

    Args:
        message: 用户最新消息
        context: 可选的对话上下文（前几轮对话）

    Returns:
        记忆候选列表，每条包含 slug/description/content/mem_type/priority/event_date
    """
    if not message.strip():
        return []

    # 预处理：太短的纯功能性消息不送 LLM
    if _should_skip(message):
        logger.info(f"Skipped (too short/functional): {message[:80]}")
        return []

    catalog = _memory_catalog_text()
    user_content = f"## 已有记忆目录\n{catalog}\n\n## 用户消息\n{message}"
    if context:
        user_content = f"## 对话上文\n{context}\n\n{user_content}"

    try:
        text_content = await complete_text(
            system_prompt=EXTRACT_SYSTEM_PROMPT,
            user_prompt=user_content,
            max_tokens=EXTRACT_MAX_TOKENS,
            temperature=EXTRACT_TEMPERATURE,
            timeout=EXTRACT_TIMEOUT,
        )
        text_content = _sanitize_surrogates(text_content)
        return _parse_extraction_result(text_content)

    except LLMConfigurationError as e:
        logger.error("LLM configuration error: %s", e)
        return []
    except httpx.TimeoutException:
        logger.warning("Extraction timeout (%.1fs) for: %s", EXTRACT_TIMEOUT, _sanitize_surrogates(message[:80]))
        return []
    except httpx.HTTPStatusError as e:
        logger.error(
            "HTTP %d: %s",
            e.response.status_code,
            _sanitize_surrogates(e.response.text[:200]),
        )
        return []
    except Exception as e:
        logger.error("Extraction failed: %s", _sanitize_surrogates(str(e)))
        return []


def _should_skip(message: str) -> bool:
    """跳过低信息量的纯功能性消息"""
    stripped = message.strip()

    # 太短
    if len(stripped) < 8:
        return True

    # IDE 自动注入的上下文（如 VSCode 自动附加的选中文本），不是用户自然表达
    ide_patterns = [
        r"^<ide_[^>]*>",
        r"^<command-[^>]*>",
        r"^<tool_[^>]*>",
        r"^<[^>]*(?:opened_file|selection|readonly|tool output)[^>]*>",
    ]
    for pat in ide_patterns:
        if re.match(pat, stripped, flags=re.IGNORECASE):
            return True

    # 纯命令/快捷键
    functional_patterns = [
        r"^/[a-z-]+",           # /model, /clear, /compact
        r"^(yes|no|ok|好的|嗯|对|是|行|可以|继续)$",
        r"^(ls|cd|git|npm|pip|python|node)\b",
        r"^[a-zA-Z]:\\",        # Windows 路径
    ]
    for pat in functional_patterns:
        if re.match(pat, stripped):
            return True

    return False


def should_skip_message(message: str) -> bool:
    """供 hook 层做预过滤，避免日志把跳过误写成提取。"""
    return _should_skip(message)


def _parse_extraction_result(raw: str) -> list[dict[str, Any]]:
    """解析 LLM 返回的 JSON，容错处理"""
    raw = raw.strip()
    try:
        # 尝试直接解析
        candidates = cast(list[dict[str, Any]], json.loads(raw))
        return [c for c in candidates if c.get("content")]
    except json.JSONDecodeError:
        pass

    # 尝试从 ```json ... ``` 中提取
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if m:
        try:
            candidates = cast(list[dict[str, Any]], json.loads(m.group(1)))
            return [c for c in candidates if c.get("content")]
        except json.JSONDecodeError:
            pass

    # 尝试截取第一个 JSON 数组，处理模型前后加解释文字的情况
    start = raw.find("[")
    end = raw.rfind("]")
    if 0 <= start < end:
        try:
            candidates = cast(list[dict[str, Any]], json.loads(raw[start:end + 1]))
            return [c for c in candidates if c.get("content")]
        except json.JSONDecodeError:
            pass

    logger.warning(f"Could not parse extraction result: {raw[:200]}")
    return []


async def extract_and_save(message: str, context: str = "") -> list[str]:
    """
    一步完成：提取 → 去重 → 写入。

    Returns:
        新创建的记忆文件 slug 列表
    """
    candidates = await extract_from_message(message, context)
    saved: list[str] = []

    for c in candidates:
        try:
            result = save_memory_candidate(c)
            if result:
                action, slug = result
                saved.append(f"{action}:{slug}")
                logger.info("%s: %s (priority=%s)", action, slug, c.get("priority"))
            else:
                logger.info(f"Dedup skipped: {c.get('slug')}")
        except Exception as e:
            logger.error(f"Write failed for {c.get('slug')}: {e}")

    return saved
