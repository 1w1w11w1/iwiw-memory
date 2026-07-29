from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Callable

from selfecho_config.model_profiles import active_provider

from .provider import ProviderAdapter, ProviderSettings


@dataclass(frozen=True)
class ModelCallConfig:
    role: str
    max_tokens: int
    temperature: float
    timeout: float


class ModelGateway:
    """Model calls, provider selection, and retry behavior."""

    def __init__(self, provider: ProviderAdapter | None = None) -> None:
        self.provider = provider or ProviderAdapter()

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        config: ModelCallConfig,
        on_retry: Callable[[Exception], None] | None = None,
    ) -> str:
        settings = self._settings(config.role)
        try:
            return await self.provider.complete(
                settings=settings,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                timeout=config.timeout,
            )
        except Exception as first_exc:
            if on_retry:
                on_retry(first_exc)
            try:
                return await self.provider.complete(
                    settings=settings,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=config.max_tokens,
                    temperature=config.temperature,
                    timeout=config.timeout,
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
        settings = self._settings(config.role)
        if not settings.streaming:
            text = await self.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                config=config,
                on_retry=on_retry,
            )
            if text:
                yield text
            return
        emitted = False
        try:
            async for chunk in self.provider.stream(
                settings=settings,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                timeout=config.timeout,
            ):
                emitted = True
                yield chunk
        except Exception as first_exc:
            if on_retry:
                on_retry(first_exc)
            if emitted:
                raise
            text = await self.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                config=config,
            )
            if text:
                yield text

    def streaming_enabled(self, model_role: str) -> bool:
        return self._settings(model_role).streaming

    @staticmethod
    def _settings(model_role: str) -> ProviderSettings:
        role = "chat"
        if model_role in {"summary", "session_summary"}:
            role = "summary"
        elif model_role in {"memory", "memory_consolidation"}:
            role = "memory"
        provider = active_provider(role)
        if not provider:
            return ProviderSettings.from_environment()
        return ProviderSettings(
            model=str(provider.get("model") or ""),
            api_style=str(provider.get("api_style") or "anthropic").strip().lower(),
            base_url=str(provider.get("base_url") or "").rstrip("/"),
            api_key=str(provider.get("api_key") or ""),
            streaming=bool(provider.get("streaming", True)),
        )
