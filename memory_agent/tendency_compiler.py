"""
tendency_compiler.py — TendencyCompiler

从触发器提交的材料中整理倾向。读取原始消息和倾向观察，
通过 LLM 生成短 profile，写入 tendency_profiles。

Interface:
    TendencyCompiler.compile(scope_kind, scope_key, trigger) -> TendencyCompileResult

触发类型：
  - observe: 高信号内容写入 tendency_observations
  - compile: 从 dirty observations 编译短 profile
  - rebuild: 从指定范围重新编译 profile
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from selfecho_model import ModelCallConfig, ModelGateway

from .config import EXTRACT_MAX_TOKENS, EXTRACT_TEMPERATURE, EXTRACT_TIMEOUT, LOG_FILE
from .db import get_profile_by_scope, insert_observation, list_observations, upsert_profile
from .scopes import AGENT_GLOBAL_SCOPE, normalize_tendency_scope

logger = logging.getLogger("tendency_compiler")

_SURROGATE_RE = re.compile(r'[\ud800-\udfff]')


def _sanitize(text: str) -> str:
    return _SURROGATE_RE.sub("", text)


@dataclass(frozen=True)
class TendencyCompileResult:
    ok: bool
    action: str                     # observe | compile | rebuild
    observation_ids: list[str] = field(default_factory=list)
    profile_id: str | None = None
    profile_version: int = 0
    changed_rows: int = 0
    version_id: str | None = None
    audit_id: str | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "observation_ids": self.observation_ids,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "changed_rows": self.changed_rows,
            "version_id": self.version_id,
            "audit_id": self.audit_id,
            "error": self.error,
        }


# ── 编译 Prompt ──
COMPILE_PROMPT = """你是一个「个人倾向编译器」。你的任务是从多条倾向观察（tendency observations）和对话上下文中，编译一份简短的倾向 profile。

## 输入
- 当前 scope：{scope_label}
- 当前 scope 的旧 profile（如存在）
- dirty observations 列表
- 触发类型：{action}

## 输出要求
- 用第三人称中文自然语言
- 每条倾向一句完整话，说明"用户在什么情况下倾向于什么行为"
- profile 要短（每条不超过 100 字，总数不超过 6 条）
- 如果有与旧 profile 冲突的观察，以最新观察为准
- 不确定是否稳定时只保留 observation，不写入 profile

## 输出格式
只返回 JSON：
```json
{{
  "tendencies": ["倾向1", "倾向2"],
  "notes": "编译说明（可选）"
}}
```

如果没有足够稳定的倾向，返回空数组。"""


class TendencyCompiler:
    """从倾向观察编译倾向 profile。"""

    def __init__(self, model_gateway: ModelGateway | None = None) -> None:
        self.model_gateway = model_gateway or ModelGateway()

    async def observe(
        self,
        *,
        content: str,
        scope_kind: str = AGENT_GLOBAL_SCOPE,
        scope_key: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        turn_range: str | None = None,
    ) -> TendencyCompileResult:
        """写入单条倾向观察（不编译 profile）。"""
        obs_id = insert_observation(
            content=content,
            scope_kind=scope_kind,
            scope_key=scope_key,
            workspace_id=workspace_id,
            project_id=project_id,
            source_session_id=session_id,
            source_turn_range=turn_range,
        )
        if obs_id:
            return TendencyCompileResult(True, "observe", observation_ids=[obs_id])
        return TendencyCompileResult(False, "observe", error="insert failed")

    async def compile(
        self,
        *,
        scope_kind: str = AGENT_GLOBAL_SCOPE,
        scope_key: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        max_observations: int = 20,
        observation_status: str | None = "active",
        action: str = "compile",
        observations: list[dict[str, Any]] | None = None,
    ) -> TendencyCompileResult:
        """
        从 dirty observations 编译倾向 profile。

        读取 active observations → LLM 生成 profile → 写入 tendency_profiles
        → 标记 observations 为 merged
        """
        try:
            scope_kind, scope_key = normalize_tendency_scope(scope_kind, scope_key)
        except ValueError as exc:
            return TendencyCompileResult(False, action, error=str(exc))
        if observations is None:
            observations = list_observations(
                scope_kind=scope_kind,
                scope_key=scope_key,
                status=observation_status,
                limit=max_observations,
            )
        if observation_status is None:
            observations = [obs for obs in observations if obs.get("status") != "deleted"]
        if not observations:
            return TendencyCompileResult(False, action, observation_ids=[],
                                         error="no observations")

        # 获取当前 profile
        old_profile = get_profile_by_scope(scope_kind, scope_key)
        old_content = old_profile.get("content", "") if old_profile and action != "rebuild" else ""

        # 构建 prompt
        obs_text = "\n".join(
            f"- [{obs['id'][:8]}] {obs['content']}"
            for obs in observations
        )
        prompt = COMPILE_PROMPT.format(action=action, scope_label=_scope_label(scope_kind))
        user_content = (
            f"## 当前 profile\n{old_content or '(无)'}\n\n"
            f"## 待编译观察\n{obs_text}"
        )

        try:
            raw = await self.model_gateway.complete(
                system_prompt=prompt,
                user_prompt=user_content,
                config=ModelCallConfig(
                    role="memory",
                    max_tokens=EXTRACT_MAX_TOKENS,
                    temperature=EXTRACT_TEMPERATURE,
                    timeout=EXTRACT_TIMEOUT,
                ),
            )
            parsed = _parse_compile_result(raw)
            if not parsed:
                return TendencyCompileResult(False, action, observation_ids=[],
                                             error="LLM returned empty result")

            tendencies = parsed.get("tendencies", [])
            if not tendencies:
                return TendencyCompileResult(False, action, observation_ids=[],
                                             error="no tendencies for scope")

            profile_content = "\n".join(f"- {t}" for t in tendencies)

            # 写入 profile
            obs_ids = [obs["id"] for obs in observations]
            result = upsert_profile(
                scope_kind=scope_kind,
                scope_key=scope_key,
                workspace_id=workspace_id,
                project_id=project_id,
                content=profile_content,
                source_observation_ids=obs_ids,
                merge_observation_ids=obs_ids,
                replace_source_observation_ids=action == "rebuild",
            )
            if not result.ok:
                return TendencyCompileResult(False, action, error=result.error)

            return TendencyCompileResult(
                True, action,
                observation_ids=obs_ids,
                profile_id=result.target_id,
                profile_version=result.details.get("version", 0),
                changed_rows=result.changed_rows,
                version_id=result.version_id,
                audit_id=result.audit_id,
            )

        except Exception as e:
            logger.error("Compile failed: %s", e)
            return TendencyCompileResult(False, action, error=str(e))

    async def rebuild(
        self,
        *,
        scope_kind: str = AGENT_GLOBAL_SCOPE,
        scope_key: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> TendencyCompileResult:
        """
        从所有 observations 重建 profile（用于冲突解决/方向变化）。
        与 compile 相同但读取所有历史 observations（包括已 merged）。
        """
        try:
            scope_kind, scope_key = normalize_tendency_scope(scope_kind, scope_key)
        except ValueError as exc:
            return TendencyCompileResult(False, "rebuild", error=str(exc))
        observations = [
            obs for obs in list_observations(
                scope_kind=scope_kind,
                scope_key=scope_key,
                status=None,
                limit=100,
            )
            if obs.get("status") != "deleted"
        ]
        if not observations:
            return TendencyCompileResult(False, "rebuild", observation_ids=[],
                                         error="no observations to rebuild from")

        return await self.compile(
            scope_kind=scope_kind,
            scope_key=scope_key,
            workspace_id=workspace_id,
            project_id=project_id,
            session_id=session_id,
            max_observations=100,
            observation_status=None,
            action="rebuild",
        )


def _parse_compile_result(raw: str) -> dict[str, Any] | None:
    """容错解析 LLM 返回的 JSON。"""
    raw = _sanitize(raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def _scope_label(scope_kind: str) -> str:
    if scope_kind == "agent_global":
        return "跨所有工作目录的全局倾向"
    if scope_kind == "workspace":
        return "当前工作目录倾向"
    if scope_kind == "session":
        return "当前会话倾向 overlay"
    return scope_kind
