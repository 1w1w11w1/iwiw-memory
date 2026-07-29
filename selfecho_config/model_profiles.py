from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "selfecho_config"
LOCAL_CONFIG = CONFIG_DIR / "providers.local.json"
EXAMPLE_CONFIG = CONFIG_DIR / "providers.local.example.json"
MODEL_ROLES = ("chat", "summary", "memory")

MODEL_TEMPLATES = [
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "api_style": "anthropic",
        "base_url": "https://api.deepseek.com/anthropic",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "streaming": True,
    },
    {
        "id": "openai-compatible",
        "label": "OpenAI Compatible",
        "api_style": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4.1", "gpt-4.1-mini"],
        "streaming": True,
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "api_style": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["openai/gpt-4.1-mini", "anthropic/claude-sonnet-4"],
        "streaming": True,
    },
    {
        "id": "custom",
        "label": "自定义模型",
        "api_style": "openai",
        "base_url": "",
        "models": [""],
        "streaming": True,
    },
]


def _load_raw() -> dict[str, Any]:
    if LOCAL_CONFIG.exists():
        return json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
    if EXAMPLE_CONFIG.exists():
        return json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    return {"role_defaults": {}, "providers": []}


def _fallback_provider_id(providers: list[dict[str, Any]]) -> str:
    enabled = next((p for p in providers if p.get("enabled", True) and p.get("id")), None)
    return str(enabled.get("id")) if enabled else ""


def _first_model(provider: dict[str, Any] | None) -> str:
    if not provider:
        return ""
    models = provider.get("models") if isinstance(provider.get("models"), list) else []
    return str(models[0]) if models else ""


def _model_ref(provider_id: str = "", model: str = "") -> dict[str, str]:
    return {"provider_id": provider_id, "model": model}


def _fallback_provider(providers: list[dict[str, Any]]) -> dict[str, Any] | None:
    provider_id = _fallback_provider_id(providers)
    return next((p for p in providers if p.get("id") == provider_id), None)


def _role_defaults(raw: dict[str, Any], providers: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    configured = raw.get("role_defaults") if isinstance(raw.get("role_defaults"), dict) else {}
    providers_by_id = {str(p.get("id")): p for p in providers if p.get("id") and p.get("enabled", True)}
    fallback = _fallback_provider(providers)
    result: dict[str, dict[str, str]] = {}

    for role in MODEL_ROLES:
        ref = configured.get(role) if isinstance(configured.get(role), dict) else {}
        provider_id = str(ref.get("provider_id") or "")
        model = str(ref.get("model") or "")

        if not provider_id or not model:
            candidate = providers_by_id.get(provider_id) if provider_id else fallback
            candidate = candidate or fallback
            provider_id = str(candidate.get("id") or "") if candidate else ""
            model = model or _first_model(candidate)

        provider = providers_by_id.get(provider_id)
        if provider is None:
            provider = fallback
            provider_id = str(provider.get("id") or "") if provider else ""
            if provider is None:
                model = ""
        if provider and model not in [str(item) for item in provider.get("models", [])]:
            model = _first_model(provider)
        result[role] = _model_ref(provider_id, model)

    return result


def _provider_for_save(provider: dict[str, Any]) -> dict[str, Any]:
    item = dict(provider)
    item.pop("api_key_configured", None)
    item.pop("health", None)
    item.pop("last_test", None)
    item.pop("defaults", None)
    item["streaming"] = bool(item.get("streaming", True))
    models = item.get("models") if isinstance(item.get("models"), list) else []
    item["models"] = [str(model).strip() for model in models if str(model).strip()]
    return item


def _safe_provider(provider: dict[str, Any]) -> dict[str, Any]:
    p = dict(provider)
    p.pop("defaults", None)
    if "api_key" in p:
        p["api_key"] = "***" if p["api_key"] else ""
    env_name = p.get("api_key_env")
    p["api_key_configured"] = bool(p.get("api_key")) or bool(env_name and os.environ.get(env_name))
    p["streaming"] = bool(p.get("streaming", True))
    return p


def list_providers() -> dict[str, Any]:
    raw = _load_raw()
    providers = raw.get("providers", [])
    return {
        "role_defaults": _role_defaults(raw, providers),
        "providers": [_safe_provider(p) for p in providers],
    }


def list_model_templates() -> dict[str, Any]:
    return {"templates": MODEL_TEMPLATES}


def render_providers_config(
    providers: list[dict[str, Any]],
    role_defaults: dict[str, Any] | None = None,
) -> str:
    raw = _load_raw()
    current = {p.get("id"): p for p in raw.get("providers", [])}
    merged = []
    for provider in providers:
        pid = provider.get("id")
        old = current.get(pid, {})
        item = dict(old)
        item.update(provider)
        if item.get("api_key") == "***":
            item["api_key"] = old.get("api_key", "")
        merged.append(_provider_for_save(item))
    next_raw = {"role_defaults": role_defaults or raw.get("role_defaults") or {}, "providers": merged}
    normalized_roles = _role_defaults(next_raw, merged)
    return json.dumps(
        {"role_defaults": normalized_roles, "providers": merged},
        ensure_ascii=False,
        indent=2,
    )


def active_provider(role: str = "chat") -> dict[str, Any] | None:
    raw = _load_raw()
    providers = raw.get("providers", [])
    role_key = role if role in MODEL_ROLES else "chat"
    role_defaults = _role_defaults(raw, providers)
    role_ref = role_defaults.get(role_key, {})
    provider = next((p for p in providers if p.get("id") == role_ref.get("provider_id")), None)
    if provider is None:
        return None
    item = dict(provider)
    item.pop("defaults", None)
    item["model"] = role_ref.get("model") or _first_model(provider)
    item["api_key"] = _provider_key(item)
    return item


def _provider_key(provider: dict[str, Any]) -> str:
    if provider.get("api_key"):
        return provider["api_key"]
    env_name = provider.get("api_key_env")
    return os.environ.get(env_name, "") if env_name else ""


async def test_provider_config(provider: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(provider.get("id") or "")
    key = _provider_key(provider)
    if not key:
        return {"ok": False, "provider_id": provider_id, "error": "api key not configured", "latency_ms": 0}
    base_url = str(provider.get("base_url", "")).rstrip("/")
    api_style = provider.get("api_style", "anthropic")
    model = (provider.get("models") or [""])[0]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            if api_style == "openai":
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 8,
                    },
                )
            else:
                resp = await client.post(
                    f"{base_url}/messages",
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 8,
                    },
                )
            resp.raise_for_status()
        latency_ms = round((time.perf_counter() - started) * 1000)
        return {"ok": True, "provider_id": provider_id, "model": model, "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000)
        return {"ok": False, "provider_id": provider_id, "error": str(exc), "latency_ms": latency_ms}


async def test_provider(provider_id: str) -> dict[str, Any]:
    raw = _load_raw()
    provider = next((p for p in raw.get("providers", []) if p.get("id") == provider_id), None)
    if not provider:
        return {"ok": False, "error": "provider not found", "latency_ms": 0}
    return await test_provider_config(provider)
