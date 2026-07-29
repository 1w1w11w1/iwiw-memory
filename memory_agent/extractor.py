"""
extractor.py — 记忆提取核心

架构变更：
  全量记忆库写入不再经过 LLM 裁决。原始材料确定性存入 memory_records。
  倾向观察（tendency_observations）仍可由 LLM 提取，作为 TendencyCompiler 的输入。

两条路径：
  1. ingest_message() — 确定性写入，无 LLM 调用
  2. extract_tendency_observations() — LLM 分析，提取行为倾向信号
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, cast

from selfecho_model import ModelCallConfig, ModelGateway

from .config import (
    EXTRACT_MAX_TOKENS,
    EXTRACT_TEMPERATURE,
    EXTRACT_TIMEOUT,
    LOG_FILE,
)

# ── 日志 ──
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    encoding="utf-8",
    errors="replace",
)
logger = logging.getLogger("extractor")


# ── Surrogate 清理（DeepSeek API 偶尔返回）──
_SURROGATE_RE = re.compile(r'[\ud800-\udfff]')


def _sanitize_surrogates(text: str) -> str:
    return _SURROGATE_RE.sub("", text)


# ── 倾向提取 Prompt ──
TENDENCY_EXTRACT_PROMPT = """你是一个「个人倾向维护器」。你的任务是分析用户最新消息和对话上下文，识别用户的行为偏好、纠正、反馈或长期合作模式，整理为倾向观察（tendency observation）。

## 需要提取的倾向信号
- **行为纠正**：用户对 AI 的语气、长度、格式、风格的明确要求
- **长期偏好**：用户喜欢的合作方式、决定模式、规避的内容
- **重要决策**：用户明确做出的会影响后续合作的原则性决定
- **反馈**：用户的正面或负面反馈，说明什么好什么不好

## 不需要提取
- 纯技术讨论（代码、架构、工具用法）
- 闲聊中无持久价值的内容
- 事实性信息（事实应走全量记忆库，不由倾向观察承担）

## 输出格式
只返回 JSON 数组。没有倾向信号时返回空数组 []。
```json
[
  {
    "scope_kind": "agent_global|workspace|session",
    "content": "用中文自然语言描述该倾向。第三人称，完整一句话说明用户在什么情况下倾向于什么行为。",
    "project_id": null
  }
]
```

scope_kind 规则：
- agent_global：跨越所有工作目录都稳定成立的 agent 协作倾向。
- workspace：只在当前工作目录 / repo 内成立的架构、代码、产品或执行倾向。
- session：当前会话里的临时倾向或尚未稳定的观察。无法确定时优先用 session。

不要编造。没有值得保存的倾向信号就返回 []。"""


async def extract_tendency_observations(
    message: str,
    context: str = "",
    *,
    model_gateway: ModelGateway | None = None,
) -> list[dict[str, Any]]:
    """
    LLM 分析消息，提取倾向信号。

    Returns:
        倾向观察候选列表，每条含 scope_kind/content/project_id
    """
    if not message.strip():
        return []

    today_str = datetime.now().strftime("%Y-%m-%d")
    system_prompt = TENDENCY_EXTRACT_PROMPT.replace("{current_date}", today_str)

    user_content = f"## 用户消息\n{message}"
    if context:
        user_content = f"## 对话上文\n{context}\n\n{user_content}"

    try:
        text_content = await (model_gateway or ModelGateway()).complete(
            system_prompt=system_prompt,
            user_prompt=user_content,
            config=ModelCallConfig(
                role="memory",
                max_tokens=EXTRACT_MAX_TOKENS,
                temperature=EXTRACT_TEMPERATURE,
                timeout=EXTRACT_TIMEOUT,
            ),
        )
        text_content = _sanitize_surrogates(text_content)
        return _parse_extraction_result(text_content)

    except Exception as e:
        logger.error("Extraction failed: %s", _sanitize_surrogates(str(e)))
        return []


def _parse_extraction_result(raw: str) -> list[dict[str, Any]]:
    """容错解析 LLM 返回的 JSON。"""
    raw = raw.strip()
    try:
        candidates = cast(list[dict[str, Any]], json.loads(raw))
        return [c for c in candidates if c.get("content")]
    except json.JSONDecodeError:
        pass

    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if m:
        try:
            candidates = cast(list[dict[str, Any]], json.loads(m.group(1)))
            return [c for c in candidates if c.get("content")]
        except json.JSONDecodeError:
            pass

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
