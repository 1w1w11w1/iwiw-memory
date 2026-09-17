"""
chat.py — 记忆系统 CLI 工作台（独立 chat 功能）

用法：python -m memory_agent.chat

能力：
- 对话：LLM 回复，启动时注入常驻层（STANDING_LAYERS）记忆全文，
  每轮按话题联想相关记忆注入上下文。
- 记忆工具：模型可在对话中自主调用记忆工具（记住/检索/读取/项目全景）。
- 命令：
  /mem list [priority]       列出记忆
  /mem search <q>            搜索记忆
  /mem read <slug>           读取记忆正文
  /mem edit <slug>           编辑记忆（多行输入，__END__ 结束）
  /mem archive <slug>        归档
  /mem delete <slug>         删除
  /mem merge <t> <s>         合并（输入合并后正文）
  /mem history <slug>        版本历史
  /mem rollback <slug> <v>   回滚到版本
  /pending [status]          待确认动作
  /pending approve <id>      审批通过
  /pending reject <id>       拒绝
  /maintain                  审查记忆维护候选（生成归档待确认动作）
  /consolidate               空闲巩固：审查近期记忆（低风险修正自动留痕执行）
  /stats                     记忆库统计
  /help                      帮助
  /quit                      退出

会话上下文仅保存在内存（最近 N 轮），不持久化；
持久化会话由外部 harness（如 DSH）承担。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

# 白箱可观测开关：MEMORY_AGENT_ECHO_STATE=1 时回显联想注入/提取等内部状态
# （默认关闭，保持对话界面干净；E2E 测试以此作为确定性判定锚点）
ECHO_STATE = os.environ.get("MEMORY_AGENT_ECHO_STATE") == "1"

from .config import (
    CHAT_CONTEXT_TURNS,
    CHAT_RECALL_TOP_K,
    CHAT_MAX_REPLY_TOKENS,
    LLM_MODEL,
    REFLECT_TURNS,
    STANDING_LAYERS,
    TOOL_MAX_TOKENS,
    TOOL_TEMPERATURE,
    TOOL_TIMEOUT,
    TOOL_MAX_LOOPS,
)
from .db import (
    get_memory,
    list_memories,
    upsert_memory,
    touch_memories,
    list_pending_actions,
    approve_pending_action_result,
    reject_pending_action,
    replace_memory_result,
    archive_memory_result,
    delete_memory_result,
    merge_memories_result,
    list_history,
    restore_memory_from_history_result,
    get_stats,
)
from .retrieval import search_memories
from .model_tools import MEMORY_TOOLS, execute_memory_tool
from .llm import stream_text, complete_with_tools, LLMConfigurationError

# ── 系统提示 ──

BASE_SYSTEM_PROMPT = """你是一个「记忆工作台」里的对话助手，服务于一个拥有长期记忆系统的个人用户。

规则：
1. 记忆分类：类型五层（profile/fact/lesson/rules/project）× 生命周期（active/archived）；profile 与 rules 常驻注入，其余按话题检索召回。
2. 回复中如涉及记忆内容，直接自然引用，不要提及内部机制（如 slug、检索分数）。
3. 你可以在回复前自主调用记忆工具：
   - 出现值得长期保存的稳定事实（身份、偏好、决策、健康、关系、计划）时调用 memory_remember；
   - 需要确认用户既往事实或依据时调用 memory_search（system 已注入的记忆是背景知识，无需重复检索）。
   不要向用户承诺"已记住"，除非你确实调用了 memory_remember；
   用户明确要求记住或更正时立即调用，用户最新表述优先。
4. 简洁、真诚，使用中文。
"""


# ── 模型记忆工具（function calling，共享执行逻辑见 model_tools.py）──

async def _execute_memory_tool(tc: dict[str, Any]) -> tuple[dict[str, str], str]:
    """执行一个 tool_call，返回 (OpenAI tool 消息, 人类可读摘要)。"""
    fn = tc.get("function") or {}
    try:
        args = json.loads(fn.get("arguments") or "{}")
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        args = {}
    out = execute_memory_tool(fn.get("name", ""), args)
    msg = {
        "role": "tool",
        "tool_call_id": tc.get("id", ""),
        "content": json.dumps(out["result"], ensure_ascii=False),
    }
    return msg, out["echo"]


def _always_load_text() -> str:
    """组装常驻记忆全文（STANDING_LAYERS 各层，启动时注入系统提示）。

    按层分节：profile 等身份层进「长期记忆」；rules 层进「准则」
    （准则的"严格遵守"包装是行为生效的关键）。受 MEMORY_RECALL_MAX_CHARS
    字符预算约束：超出按记录截断，避免常驻层无限膨胀。
    """
    from .config import MEMORY_RECALL_MAX_CHARS
    memory_lines: list[str] = []
    rule_lines: list[str] = []
    budget = MEMORY_RECALL_MAX_CHARS

    def _collect(target: list[str], mems: list[dict]) -> None:
        nonlocal budget
        for mem in mems:
            header = f"- **{mem['slug']}** ({mem['mem_type']}) — {mem.get('description', '')}"
            body = mem.get("content", "")
            if len(body) > budget:
                body = body[:budget] + "..."
                budget = 0
            else:
                budget -= len(body)
            if budget < 0:
                break
            target.append(header)
            target.append(f"  {body}")

    for layer in STANDING_LAYERS:
        mems = list_memories(priority="active", mem_type=layer)
        if layer == "rules":
            _collect(rule_lines, mems)
        else:
            _collect(memory_lines, mems)

    sections: list[str] = []
    if memory_lines:
        sections.append("\n".join(["## 长期记忆（常驻）", *memory_lines]))
    if rule_lines:
        sections.append("\n".join([
            "## 准则（用户要求持续遵守）",
            "以下准则来自记忆库 rules 层，请在本会话中严格遵守：",
            *rule_lines,
        ]))
    return "\n\n".join(sections)


def _related_text(
    user_message: str,
    context: list[dict[str, str]],
    exclude_slugs: set[str] | None = None,
    topic_words: list[str] | None = None,
    session_state: Any | None = None,
) -> tuple[str, set[str]]:
    """按话题联想相关记忆，返回 (注入文本, 注入的记忆 slug 集合)。

    - exclude_slugs：排除已注入过的记忆（常驻层 + 窗口内已联想）。
    - topic_words：会话主题轨迹（高频话题词）。
    - session_state：SessionState（DSC）——回指联想的确定性来源：
      "那件事/那个兴趣班"无关键词时，从会话状态里最近/高频讨论的话题召回记忆。
    - 注入的是"联想"信号：进入 system 层，让模型当作已知背景自然引用。
    - 受 MEMORY_RECALL_MAX_CHARS 字符预算约束（总注入量）。
    """
    from .config import MEMORY_RECALL_MAX_CHARS
    ctx_msgs = [m["content"] for m in context[-4:]]
    if topic_words:
        ctx_msgs = ctx_msgs + [" ".join(topic_words[:8])]
    try:
        results = search_memories(
            user_message,
            context_messages=ctx_msgs,
            top_k=CHAT_RECALL_TOP_K,
            exclude_slugs=exclude_slugs,
            topic_words=topic_words,
            session_state=session_state,
        )
    except Exception:
        return "", set()
    if not results:
        return "", set()
    lines = ["## 相关记忆（联想）"]
    injected: set[str] = set()
    budget = MEMORY_RECALL_MAX_CHARS
    for mem in results:
        header = f"- **{mem['slug']}** ({mem['priority']}) — {mem.get('description', '')}"
        body = (mem.get("content") or "")
        if len(body) > budget:
            body = body[:budget] + "..."
        budget -= len(body)
        if budget < 0:
            break
        lines.append(header)
        lines.append(f"  {body}")
        injected.add(mem["slug"])
    return "\n".join(lines), injected


# ── 命令处理 ──

def _fmt_result(r: Any) -> str:
    d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
    if d.get("ok"):
        return f"✓ {d['action']}: {d.get('target_slug')} (rows={d.get('changed_rows')}, version={d.get('version_id')}, audit={d.get('audit_id')})"
    return f"✗ {d.get('action')}: {d.get('error')}"


async def _input_async(prompt: str = "") -> str:
    return await asyncio.to_thread(input, prompt)


async def _read_multiline(prompt: str) -> str:
    print(prompt)
    print("（输入完成后单独一行 __END__ 结束）")
    parts: list[str] = []
    while True:
        line = await _input_async("  | ")
        if line.strip() == "__END__":
            break
        parts.append(line)
    return "\n".join(parts).strip()


async def _handle_command(cmd: str, rest: str, state: dict[str, Any]) -> bool:
    """处理命令；返回 False 表示退出。"""
    c = cmd.lower()

    if c == "quit" or c == "exit":
        return False

    if c == "help":
        print(__doc__)
        return True

    if c == "mem":
        args = rest.split()
        sub = args[0] if args else ""
        if sub == "list":
            priority = args[1] if len(args) > 1 else None
            mems = list_memories(priority=priority)
            if not mems:
                print("（暂无记忆）")
            for m in mems:
                print(f"  [{m['priority']:9s}][{m['mem_type']:8s}] {m['slug']:<40s} {m.get('description', '')[:50]}")
        elif sub == "search":
            query = rest[len("search"):].strip()
            results = search_memories(query, top_k=8)
            if not results:
                print("（无结果）")
            for r in results:
                print(f"  [{r['score']:.3f}][{r['priority']:9s}] {r['slug']} — {r.get('description', '')[:60]}")
                print(f"      {(r.get('content') or '')[:120]}")
        elif sub == "read":
            slug = args[1] if len(args) > 1 else ""
            mem = get_memory(slug)
            if not mem:
                print(f"（未找到：{slug}）")
            else:
                print(f"== {mem['slug']} [{mem['priority']}/{mem['mem_type']}] {mem.get('description', '')}")
                print(mem["content"])
        elif sub == "edit":
            slug = args[1] if len(args) > 1 else ""
            mem = get_memory(slug)
            if not mem:
                print(f"（未找到：{slug}）")
                return True
            print(f"== 当前正文（{slug}）==\n{mem['content']}")
            body = await _read_multiline("== 输入新正文 ==")
            if not body:
                print("（放弃编辑）")
                return True
            result = replace_memory_result(
                slug=slug, description=mem.get("description", ""), body=body,
                mem_type=mem.get("mem_type", "profile"), priority=mem.get("priority", "active"),
                event_date=mem.get("event_date"), reason="chat edit", audit_action="chat_edit",
            )
            print(_fmt_result(result))
        elif sub == "archive":
            result = archive_memory_result(args[1], reason="chat archive") if len(args) > 1 else None
            print(_fmt_result(result) if result else "用法: /mem archive <slug>")
        elif sub == "delete":
            if len(args) < 2:
                print("用法: /mem delete <slug>")
                return True
            confirm = await _input_async(f"确认删除 {args[1]}？（y/N）")
            if confirm.strip().lower() != "y":
                print("（已取消）")
                return True
            print(_fmt_result(delete_memory_result(args[1], reason="chat delete")))
        elif sub == "merge":
            if len(args) < 3:
                print("用法: /mem merge <target> <source>")
                return True
            target, source = args[1], args[2]
            print(f"== 目标（{target}）==\n{(get_memory(target) or {}).get('content', '')}")
            print(f"== 来源（{source}）==\n{(get_memory(source) or {}).get('content', '')}")
            body = await _read_multiline("== 输入合并后的完整正文 ==")
            if not body:
                print("（放弃合并）")
                return True
            desc = (get_memory(target) or {}).get("description", "")
            result = merge_memories_result(target, source, body, desc)
            print(_fmt_result(result))
        elif sub == "history":
            if len(args) < 2:
                print("用法: /mem history <slug>")
                return True
            for v in list_history(args[1]):
                print(f"  v{v['version']}  {v['mtime']}  {v['size']}B  {v.get('reason', '')[:60]}")
        elif sub == "rollback":
            if len(args) < 3:
                print("用法: /mem rollback <slug> <version>")
                return True
            print(_fmt_result(restore_memory_from_history_result(args[1], args[2], reason="chat rollback")))
        else:
            print("用法: /mem list|search|read|edit|archive|delete|merge|history|rollback")
        return True

    if c == "pending":
        args = rest.split()
        if args and args[0] in ("approve", "reject"):
            if len(args) < 2:
                print(f"用法: /pending {args[0]} <id>")
                return True
            if args[0] == "approve":
                print(_fmt_result(approve_pending_action_result(args[1])))
            else:
                print(f"✓ rejected: {reject_pending_action(args[1])}")
            return True
        status = args[0] if args else "pending"
        items = list_pending_actions(status)
        if not items:
            print("（无待确认动作）")
        for it in items:
            print(f"  [{it['status']}] {it['id'][:8]} {it['action']:9s} -> {it.get('target_slug')}  {it.get('reason', '')[:60]}")
        return True

    if c == "maintain":
        print("（正在审查记忆维护候选...）")
        from .maintenance import review_maintenance
        result = await review_maintenance()
        if result.get("error"):
            print(f"✗ 维护审查失败: {result['error']}")
        elif not result["pending"]:
            print(f"✓ 审查了 {result['reviewed']} 条候选，没有需要归档的（或已全部保持）")
        else:
            print(f"✓ 审查了 {result['reviewed']} 条候选，生成 {len(result['pending'])} 条待确认动作：")
            for p in result["pending"]:
                print(f"  [{p['id'][:8]}] archive -> {p['slug']}  {p['reason']}")
            print("  使用 /pending approve <id> 或 /pending reject <id> 处理")
        return True

    if c == "consolidate":
        print("（巩固中……）")
        from .maintenance import run_consolidate
        r = await run_consolidate()
        if r.get("error"):
            print(f"✗ 巩固失败: {r['error']}")
            return True
        print(f"✓ 审查了 {r['reviewed']} 条近期记忆")
        for f in r["auto_fixed"]:
            print(f"  自动修正: {f['slug']} -> {f['action']}" + (f" ({f.get('mem_type')})" if f.get("mem_type") else ""))
        for p in r["pending"]:
            print(f"  [待审批] archive -> {p['slug']}  {p['reason']}")
        for s in r["suggestions"]:
            print(f"  [合并建议] {s['slug']} → {s['target_slug']}  {s['reason']}")
        if not (r["auto_fixed"] or r["pending"] or r["suggestions"]):
            print("  无需整理")
        if r["pending"]:
            print("  使用 /pending approve <id> 或 /pending reject <id> 处理")
        return True

    if c == "stats":
        s = get_stats()
        print(f"记忆总数: {s['total_memories']}")
        print(f"  by priority: {s['by_priority']}")
        print(f"  by type: {s['by_type']}")
        return True

    print(f"（未知命令 /{cmd}，/help 查看用法）")
    return True


# ── 对话主循环 ──

async def run() -> None:
    print("=" * 60)
    print(" 记忆系统 · CLI 工作台")
    print(" 输入 /help 查看命令；/quit 退出")
    print("=" * 60)

    history: list[dict[str, str]] = []
    # injected_slugs: slug -> 最近注入的轮次（用于"窗口内去重"：
    # 记忆滑出上下文窗口后允许重新联想，避免长会话中联想枯竭）
    # topic_freq: 会话主题轨迹（词 -> 频次），回指联想的关键线索
    from .session_state import SessionState
    session = SessionState()
    session.build_word_index(list_memories())
    state: dict[str, Any] = {
        "last_user_message": "", "injected_slugs": {}, "_turn": 0, "topic_freq": {},
        "session": session, "compacted": "", "since_write": 0,
    }

    while True:
        try:
            line = await _input_async("你> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        line = line.strip()
        if not line:
            continue

        if line.startswith("/"):
            parts = line[1:].split(None, 1)
            cmd = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            if not await _handle_command(cmd, rest, state):
                break
            continue

        # ── 组装上下文（联想注入 → system 层，模型当作已知背景）──
        # reflect steering：连续 REFLECT_TURNS 轮未写入 → 注入一次性回顾提示
        reflect_nudge = ""
        if REFLECT_TURNS > 0 and state.get("since_write", 0) >= REFLECT_TURNS:
            reflect_nudge = (
                "（会话回顾提示：最近多轮未写入记忆。若此前对话出现值得长期保存的稳定事实、"
                "决策或偏好，请在本次回复前调用 memory_remember；确认没有则忽略本提示。）"
            )
            state["since_write"] = 0
            if ECHO_STATE:
                print("[reflect steering] 注入回顾提示")
        system = BASE_SYSTEM_PROMPT
        # 每轮重算常驻层与词面索引：会话中新写入的记忆立即生效（A2 一致性）
        always_load = _always_load_text()
        if always_load:
            system += "\n\n" + always_load
            if ECHO_STATE:
                print("[standing 注入] " + ", ".join(
                    m["slug"] for layer in STANDING_LAYERS for m in list_memories(priority="active", mem_type=layer)
                ))
        session.build_word_index(list_memories())
        core_slugs = {
            m["slug"] for layer in STANDING_LAYERS for m in list_memories(priority="active", mem_type=layer)
        }
        # 窗口内去重：只排除"最近 W 轮内注入过"的记忆（已滑出窗口的允许重新联想）
        state["_turn"] = int(state.get("_turn", 0)) + 1
        # 注入只进 system（每轮重建），不驻留上下文；窗口仅防高频重复（话题通常连续几轮）
        inject_window = 4
        recent = {
            s for s, r in state.get("injected_slugs", {}).items()
            if r >= state["_turn"] - inject_window
        }
        session_excluded = core_slugs | recent
        # 会话主题轨迹：每轮用户消息的主题 n-gram 累加（高频词 = 重要话题线索）
        from .query_builder import extract_topic_grams, filter_topic_words
        grams = extract_topic_grams(line)
        for kw in grams:
            state["topic_freq"][kw] = state["topic_freq"].get(kw, 0) + 1
        topic_words = filter_topic_words(state["topic_freq"], limit=30)
        # 会话状态（DSC）：更新话题/决策/任务
        session.update(line, grams)
        # DSC 压缩：历史超过窗口时，把早期上下文压成 SessionState 检查点，保留尾部原文
        from .config import CHAT_COMPACT_TAIL_TURNS
        if len(history) > CHAT_CONTEXT_TURNS * 2:
            checkpoint = session.render_checkpoint()
            if checkpoint:
                state["compacted"] = checkpoint
            keep = CHAT_COMPACT_TAIL_TURNS * 2
            history = history[-keep:]
        related, injected = _related_text(
            line, history,
            exclude_slugs=session_excluded,
            topic_words=topic_words,
            session_state=session,
        )
        if related:
            system += "\n\n" + related
            for s in injected:
                state["injected_slugs"][s] = state["_turn"]
            session.note_recalled(injected)
            touch_memories(sorted(injected))
            if ECHO_STATE:
                print(f"[联想注入] {', '.join(sorted(injected))}")
        if state.get("compacted"):
            system += "\n\n" + state["compacted"]
        if reflect_nudge:
            system += "\n\n" + reflect_nudge
        user_prompt = line

        messages = [{"role": "system", "content": system}]
        for msg in history[-(CHAT_CONTEXT_TURNS * 2):]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_prompt})

        # ── 记忆工具轮（回复前，非流式；模型自主决定写入/检索/读取）──
        tool_round_msgs: list[dict[str, Any]] = []
        wrote_memory = False
        try:
            for _ in range(TOOL_MAX_LOOPS):
                result = await complete_with_tools(
                    system_prompt=system,
                    messages=messages + tool_round_msgs,
                    tools=MEMORY_TOOLS,
                    max_tokens=TOOL_MAX_TOKENS,
                    temperature=TOOL_TEMPERATURE,
                    timeout=TOOL_TIMEOUT,
                    model=LLM_MODEL,
                )
                if result["finish_reason"] != "tool_calls":
                    break
                tool_round_msgs.append(result["message"])
                for tc in result["message"].get("tool_calls") or []:
                    tool_msg, echo = await _execute_memory_tool(tc)
                    tool_round_msgs.append(tool_msg)
                    if (tc.get("function") or {}).get("name") == "memory_remember":
                        wrote_memory = True
                    if ECHO_STATE:
                        print(f"[记忆工具] {echo}")
            messages.extend(tool_round_msgs)
        except LLMConfigurationError as exc:
            print(f"[记忆工具不可用: {exc}]")
        except Exception as exc:
            print(f"[记忆工具失败: {exc}]")
        finally:
            state["since_write"] = 0 if wrote_memory else state.get("since_write", 0) + 1

        # ── 流式回复 ──
        try:
            print("助手> ", end="", flush=True)
            reply_parts: list[str] = []
            async for chunk in stream_text(
                system_prompt=system,
                user_prompt=user_prompt,
                messages=messages,
                max_tokens=CHAT_MAX_REPLY_TOKENS,
                temperature=0.7,
                timeout=60.0,
                model=LLM_MODEL,
            ):
                print(chunk, end="", flush=True)
                reply_parts.append(chunk)
            print()
            reply = "".join(reply_parts)
        except LLMConfigurationError as exc:
            print(f"\n（LLM 配置错误: {exc}。请设置 MEMORY_AGENT_LLM_API_KEY）")
            continue
        except Exception as exc:
            print(f"\n（回复失败: {exc}）")
            continue

        history.append({"role": "user", "content": line})
        history.append({"role": "assistant", "content": reply})
        state["last_user_message"] = line



def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
