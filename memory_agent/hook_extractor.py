#!/usr/bin/env python
"""
hook_extractor.py — pre-message hook 自动记忆提取

取代当前 pre-message-reminder.sh 的「提醒」角色，改为「自动执行」角色。

工作流：
1. 从 stdin 读取 Claude Code hook 传入的 JSON
2. 提取本次用户消息
3. 调用 DeepSeek API 分析是否需要保存记忆
4. 如果需要 → 写入 memory/ 目录（ADD-only + hash 去重）
5. 输出 JSON → hookSpecificOutput.additionalContext（仅状态摘要，不占用上下文）

关键设计：
- 超时保护：API 调用最长 15 秒，超时直接跳过，不阻塞对话
- 静默失败：任何异常都不影响主对话流程
- 低上下文占用：additionalContext 只输出简短状态（~50 tokens），不注入全文
"""

import asyncio
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# 确保 memory-agent 在 sys.path 中
AGENT_DIR = Path(__file__).parent
if str(AGENT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR.parent))

from memory_agent.config import HOOK_REPORT_STATUS
from memory_agent.extractor import extract_and_save, should_skip_message

LOG_FILE = AGENT_DIR / "hook_extractor.log"
MAX_WAIT_SECONDS = 20  # 硬超时：hook 总执行时间上限


def log(msg: str) -> None:
    """简单日志，不依赖 logging（加快启动）"""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 移除 surrogate 字符，写入 UTF-8 安全
        safe_msg = msg.encode("utf-8", errors="replace").decode("utf-8")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {safe_msg}\n")
    except Exception:
        pass  # 日志失败不要影响对话


def read_stdin(timeout: float = 5.0) -> str:
    """
    读取 stdin。Windows 上 select 不支持非 socket，用线程+超时。
    使用 buffer 模式读取原始字节，避免 text mode stdin 在 Windows 上的编码问题。
    """
    import threading

    result_bytes = []
    read_error = []

    def _read():
        try:
            # 优先用 buffer 读取原始字节
            # Windows 上 text mode stdin 编码不可靠（可能误用 GBK 等）
            if hasattr(sys.stdin, "buffer") and sys.stdin.buffer is not None:
                result_bytes.append(sys.stdin.buffer.read())
            else:
                # 回退到 text mode（极少情况）
                text = sys.stdin.read()
                result_bytes.append(text.encode("utf-8", errors="replace"))
        except Exception as e:
            read_error.append(e)

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return ""  # 超时
    if read_error:
        raise read_error[0]
    if not result_bytes:
        return ""

    raw = result_bytes[0]
    if not raw:
        return ""

    # 解码：优先 UTF-8，失败则尝试 GBK（Windows 中文环境常见）
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")


def extract_user_message(payload: dict) -> str:
    """
    从 hook payload 中提取用户最新消息。

    支持两种事件格式：
    - UserPromptSubmit (VSCode 扩展): prompt 字段
    - pre-message (CLI 旧格式): messages 数组 / transcript 等
    """
    # 尝试 1：UserPromptSubmit 格式 — prompt 字段
    prompt = payload.get("prompt", "")
    if prompt and isinstance(prompt, str) and len(prompt.strip()) >= 8:
        return prompt

    # 尝试 2：直接字段（旧 pre-message 格式）
    for key in ("latest_message", "last_message", "message", "text", "content"):
        if key in payload and isinstance(payload[key], str):
            return payload[key]

    # 尝试 3：messages 数组（取最后一条 role=user 的）
    messages = payload.get("messages", [])
    if isinstance(messages, list):
        for msg in reversed(messages):
            if isinstance(msg, dict):
                if msg.get("role") == "user" or msg.get("type") == "user":
                    return msg.get("content", msg.get("text", ""))
            elif isinstance(msg, str):
                return msg

    # 尝试 4：转录文本（最后一段）
    transcript = payload.get("transcript", "")
    if transcript:
        return transcript

    return ""


def extract_context(payload: dict, current_message: str) -> str:
    """提取对话上下文（排除当前消息）。

    当用户消息较短（可能是对 AI 分析的回应）时，尽量保留 AI 分析全文，
    以便记忆提取模型判断用户是否采纳了 AI 的观点。
    """
    messages = payload.get("messages", [])
    if not isinstance(messages, list) or len(messages) < 2:
        return ""

    user_msg_short = len(current_message.strip()) < 200

    context_parts = []
    for msg in reversed(messages[:-1]):
        if isinstance(msg, dict):
            role = msg.get("role", "")
            content = msg.get("content", msg.get("text", ""))
            if content and content != current_message:
                # AI 消息在用户短回复场景下保留更多（800 字），便于捕获被采纳的观点
                if user_msg_short and role in ("assistant", "ai", "claude"):
                    context_parts.insert(0, f"[{role}]: {content[:800]}")
                else:
                    context_parts.insert(0, f"[{role}]: {content[:500]}")

        # 用户短回复时多取几轮上下文
        max_parts = 6 if user_msg_short else 4
        if len(context_parts) >= max_parts:
            break

    return "\n".join(context_parts)


def output_nothing() -> None:
    """什么都不注入（最常见的成功路径——静默保存）"""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "",
        }
    }))


def output_status(summary: str) -> None:
    """输出简短状态（约 30-80 tokens，几乎不占上下文）"""
    if not HOOK_REPORT_STATUS:
        output_nothing()
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": f"[memory-agent] {summary}",
        }
    }))


async def main():
    start_time = time.time()
    # ── 0. 心跳日志：确认脚本被启动 ──
    log("HOOK STARTED")
    try:
        # ── 1. 读取 hook 输入 ──
        raw = read_stdin(timeout=5.0)
        if raw:
            log(f"stdin received: {len(raw)} chars")
        else:
            log(f"stdin received: 0 chars (buffer_mode={'buffer' if hasattr(sys.stdin, 'buffer') else 'text'})")

        payload = {}
        if raw.strip():
            try:
                payload = json.loads(raw)
                log(f"Payload keys: {list(payload.keys())[:10]}")
            except json.JSONDecodeError:
                log(f"stdin not valid JSON: {raw[:200]}")
                output_nothing()
                return

        # ── 2. 提取用户消息 ──
        message = extract_user_message(payload)
        if not message:
            log("No user message found in payload")
            output_nothing()
            return

        # 检查长度和内容质量
        if len(message.strip()) < 8:
            log(f"Message too short ({len(message)} chars)")
            output_nothing()
            return

        if should_skip_message(message):
            log(f"Message skipped by prefilter: {message[:100]}...")
            output_nothing()
            return

        elapsed = time.time() - start_time
        if elapsed > MAX_WAIT_SECONDS * 0.8:
            log(f"Pre-processing too slow ({elapsed:.1f}s), skipping")
            output_nothing()
            return

        # ── 3. 提取上下文 ──
        context = extract_context(payload, message)

        # ── 4. 提取并保存记忆 ──
        log(f"Extracting from: {message[:100]}...")
        saved = await extract_and_save(message, context)

        # ── 5. 输出 ──
        if saved:
            summary = f"已自动保存 {len(saved)} 条新记忆: {', '.join(saved[:3])}"
            if len(saved) > 3:
                summary += f" 等 {len(saved)} 条"
            log(f"Saved: {saved}")
            output_status(summary)
        else:
            log("No new memories extracted")
            output_nothing()

    except asyncio.TimeoutError:
        log(f"Timeout after {time.time() - start_time:.1f}s")
        output_nothing()
    except Exception as e:
        log(f"Error: {e}\n{traceback.format_exc()}")
        output_nothing()  # 静默失败，不影响对话


if __name__ == "__main__":
    asyncio.run(main())
