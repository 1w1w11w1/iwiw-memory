from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("selfecho_model.provider")


class ModelConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderSettings:
    model: str
    api_style: str
    base_url: str
    api_key: str
    streaming: bool = True

    @classmethod
    def from_environment(cls) -> "ProviderSettings":
        return cls(
            model=os.environ.get("MEMORY_AGENT_LLM_MODEL", "deepseek-v4-flash"),
            api_style=os.environ.get("MEMORY_AGENT_LLM_API_STYLE", "anthropic").strip().lower(),
            base_url=os.environ.get(
                "MEMORY_AGENT_LLM_BASE_URL",
                "https://api.deepseek.com/anthropic",
            ).rstrip("/"),
            api_key=os.environ.get("MEMORY_AGENT_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", ""),
        )


class ProviderAdapter:
    async def complete(
        self,
        *,
        settings: ProviderSettings,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        timeout: float,
    ) -> str:
        self._validate(settings)
        if settings.api_style == "anthropic":
            payload: dict[str, Any] = {
                "model": settings.model,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            data = await self._post(settings, "/messages", payload, timeout)
            content = data.get("content", "")
            if isinstance(content, list):
                return "\n".join(
                    str(block.get("text") or "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            return content if isinstance(content, str) else ""

        payload = {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        data = await self._post(settings, "/chat/completions", payload, timeout)
        choices = data.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        return str(message.get("content") or "") if isinstance(message, dict) else ""

    async def stream(
        self,
        *,
        settings: ProviderSettings,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        timeout: float,
    ) -> AsyncIterator[str]:
        self._validate(settings)
        if settings.api_style == "anthropic":
            path = "/messages"
            payload: dict[str, Any] = {
                "model": settings.model,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
            }
        else:
            path = "/chat/completions"
            payload = {
                "model": settings.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
                "stream_options": {"include_usage": True},
            }

        headers = self._headers(settings)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{settings.base_url}{path}",
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    event = _sse_json(line)
                    if not event:
                        continue
                    if settings.api_style == "anthropic":
                        delta = event.get("delta") or {}
                        text = delta.get("text") if event.get("type") == "content_block_delta" else None
                        if isinstance(text, str) and text:
                            yield text
                        continue
                    for choice in event.get("choices") or []:
                        text = (choice.get("delta") or {}).get("content")
                        if isinstance(text, str) and text:
                            yield text

    async def _post(
        self,
        settings: ProviderSettings,
        path: str,
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.base_url}{path}",
                json=payload,
                headers=self._headers(settings),
            )
            response.raise_for_status()
            data = response.json()
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _headers(settings: ProviderSettings) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.api_key}",
        }

    @staticmethod
    def _validate(settings: ProviderSettings) -> None:
        if not settings.api_key:
            raise ModelConfigurationError("model API key is not configured")
        if settings.api_style not in {"anthropic", "openai"}:
            raise ModelConfigurationError(f"unsupported model API style: {settings.api_style}")


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
