"""debug_bundle.py — 把 DSH 导出的会话包解析成「记忆系统」调试摘要。

输入：DSH Web 会话头部导出按钮 / `/export` 命令产出的 ZIP（内含明文 JSONL），
或单个会话 JSONL 文件。不传路径时取 `debug-inbox/` 中最新的一个包。

用法：
    python -m memory_agent.debug_bundle <包.zip|会话.jsonl> [--json] [--out 报告.md] [--raw]

输出：按轮次串起来的调试事实链——
    用户说了什么 → 插件注入了什么（kind + 记忆 slug）→ 模型怎么调 memory_*
    → 结果/报错。默认脱敏（疑似密钥打码），--raw 关闭脱敏保真。
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .redaction import redact_for_display

TOOL_PREFIX = "memory_"
INBOX_DIR = Path(__file__).resolve().parent.parent / "debug-inbox"

_MEMORY_SECTION_NAMES = {"相关记忆", "长期记忆", "会话回顾（reflect）", "记忆巩固"}

EXCERPT = 200
ARG_EXCERPT = 160
RESULT_EXCERPT = 240

def redact(text: str, raw: bool) -> str:
    """默认脱敏（与写入闸门同一套规则）；raw=True 时原样返回（调试保真用）。"""
    if raw or not text:
        return text
    return redact_for_display(text)


def clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


# ── 载入 ──────────────────────────────────────────────────────────────────────


def _parse_jsonl(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def load_bundle(path: Path) -> dict[str, Any]:
    """读导出包/裸日志，返回 {root: [events], subagents: {name: [events]}, source: str, skipped: [...]}。"""
    logs: dict[str, list[dict[str, Any]]] = {}
    subagents: dict[str, list[dict[str, Any]]] = {}
    skipped: list[str] = []

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                if name.endswith(".jsonl"):
                    events = _parse_jsonl(zf.read(name).decode("utf-8", "replace"))
                elif name.endswith((".jsonl.zstd", ".zstd")):
                    skipped.append(name)
                    continue
                else:
                    continue
                if name.startswith("subagents/"):
                    subagents[name] = events
                else:
                    logs[name] = events
    else:
        logs[path.name] = _parse_jsonl(path.read_text(encoding="utf-8", errors="replace"))

    # 根会话 = 事件最多的那份（导出里根日志可能带 vN 后缀，不靠文件名猜）
    root_name = max(logs, key=lambda n: len(logs[n])) if logs else ""
    return {
        "root": logs.get(root_name, []),
        "root_name": root_name,
        "other": {n: e for n, e in logs.items() if n != root_name},
        "subagents": subagents,
        "source": str(path),
        "skipped": skipped,
    }


# ── 抽取 ──────────────────────────────────────────────────────────────────────


def _message_of(event: dict[str, Any]) -> dict[str, Any] | None:
    """取消息体。实测两种形态：tool/result 等包在 data.message；
    user/message（含插件快照）字段直接挂在 data 下。"""
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    msg = data.get("message")
    if isinstance(msg, dict):
        return msg
    if "role" in data and "content" in data:
        return data
    return None


def _text_of(msg: dict[str, Any], kinds: Iterable[str] = ("text",)) -> str:
    blocks = msg.get("content")
    if not isinstance(blocks, list):
        return ""
    wanted = set(kinds)
    return "\n".join(
        b.get("text", "") for b in blocks
        if isinstance(b, dict) and b.get("type") in wanted and isinstance(b.get("text"), str)
    )


def _is_internal(text: str) -> bool:
    """系统注入的模板文本（skill 清单、系统提醒等）不算用户说的话。"""
    head = text.lstrip()[:200]
    return head.startswith("<system-reminder>") or head.startswith("<available_skills>")


def _snapshot_meta(msg: dict[str, Any]) -> dict[str, Any] | None:
    """识别「插件注入的记忆快照」。实测两种记忆插件的标注形态：
    - dsh-iwiw-memory：元信息在 source.memory = {kind, ids}
    - meow-memory：无 source.memory，元信息藏在 sections 里名为 __meta__ 的 JSON 段
    两者都是 {kind:"plugin", plugin, form:"snapshot", sections:[...]}。"""
    source = msg.get("source")
    if not isinstance(source, dict):
        return None
    plugin = source.get("plugin")
    if not isinstance(plugin, str):
        return None
    sections = source.get("sections")
    if not isinstance(sections, list):
        return None

    memory = source.get("memory")
    meta = memory if isinstance(memory, dict) else {}
    kind = meta.get("kind")
    ids = meta.get("ids")

    parts: list[str] = []
    has_memory_meta = isinstance(memory, dict)
    has_known_section = False
    for section in sections:
        if not isinstance(section, dict):
            continue
        name = section.get("name")
        text = section.get("text") if isinstance(section.get("text"), str) else ""
        if name == "__meta__":
            has_memory_meta = True
            try:
                parsed = json.loads(text or "{}")
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                kind = kind or parsed.get("kind")
                ids = ids or parsed.get("ids")
            continue
        if name in _MEMORY_SECTION_NAMES:
            has_known_section = True
        if text:
            parts.append(text)

    # 只有带记忆元信息/已知记忆段名的插件快照才算注入：
    # 系统提示段（sandbox/approval 等）同样是 form=snapshot，但不是记忆。
    if not (has_memory_meta or has_known_section):
        return None

    return {
        "plugin": plugin,
        "form": source.get("form"),
        "kind": str(kind or "unknown"),
        "ids": [i for i in (ids or []) if isinstance(i, str)],
        "text": "\n".join(parts),
    }


def _tool_result_text(msg: dict[str, Any]) -> tuple[str, bool]:
    """返回 (文本, 是否报错)。tool-result 块里可能带 isError。"""
    blocks = msg.get("content")
    if not isinstance(blocks, list):
        return "", False
    out: list[str] = []
    is_error = False
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") != "tool-result":
            continue
        if b.get("isError") or b.get("is_error"):
            is_error = True
        inner = b.get("content")
        if isinstance(inner, list):
            for ib in inner:
                if isinstance(ib, dict) and isinstance(ib.get("text"), str):
                    out.append(ib["text"])
        elif isinstance(inner, str):
            out.append(inner)
    if not out:
        out.append(_text_of(msg))
    return "\n".join(out), is_error


def _args_brief(raw: Any) -> str:
    if isinstance(raw, dict):
        parsed: Any = raw
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return clip(raw, ARG_EXCERPT)
    else:
        return clip(json.dumps(raw, ensure_ascii=False), ARG_EXCERPT)
    if not isinstance(parsed, dict):
        return clip(json.dumps(parsed, ensure_ascii=False), ARG_EXCERPT)
    parts: list[str] = []
    for key in ("query", "slug", "id", "description", "level", "priority", "top_k", "limit", "mem_type", "title", "tags", "project", "status"):
        if key in parsed:
            parts.append(f"{key}={clip(str(parsed[key]), 80)}")
    if not parts:
        parts.append(clip(json.dumps(parsed, ensure_ascii=False), ARG_EXCERPT))
    body = parsed.get("body")
    if isinstance(body, str) and body:
        parts.append(f"body={len(body)}字")
    return " ".join(parts)


def extremes(events: list[dict[str, Any]]) -> dict[str, Any]:
    """把一串事件压成调试摘要。"""
    header: dict[str, Any] = {}
    turns: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []
    calls: dict[str, dict[str, Any]] = {}
    injections: list[dict[str, Any]] = []
    errors: list[str] = []
    counts: dict[str, int] = {}
    timeline: list[dict[str, Any]] = []
    # user/message 不带 turn（实测）：用 turn/start 游标定位当前轮
    current_turn: Any = None

    for event in events:
        etype = event.get("type", "?")
        counts[etype] = counts.get(etype, 0) + 1
        if etype == "session" and not header:
            header = event
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if etype == "turn/start" and data.get("turn") is not None:
            current_turn = data["turn"]
        turn_no = data.get("turn", current_turn)
        if turn_no is not None and turn_no not in turns:
            turns[turn_no] = {"turn": turn_no, "user": "", "injections": [], "calls": []}
            order.append(turn_no)

        if etype in ("user/message", "system/message"):
            msg = _message_of(event)
            if not msg:
                continue
            snap = _snapshot_meta(msg)
            if snap:
                injections.append(snap)
                timeline.append({"kind": "inject", "turn": turn_no, **{k: snap[k] for k in ("plugin", "kind", "ids")}})
                if turn_no in turns:
                    turns[turn_no]["injections"].append(snap)
                continue
            if etype == "user/message" and turn_no in turns and not turns[turn_no]["user"]:
                text = _text_of(msg)
                if text and not _is_internal(text):
                    turns[turn_no]["user"] = clip(text, EXCERPT)
        elif etype in ("tool/call", "tool/ptc-dispatch", "tool/ptc-dispatch-start"):
            name = str(data.get("name", ""))
            if not name.startswith(TOOL_PREFIX):
                continue
            # PTC 会话：嵌套调用只有 ptc-dispatch(-start)，callId 用 subCallId 配对，
            # 结果文本在 data.content[].text（不是 tool/result 的 message 形态）。
            call_id = data.get("subCallId") if etype != "tool/call" else data.get("callId")
            call = calls.get(str(call_id))
            if call is None:
                call = {
                    "callId": call_id,
                    "name": name,
                    "args": _args_brief(data.get("arguments")),
                    "result": "",
                    "error": None,
                }
                calls[str(call_id)] = call
                timeline.append({"kind": "call", "turn": turn_no, "name": name, "args": call["args"]})
                if turn_no in turns:
                    turns[turn_no]["calls"].append(call)
            if etype == "tool/ptc-dispatch":
                blocks = data.get("content")
                text = ""
                if isinstance(blocks, list):
                    text = "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str))
                call["result"] = clip(text, RESULT_EXCERPT)
                if data.get("isError") or "error" in call["result"][:120].lower() or "Traceback" in call["result"]:
                    call["error"] = clip(text, RESULT_EXCERPT)
                    errors.append(f"[{name} {call['args']}] {clip(text, 160)}")
        elif etype == "tool/result":
            msg = _message_of(event)
            if not msg:
                continue
            call_id = (msg.get("source") or {}).get("callId") if isinstance(msg.get("source"), dict) else None
            call = calls.get(str(call_id))
            if call is None:
                continue
            text, is_error = _tool_result_text(msg)
            call["result"] = clip(text, RESULT_EXCERPT)
            if is_error or '"error"' in text[:400] or "Traceback" in text[:400]:
                call["error"] = clip(text, RESULT_EXCERPT)
                errors.append(f"[{call['name']} {call['args']}] {clip(text, 160)}")
        # 任何事件自带的错误字段都收进来
        if isinstance(data.get("error"), (str, dict)) and data.get("error"):
            errors.append(f"[{etype}] {clip(str(data['error']), 160)}")

    return {
        "header": header,
        "turns": [turns[t] for t in order],
        "calls": list(calls.values()),
        "injections": injections,
        "errors": errors,
        "counts": counts,
    }


def build_digest(bundle: dict[str, Any]) -> dict[str, Any]:
    root = extremes(bundle["root"])
    subagents = {name: extremes(evs) for name, evs in bundle["subagents"].items()}
    injections = root["injections"]
    by_kind: dict[str, int] = {}
    for inj in injections:
        by_kind[inj["kind"]] = by_kind.get(inj["kind"], 0) + 1
    header = root["header"]
    return {
        "source": bundle["source"],
        "log": bundle["root_name"],
        "skipped": bundle["skipped"],
        "session": {
            "id": header.get("id"),
            "cwd": header.get("cwd"),
            "agentPreset": header.get("agentPreset"),
            "createdAt": header.get("createdAt"),
            "events": len(bundle["root"]),
        },
        "summary": {
            "turns": len(root["turns"]),
            "injections": len(injections),
            "injectionsByKind": by_kind,
            "memoryToolCalls": len(root["calls"]),
            "memoryToolNames": sorted({c["name"] for c in root["calls"]}),
            "memoryToolBreakdown": {
                name: sum(1 for c in root["calls"] if c["name"] == name)
                for name in sorted({c["name"] for c in root["calls"]})
            },
            "errors": len(root["errors"]),
        },
        "turns": root["turns"],
        "injections": injections,
        "calls": root["calls"],
        "errors": root["errors"],
        "subagents": {name: {"injections": len(d["injections"]), "calls": len(d["calls"])} for name, d in subagents.items()},
        "counts": root["counts"],
    }


# ── 渲染 ──────────────────────────────────────────────────────────────────────


def render_text(digest: dict[str, Any], raw: bool) -> str:
    s = digest["session"]
    sm = digest["summary"]
    out: list[str] = []
    out.append(f"会话 {s['id']}")
    out.append(f"  工作区 {s['cwd']}   preset={s['agentPreset']}   事件 {s['events']} 行")
    out.append(f"  来源 {digest['source']}（日志 {digest['log']}）")
    if digest["skipped"]:
        out.append(f"  跳过（未解析）: {', '.join(digest['skipped'])}")
    out.append(
        f"  轮次 {sm['turns']} | 注入 {sm['injections']} {sm['injectionsByKind'] or ''} | "
        f"memory_* 调用 {sm['memoryToolCalls']} {sm['memoryToolNames'] or ''} | 报错 {sm['errors']}"
    )
    if digest["subagents"]:
        out.append(f"  子代理日志: {digest['subagents']}")

    for turn in digest["turns"]:
        if not (turn["injections"] or turn["calls"]):
            continue
        out.append("")
        out.append(f"── 轮次 {turn['turn']} ──")
        if turn["user"]:
            out.append(f"  用户: {redact(turn['user'], raw)}")
        for inj in turn["injections"]:
            ids = ", ".join(inj["ids"]) if inj["ids"] else "-"
            out.append(f"  注入[{inj['plugin']} / {inj['kind']}] {len(inj['text'])}字  ids: {ids}")
        for call in turn["calls"]:
            out.append(f"  调用 {call['name']} {redact(call['args'], raw)}")
            if call["result"]:
                out.append(f"     → {redact(call['result'], raw)}")
            if call["error"]:
                out.append(f"     !! {redact(call['error'], raw)}")

    if digest["errors"]:
        out.append("")
        out.append("── 报错汇总 ──")
        for err in digest["errors"][:20]:
            out.append("  " + redact(err, raw))
    return "\n".join(out)


def render_markdown(digest: dict[str, Any], raw: bool) -> str:
    s = digest["session"]
    sm = digest["summary"]
    lines = [
        "# 记忆系统调试摘要",
        "",
        f"- 会话：`{s['id']}`（{s['cwd']}，preset={s['agentPreset']}，事件 {s['events']} 行）",
        f"- 导出包：`{digest['source']}`",
        f"- 统计：轮次 {sm['turns']} / 注入 {sm['injections']} / memory_* 调用 {sm['memoryToolCalls']} / 报错 {sm['errors']}",
        f"- 注入分布：`{json.dumps(sm['injectionsByKind'], ensure_ascii=False)}`",
        "",
        "> 本文由 `python -m memory_agent.debug_bundle --out` 生成，"
        + ("**未脱敏（--raw）**。" if raw else "已默认脱敏；需要保真请加 `--raw`。"),
        "",
        "## 逐轮事实链",
        "",
    ]
    for turn in digest["turns"]:
        if not (turn["injections"] or turn["calls"]):
            continue
        lines.append(f"### 轮次 {turn['turn']}")
        if turn["user"]:
            lines.append(f"- 用户：{redact(turn['user'], raw)}")
        for inj in turn["injections"]:
            ids = ", ".join(inj["ids"]) if inj["ids"] else "-"
            lines.append(f"- 注入 `{inj['plugin']}` / `{inj['kind']}`（{len(inj['text'])} 字）：{ids}")
        for call in turn["calls"]:
            lines.append(f"- 调用 `{call['name']}` {redact(call['args'], raw)}")
            if call["result"]:
                lines.append(f"  - 结果：{redact(call['result'], raw)}")
            if call["error"]:
                lines.append(f"  - **报错**：{redact(call['error'], raw)}")
        lines.append("")
    if digest["errors"]:
        lines.append("## 报错汇总")
        lines.append("")
        for err in digest["errors"][:20]:
            lines.append(f"- {redact(err, raw)}")
    return "\n".join(lines)


def newest_in_inbox() -> Path | None:
    if not INBOX_DIR.is_dir():
        return None
    candidates = [p for p in INBOX_DIR.iterdir() if p.is_file() and p.suffix.lower() in (".zip", ".jsonl")]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m memory_agent.debug_bundle",
        description="把 DSH 会话导出包解析成记忆系统调试摘要（默认脱敏）。",
    )
    parser.add_argument("path", nargs="?", help="导出包 .zip 或会话 .jsonl；省略则取 debug-inbox/ 中最新的一个")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--out", help="把 Markdown 报告写到指定文件")
    parser.add_argument("--raw", action="store_true", help="关闭脱敏（保真，慎用）")
    args = parser.parse_args(argv)

    path = Path(args.path) if args.path else newest_in_inbox()
    if path is None:
        print(f"没有找到导出包：请给出路径，或把包放进 {INBOX_DIR}", file=sys.stderr)
        return 2
    if not path.is_file():
        print(f"路径不存在：{path}", file=sys.stderr)
        return 2

    digest = build_digest(load_bundle(path))
    if args.json:
        print(json.dumps(digest, ensure_ascii=False, indent=1))
    else:
        print(render_text(digest, args.raw))
    if args.out:
        out_path = Path(args.out)
        out_path.write_text(render_markdown(digest, args.raw), encoding="utf-8")
        print(f"\n报告已写入 {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
