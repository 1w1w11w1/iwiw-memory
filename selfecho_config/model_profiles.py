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
    return {"active_provider_id": "", "providers": []}


def _default_active_provider_id(providers: list[dict[str, Any]]) -> str:
    enabled = next((p for p in providers if p.get("enabled", True) and p.get("id")), None)
    first = enabled or next((p for p in providers if p.get("id")), None)
    return str(first.get("id")) if first else ""


def _provider_for_save(provider: dict[str, Any]) -> dict[str, Any]:
    item = dict(provider)
    item.pop("api_key_configured", None)
    item.pop("health", None)
    item.pop("last_test", None)
    item["streaming"] = bool(item.get("streaming", True))
    defaults = item.get("defaults") if isinstance(item.get("defaults"), dict) else {}
    models = item.get("models") if isinstance(item.get("models"), list) else []
    first = str(models[0]) if models else ""
    item["defaults"] = {
        "chat": str(defaults.get("chat") or first),
        "summary": str(defaults.get("summary") or first),
        "memory": str(defaults.get("memory") or first),
    }
    return item


def _safe_provider(provider: dict[str, Any]) -> dict[str, Any]:
    p = dict(provider)
    if "api_key" in p:
        p["api_key"] = "***" if p["api_key"] else ""
    env_name = p.get("api_key_env")
    p["api_key_configured"] = bool(p.get("api_key")) or bool(env_name and os.environ.get(env_name))
    p["streaming"] = bool(p.get("streaming", True))
    return p


def list_providers() -> dict[str, Any]:
    raw = _load_raw()
    providers = raw.get("providers", [])
    active_provider_id = str(raw.get("active_provider_id") or "") or _default_active_provider_id(providers)
    return {
        "active_provider_id": active_provider_id,
        "providers": [_safe_provider(p) for p in providers],
    }


def list_model_templates() -> dict[str, Any]:
    return {"templates": MODEL_TEMPLATES}


def save_providers(providers: list[dict[str, Any]], active_provider_id: str | None = None) -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
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
    active = active_provider_id or raw.get("active_provider_id") or _default_active_provider_id(merged)
    ids = {p.get("id") for p in merged}
    if active not in ids:
        active = _default_active_provider_id(merged)
    LOCAL_CONFIG.write_text(
        json.dumps({"active_provider_id": active, "providers": merged}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return list_providers()


def active_provider(role: str = "chat") -> dict[str, Any] | None:
    raw = _load_raw()
    providers = raw.get("providers", [])
    active = str(raw.get("active_provider_id") or "") or _default_active_provider_id(providers)
    provider = next((p for p in providers if p.get("id") == active), None)
    if provider is None:
        provider = next((p for p in providers if p.get("enabled", True)), None)
    if provider is None:
        return None
    item = dict(provider)
    defaults = item.get("defaults") if isinstance(item.get("defaults"), dict) else {}
    models = item.get("models") if isinstance(item.get("models"), list) else []
    item["model"] = defaults.get(role) or defaults.get("chat") or (models[0] if models else "")
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
