"""
maintenance.py — 记忆维护：审查现有记忆，生成待确认的归档动作。

背景：旧会话层的「周期整理」（_maintain_memories_sync）已随会话层删除。
独立记忆系统下，维护改为按需触发：chat 的 /maintain 命令或 MCP 的
maintenance_review 工具调用。审查结论以 pending action 落库，
由用户（/pending approve / reject）最终确认，不自动执行。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from .db import get_maintenance_candidates, create_pending_action
from .llm import complete_text

logger = logging.getLogger("memory_agent.maintenance")

MAINTENANCE_SYSTEM_PROMPT = """你是一个记忆维护器。审查用户的长期记忆，只对明确过时或冗余的记忆提议归档（archive）。

规则：
- 保守为主：不确定就 keep，不要过度归档。
- 不做合并、不修改内容，只判断是否需要归档。
- 可归档：已完成的短期计划、明显过时的偏好、与其他记忆重复的信息。
- 不要动：profile（身份画像）与 rules（准则）类型永不归档；身份、健康、关系、长期计划、最近更新的记忆。
- 访问统计（access_count/last_access_at）只是参考信号：从未访问或长期未访问不等于无用，仅当内容本身明显过时时才归档。

输出格式：JSON 数组，每项 {"action": "archive|keep", "slug": "记忆slug", "reason": "一句话原因"}。没有需要归档的则返回 []。"""


async def review_maintenance(limit: int = 20, timeout: float = 20.0) -> dict[str, Any]:
    """
    审查维护候选并生成 pending actions（不自动执行）。

    Returns:
        {"reviewed": int, "pending": [{id, slug, action, reason}], "error": str | None}
    """
    candidates = get_maintenance_candidates(limit=limit)
    if not candidates:
        return {"reviewed": 0, "pending": [], "error": None}

    def _fmt_access(m: dict[str, Any]) -> str:
        n = m.get("access_count") or 0
        la = m.get("last_access_at")
        if not la:
            return f"访问 {n} 次（从未被检索命中）"
        try:
            days = max(0, (datetime.now() - datetime.fromisoformat(la)).days)
        except (ValueError, TypeError):
            days = -1
        return f"访问 {n} 次，最近 {days} 天前"

    catalog = "\n".join(
        "- {} ({}/{}) -- {} [{}]".format(
            m["slug"], m.get("mem_type", "fact"), m.get("priority", "active"),
            m.get("description", ""), _fmt_access(m),
        )
        for m in candidates[:15]
    )

    try:
        text = await complete_text(
            system_prompt=MAINTENANCE_SYSTEM_PROMPT,
            user_prompt=f"## 记忆候选目录\n{catalog}\n\n返回JSON列表。",
            max_tokens=800,
            temperature=0.1,
            timeout=timeout,
        )
    except Exception as exc:
        logger.warning("maintenance review failed: %s", exc)
        return {"reviewed": len(candidates), "pending": [], "error": str(exc)}

    match = re.search(r"\[[\s\S]*?\]", text)
    if not match:
        return {"reviewed": len(candidates), "pending": [], "error": "unparseable LLM output"}
    try:
        actions = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return {"reviewed": len(candidates), "pending": [], "error": f"bad JSON: {exc}"}

    pending: list[dict[str, str]] = []
    for act in actions or []:
        if not isinstance(act, dict) or act.get("action") != "archive":
            continue
        slug = str(act.get("slug") or "").strip()
        if not slug:
            continue
        item = create_pending_action(
            action="archive",
            target_memory_slug=slug,
            reason=str(act.get("reason") or "maintenance review")[:200],
        )
        if item:
            pending.append({"id": item["id"], "slug": slug, "action": "archive", "reason": item["reason"]})

    return {"reviewed": len(candidates), "pending": pending, "error": None}


# ── consolidate：空闲巩固（空闲期审查近期记忆，低风险自愈 + 高风险待审批）──

CONSOLIDATE_SYSTEM_PROMPT = """你是记忆巩固器（consolidate）。审查一批最近创建/更新的长期记忆，提出巩固建议。

规则：
- 保守为主：拿不准就 keep。
- 低风险修正（系统自动执行，有版本留痕可回滚）：
  - retype：mem_type 归类错误。仅允许 fact/lesson/project 之间互转；profile 与 rules 永不参与。
  - update_desc：description（一句话描述）含混、与正文不符或过长时，给一句干净的新描述。不改正文。
- 高风险动作（只提议，人工审批）：
  - archive：明显过时（已完成的短期计划、明显失效的偏好/事实）。profile 与 rules 永不归档。
  - merge：与本批内其他候选明显重复同义，给出 target_slug（合并进哪条保留）。
- 判断只基于本批候选内的信息。

输出 JSON 数组，每项：
{"action": "keep|retype|update_desc|archive|merge", "slug": "记忆slug", "reason": "一句话原因",
 "mem_type": "（仅 retype）正确类型", "description": "（仅 update_desc）新的一句话描述",
 "target_slug": "（仅 merge）合并进哪条"}
没有需要整理的返回 []。"""


def _window_bounds(
    since_hours: float,
    since_ms: int | None,
    until_ms: int | None,
) -> tuple[str, str | None]:
    """把窗口参数归一成 DB 的本地时间 ISO 字符串（秒精度）。

    毫秒入参：下界向下取整（含边界，容忍同秒重复，靠幂等消除），
    上界取所在秒（覆盖整秒）。不假装拥有毫秒精度——DB 存的就是本地秒。
    """
    if until_ms is not None:
        until_dt = datetime.fromtimestamp(until_ms / 1000.0)
    else:
        until_dt = datetime.now()
    if since_ms is not None:
        since_dt = datetime.fromtimestamp(since_ms / 1000.0)
    else:
        from datetime import timedelta
        since_dt = until_dt - timedelta(hours=since_hours)
    return (
        since_dt.isoformat(timespec="seconds"),
        until_dt.isoformat(timespec="seconds"),
    )


async def run_consolidate(
    since_hours: float = 24.0,
    limit: int = 20,
    timeout: float = 40.0,
    *,
    since_ms: int | None = None,
    until_ms: int | None = None,
    on_progress: Any = None,
) -> dict[str, Any]:
    """consolidate 巩固：审查窗口内创建/更新的记忆。

    低风险修正（retype/update_desc）走 mutation 自动执行（version+audit 可回溯，
    呼应"白箱审计、事后回溯优于事前确认"）；archive 生成 pending 待审批；
    merge 仅输出建议（pending merge 无执行语义，不落单）。

    窗口模式（给了 since_ms/until_ms）与旧 since_hours 模式的区别：
    旧模式把 limit 当"候选上限"，超出直接丢；窗口模式把 limit 当"每批大小"，
    窗口内候选先完整取快照再分批，不静默丢候选。
    on_progress(done, total)：仅后台 MCP 调用传入，不属 MCP JSON 参数。
    返回 complete：任何一批 LLM/JSON/mutation/pending 失败即 False；
    合法空数组与零候选是成功。
    """
    from .db import (
        create_pending_action,
        get_recent_changed,
        replace_memory_result,
        snapshot_of,
    )

    windowed = since_ms is not None or until_ms is not None
    since_iso, until_iso = _window_bounds(since_hours, since_ms, until_ms)

    result: dict[str, Any] = {
        "reviewed": 0, "auto_fixed": [], "pending": [],
        "suggestions": [], "error": None, "errors": [], "complete": True,
        "batches": 0,
    }

    def fail(msg: str) -> None:
        result["complete"] = False
        result["errors"].append(msg)
        if result["error"] is None:
            result["error"] = msg

    # 候选快照：窗口模式不限条数（避免默认参数静默丢候选），旧模式保留候选上限语义。
    candidates = get_recent_changed(
        since_iso,
        limit=None if windowed else limit,
        until_iso=until_iso if windowed else None,
    )
    result["reviewed"] = len(candidates)
    if not candidates:
        return result

    batch_size = max(1, limit)
    by_slug = {m["slug"]: m for m in candidates}
    snapshots = {m["slug"]: snapshot_of(m) for m in candidates}
    movable = {"fact", "lesson", "project"}
    total_batches = (len(candidates) + batch_size - 1) // batch_size

    for batch_no in range(total_batches):
        batch = candidates[batch_no * batch_size : (batch_no + 1) * batch_size]
        result["batches"] = batch_no + 1
        if on_progress is not None:
            try:
                await on_progress(batch_no, total_batches)
            except Exception:
                pass  # 进度上报失败不影响业务

        catalog = "\n".join(
            "- {} ({}) -- {}\n  正文: {}".format(
                m["slug"], m.get("mem_type", "fact"), m.get("description", ""),
                (m.get("content") or "")[:160],
            )
            for m in batch
        )
        try:
            text = await complete_text(
                system_prompt=CONSOLIDATE_SYSTEM_PROMPT,
                user_prompt=f"## 本批候选\n{catalog}\n\n返回JSON列表。",
                max_tokens=1200,
                temperature=0.1,
                timeout=timeout,
            )
        except Exception as exc:
            logger.warning("consolidate batch %s failed: %s", batch_no, exc)
            fail(f"batch {batch_no}: llm failed: {exc}")
            return result

        match = re.search(r"\[[\s\S]*?\]", text)
        if not match:
            fail(f"batch {batch_no}: unparseable LLM output")
            return result
        try:
            actions = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            fail(f"batch {batch_no}: bad JSON: {exc}")
            return result

        # 同 slug 多动作先合成一次修改：旧版按动作顺序各写一次，
        # "先 retype 后 update_desc" 会让后一次用旧 mem_type 把类型改回去。
        for act in actions or []:
            if not isinstance(act, dict):
                continue
            slug = str(act.get("slug") or "").strip()
            if slug not in by_slug:
                continue
            mem = by_slug[slug]
            action = str(act.get("action") or "keep").strip().lower()
            reason = str(act.get("reason") or "consolidate")[:200]

            if action == "keep":
                continue
            if action == "retype":
                new_type = str(act.get("mem_type") or "").strip()
                if not (new_type in movable and mem.get("mem_type") in movable and new_type != mem.get("mem_type")):
                    continue
                mem["mem_type"] = new_type
                mem.setdefault("_pending_auto", []).append(("retype", new_type, reason))
            elif action == "update_desc":
                desc = str(act.get("description") or "").strip()
                if not desc or desc == mem.get("description", ""):
                    continue
                mem["description"] = desc
                mem.setdefault("_pending_auto", []).append(("update_desc", desc, reason))
            elif action == "archive":
                if mem.get("mem_type") in ("profile", "rules"):
                    continue
                item = create_pending_action(action="archive", target_memory_slug=slug, reason=f"consolidate: {reason}")
                if item:
                    result["pending"].append({"id": item["id"], "slug": slug, "action": "archive", "reason": item["reason"]})
                else:
                    fail(f"batch {batch_no}: pending write failed for {slug}")
            elif action == "merge":
                target_slug = str(act.get("target_slug") or "").strip()
                if target_slug and target_slug != slug and target_slug in by_slug:
                    result["suggestions"].append({"slug": slug, "target_slug": target_slug, "reason": reason})

    # 合成后的自动修正：每个 slug 只写一次，带上最终的类型与描述
    for mem in candidates:
        pending_auto = mem.get("_pending_auto")
        if not pending_auto:
            continue
        slug = mem["slug"]
        kinds = [a[0] for a in pending_auto]
        reason_txt = "; ".join(f"{k}: {r}" for k, _v, r in pending_auto)
        audit_action = "consolidate_retype" if "retype" in kinds else "consolidate_desc"
        r = replace_memory_result(
            slug=slug,
            description=mem.get("description", ""),
            body=mem.get("content", ""),
            mem_type=mem.get("mem_type", "fact"),
            priority=mem.get("priority", "active"),
            reason=f"consolidate {reason_txt}",
            audit_action=audit_action,
            expected_snapshot=snapshots[slug],
        )
        if r.ok:
            for kind, value, reason in pending_auto:
                entry = {
                    "slug": slug,
                    "action": kind,
                    "reason": reason,
                    "version_id": r.version_id,
                    "audit_id": r.audit_id,
                    "changed_rows": r.changed_rows,
                }
                if kind == "retype":
                    entry["mem_type"] = value
                result["auto_fixed"].append(entry)
        else:
            fail(f"mutation failed for {slug}: {r.error}")

    return result
