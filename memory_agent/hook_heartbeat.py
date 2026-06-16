"""Minimal heartbeat hook — just writes timestamp to a file."""
import sys
from datetime import datetime
from pathlib import Path

HEARTBEAT_FILE = Path(__file__).resolve().parent / "heartbeat.log"

try:
    raw = ""
    try:
        if hasattr(sys.stdin, "buffer") and sys.stdin.buffer is not None:
            raw_bytes = sys.stdin.buffer.read()
            raw = raw_bytes.decode("utf-8", errors="replace") if raw_bytes else ""
        else:
            raw = sys.stdin.read()
    except Exception:
        raw = "[stdin read error]"

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(HEARTBEAT_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] HEARTBEAT — stdin: {len(raw)} chars — first 100: {raw[:100]}\n")

    # Still output valid hook JSON so it doesn't break things
    print('{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":""}}')
except Exception as e:
    # Last resort — write error directly
    try:
        with open(HEARTBEAT_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] CRASH: {e}\n")
    except Exception:
        pass
    print('{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":""}}')
