from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "selfecho_data"
DB_PATH = DATA_DIR / "sessions.db"
AUDIT_DIR = DATA_DIR / "audit"
EXPORTS_DIR = DATA_DIR / "exports"
LEGACY_DIR = DATA_DIR / "legacy"
LEGACY_DB_PATH = LEGACY_DIR / "claude_code_history.db"


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    LEGACY_DIR.mkdir(parents=True, exist_ok=True)
