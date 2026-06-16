from __future__ import annotations

import json
import os
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
    },
    {
        "id": "openai-compatible",
        "label": "OpenAI Compatible",
        "api_style": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4.1", "gpt-4.1-mini"],
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "api_style": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["openai/gpt-4.1-mini", "anthropic/claude-sonnet-4"],
    },
    {
        "id": "custom",
        "label": "自定义模型",
        "api_style": "openai",
        "base_url": "",
        "models": [""],
    },
]


def _load_raw() -> dict[str, Any]:
    if LOCAL_CONFIG.exists():
        return json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
    if EXAMPLE_CONFIG.exists():
        return json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    return {"providers": []}


def _safe_provider(provider: dict[str, Any]) -> dict[str, Any]:
    p = dict(provider)
    if "api_key" in p:
        p["api_key"] = "***" if p["api_key"] else ""
    env_name = p.get("api_key_env")
    p["api_key_configured"] = bool(p.get("api_key")) or bool(env_name and os.environ.get(env_name))
    return p


def list_providers() -> dict[str, Any]:
    raw = _load_raw()
    return {"providers": [_safe_provider(p) for p in raw.get("providers", [])]}


def list_model_templates() -> dict[str, Any]:
    return {"templates": MODEL_TEMPLATES}


def save_providers(providers: list[dict[str, Any]]) -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    current = {p.get("id"): p for p in _load_raw().get("providers", [])}
    merged = []
    for provider in providers:
        pid = provider.get("id")
        old = current.get(pid, {})
        item = dict(old)
        item.update(provider)
        if item.get("api_key") == "***":
            item["api_key"] = old.get("api_key", "")
        merged.append(item)
    LOCAL_CONFIG.write_text(json.dumps({"providers": merged}, ensure_ascii=False, indent=2), encoding="utf-8")
    return list_providers()


def _provider_key(provider: dict[str, Any]) -> str:
    if provider.get("api_key"):
        return provider["api_key"]
    env_name = provider.get("api_key_env")
    return os.environ.get(env_name, "") if env_name else ""


async def test_provider(provider_id: str) -> dict[str, Any]:
    raw = _load_raw()
    provider = next((p for p in raw.get("providers", []) if p.get("id") == provider_id), None)
    if not provider:
        return {"ok": False, "error": "provider not found"}
    key = _provider_key(provider)
    if not key:
        return {"ok": False, "error": "api key not configured"}
    base_url = str(provider.get("base_url", "")).rstrip("/")
    api_style = provider.get("api_style", "anthropic")
    model = (provider.get("models") or [""])[0]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
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
        return {"ok": True, "provider_id": provider_id, "model": model}
    except Exception as exc:
        return {"ok": False, "provider_id": provider_id, "error": str(exc)}
