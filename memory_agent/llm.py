"""LLM 调用封装，兼容 Anthropic-style 与 OpenAI-style 接口。"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import LLM_API_KEY, LLM_API_STYLE, LLM_BASE_URL, LLM_MODEL

logger = logging.getLogger("memory_agent.llm")


class LLMConfigurationError(RuntimeError):
    """LLM 配置缺失或不支持。"""


def _require_api_key() -> str:
    if not LLM_API_KEY:
        raise LLMConfigurationError(
            "Missing API key. Set MEMORY_AGENT_LLM_API_KEY or DEEPSEEK_API_KEY."
        )
    return LLM_API_KEY


async def complete_text(
    *,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    timeout: float,
    model: str | None = None,
) -> str:
    """返回纯文本 completion 内容。"""
    api_key = _require_api_key()
    style = LLM_API_STYLE or "anthropic"
    chosen_model = model or LLM_MODEL

    if style == "anthropic":
        return await _complete_anthropic(
            api_key=api_key,
            model=chosen_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
    if style == "openai":
        return await _complete_openai(
            api_key=api_key,
            model=chosen_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )

    raise LLMConfigurationError(f"Unsupported MEMORY_AGENT_LLM_API_STYLE: {style}")


async def _complete_anthropic(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{LLM_BASE_URL}/messages", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    content = data.get("content", "")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(part for part in parts if part)
    if isinstance(content, str):
        return content
    return ""


async def _complete_openai(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{LLM_BASE_URL}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    choices = data.get("choices", [])
    if not choices:
        return ""
    message = choices[0].get("message", {})
    return message.get("content", "") if isinstance(message, dict) else ""
