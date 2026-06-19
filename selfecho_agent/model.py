from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Callable

from memory_agent.llm import complete_text, stream_text
from selfecho_config.model_profiles import active_provider

from .core import ModelCallConfig


class ModelGateway:
    """Model-call adapter with retry semantics kept out of the orchestrator."""

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        config: ModelCallConfig,
        on_retry: Callable[[Exception], None] | None = None,
    ) -> str:
        provider_kwargs, _ = self._provider_settings(config.role)
        try:
            return await complete_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                timeout=config.timeout,
                **provider_kwargs,
            )
        except Exception as first_exc:
            if on_retry:
                on_retry(first_exc)
            try:
                return await complete_text(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=config.max_tokens,
                    temperature=config.temperature,
                    timeout=config.timeout,
                    **provider_kwargs,
                )
            except Exception as second_exc:
                raise RuntimeError(f"{first_exc}; retry failed: {second_exc}") from second_exc

    async def stream(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        config: ModelCallConfig,
        on_retry: Callable[[Exception], None] | None = None,
    ) -> AsyncIterator[str]:
        provider_kwargs, streaming_enabled = self._provider_settings(config.role)
        if not streaming_enabled:
            text = await self.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                config=config,
                on_retry=on_retry,
            )
            if text:
                yield text
            return

        try:
            async for chunk in stream_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                timeout=config.timeout,
                **provider_kwargs,
            ):
                yield chunk
        except Exception as first_exc:
            if on_retry:
                on_retry(first_exc)
            # Streaming retries can duplicate partial text, so retry only by
            # falling back to a single complete response.
            try:
                text = await complete_text(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=config.max_tokens,
                    temperature=config.temperature,
                    timeout=config.timeout,
                    **provider_kwargs,
                )
            except Exception as second_exc:
                raise RuntimeError(f"{first_exc}; fallback failed: {second_exc}") from second_exc
            if text:
                yield text

    def streaming_enabled(self, model_role: str) -> bool:
        _, streaming_enabled = self._provider_settings(model_role)
        return streaming_enabled

    def _provider_settings(self, model_role: str) -> tuple[dict, bool]:
        role = "chat"
        if model_role in {"summary", "session_summary"}:
            role = "summary"
        if model_role in {"memory", "memory_consolidation"}:
            role = "memory"
        provider = active_provider(role)
        if not provider:
            return {}, True
        provider_kwargs = {
            "model": provider.get("model") or None,
            "api_style": provider.get("api_style") or None,
            "base_url": provider.get("base_url") or None,
            "api_key": provider.get("api_key") or None,
        }
        return provider_kwargs, bool(provider.get("streaming", True))
