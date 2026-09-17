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


def _build_messages(
    messages: list[dict[str, Any]] | None,
    user_prompt: str,
    system_prompt: str,
) -> list[dict[str, Any]]:
    """
    组装多轮消息：messages 提供多轮对话（system 条目以 system_prompt 为准），
    未提供时回退为单轮 [user: user_prompt]。
    消息原样透传（浅拷贝），保留 assistant.tool_calls / role=tool 等结构——
    工具轮消息必须完整进入后续请求（OpenAI 兼容 API 的 tool 顺序约束）。
    """
    if messages:
        return [dict(m) for m in messages if m.get("role") != "system"]
    return [{"role": "user", "content": user_prompt}]


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
    messages: list[dict[str, str]] | None = None,
) -> str:
    """返回纯文本 completion 内容。messages 提供多轮上下文时优先使用。"""
    api_key = _require_api_key(api_key)
    style = (api_style or LLM_API_STYLE or "anthropic").strip().lower()
    url = (base_url or LLM_BASE_URL).rstrip("/")
    chosen_model = model or LLM_MODEL
    msgs = _build_messages(messages, user_prompt, system_prompt)

    if style == "anthropic":
        return await _complete_anthropic(
            api_key=api_key,
            base_url=url,
            model=chosen_model,
            system_prompt=system_prompt,
            messages=msgs,
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
            messages=msgs,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )

    raise LLMConfigurationError(f"Unsupported MEMORY_AGENT_LLM_API_STYLE: {style}")


async def complete_with_tools(
    *,
    system_prompt: str,
    messages: list[dict[str, Any]] | None,
    tools: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    timeout: float,
    model: str | None = None,
    api_style: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    带工具定义的非流式 completion（OpenAI-style function calling）。

    返回 {"message": assistant 消息（原样，可能含 tool_calls）, "finish_reason": str}。
    finish_reason == "tool_calls" 时，调用方执行工具并把结果以
    {"role": "tool", "tool_call_id": ..., "content": ...} 逐条回传后再次调用。
    """
    api_key = _require_api_key(api_key)
    style = (api_style or LLM_API_STYLE or "anthropic").strip().lower()
    if style != "openai":
        raise LLMConfigurationError(
            f"memory tools require openai-style API, got: {style}"
        )
    url = (base_url or LLM_BASE_URL).rstrip("/")
    chosen_model = model or LLM_MODEL
    msgs = _build_messages(messages, "", system_prompt)
    payload: dict[str, Any] = {
        "model": chosen_model,
        "messages": [{"role": "system", "content": system_prompt}, *msgs],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "tools": tools,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{url}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    choices = data.get("choices", [])
    if not choices:
        return {"message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}
    choice = choices[0]
    return {
        "message": choice.get("message") or {"role": "assistant", "content": ""},
        "finish_reason": choice.get("finish_reason", "stop"),
    }


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
    messages: list[dict[str, str]] | None = None,
) -> AsyncIterator[str]:
    """Yield completion text deltas when the configured provider supports streaming."""
    api_key = _require_api_key(api_key)
    style = (api_style or LLM_API_STYLE or "anthropic").strip().lower()
    url = (base_url or LLM_BASE_URL).rstrip("/")
    chosen_model = model or LLM_MODEL
    msgs = _build_messages(messages, user_prompt, system_prompt)

    if style == "anthropic":
        async for chunk in _stream_anthropic(
            api_key=api_key,
            base_url=url,
            model=chosen_model,
            system_prompt=system_prompt,
            messages=msgs,
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
            messages=msgs,
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
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "system": system_prompt,
        "messages": messages,
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
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            *messages,
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
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> AsyncIterator[str]:
    payload: dict[str, Any] = {
        "model": model,
        "system": system_prompt,
        "messages": messages,
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
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> AsyncIterator[str]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            *messages,
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
