"""LLM 调用封装，兼容 Anthropic-style 与 OpenAI-style 接口。"""

from __future__ import annotations

import logging
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import LLM_API_KEY, LLM_API_STYLE, LLM_BASE_URL, LLM_MODEL

logger = logging.getLogger("memory_agent.llm")


class LLMConfigurationError(RuntimeError):
    """LLM 配置缺失或不支持。"""


def _require_api_key(api_key: str | None = None) -> str:
    key = api_key or LLM_API_KEY
    if not key:
        raise LLMConfigurationError(
            "Missing API key. Set MEMORY_AGENT_LLM_API_KEY or DEEPSEEK_API_KEY."
        )
    return key


async def complete_text(
    *,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    timeout: float,
    model: str | None = None,
    api_style: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> str:
    """返回纯文本 completion 内容。"""
    api_key = _require_api_key(api_key)
    style = (api_style or LLM_API_STYLE or "anthropic").strip().lower()
    url = (base_url or LLM_BASE_URL).rstrip("/")
    chosen_model = model or LLM_MODEL

    if style == "anthropic":
        return await _complete_anthropic(
            api_key=api_key,
            base_url=url,
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
            base_url=url,
            model=chosen_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )

    raise LLMConfigurationError(f"Unsupported MEMORY_AGENT_LLM_API_STYLE: {style}")


async def stream_text(
    *,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    timeout: float,
    model: str | None = None,
    api_style: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> AsyncIterator[str]:
    """Yield completion text deltas when the configured provider supports streaming."""
    api_key = _require_api_key(api_key)
    style = (api_style or LLM_API_STYLE or "anthropic").strip().lower()
    url = (base_url or LLM_BASE_URL).rstrip("/")
    chosen_model = model or LLM_MODEL

    if style == "anthropic":
        async for chunk in _stream_anthropic(
            api_key=api_key,
            base_url=url,
            model=chosen_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        ):
            yield chunk
        return
    if style == "openai":
        async for chunk in _stream_openai(
            api_key=api_key,
            base_url=url,
            model=chosen_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        ):
            yield chunk
        return

    raise LLMConfigurationError(f"Unsupported MEMORY_AGENT_LLM_API_STYLE: {style}")


async def _complete_anthropic(
    *,
    api_key: str,
    base_url: str,
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
        resp = await client.post(f"{base_url}/messages", json=payload, headers=headers)
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
    base_url: str,
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
        resp = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    choices = data.get("choices", [])
    if not choices:
        return ""
    message = choices[0].get("message", {})
    return message.get("content", "") if isinstance(message, dict) else ""


async def _stream_anthropic(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> AsyncIterator[str]:
    payload: dict[str, Any] = {
        "model": model,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", f"{base_url}/messages", json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                data = _sse_json(line)
                if not data:
                    continue
                if data.get("type") == "content_block_delta":
                    delta = data.get("delta") or {}
                    text = delta.get("text")
                    if isinstance(text, str) and text:
                        yield text


async def _stream_openai(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> AsyncIterator[str]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", f"{base_url}/chat/completions", json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.strip() == "data: [DONE]":
                    break
                data = _sse_json(line)
                if not data:
                    continue
                for choice in data.get("choices") or []:
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        yield text


def _sse_json(line: str) -> dict[str, Any] | None:
    if not line.startswith("data:"):
        return None
    raw = line.removeprefix("data:").strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("Failed to decode SSE line: %s", raw)
        return None
    return data if isinstance(data, dict) else None
