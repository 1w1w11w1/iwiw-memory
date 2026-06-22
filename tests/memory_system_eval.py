from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import memory_agent.db as memory_db
from selfecho_agent.evaluator import HarnessEvaluator


class IsolatedMemoryDb:
    def __enter__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tmpdir.name) / "sessions.db"
        self.original_path = memory_db.MEMORY_DB_PATH
        self.original_refresh = memory_db.refresh_memory_vectors
        memory_db.close()
        memory_db.MEMORY_DB_PATH = self.path
        memory_db.refresh_memory_vectors = self._refresh_noop  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        memory_db.close()
        memory_db.MEMORY_DB_PATH = self.original_path
        memory_db.refresh_memory_vectors = self.original_refresh  # type: ignore[assignment]
        self._tmpdir.cleanup()

    @staticmethod
    def _refresh_noop(memory_id: str, model_version: str = "test") -> dict:
        return {"ok": True, "chunks": 0, "model_version": model_version}


def _audit_actions() -> list[str]:
    rows = memory_db.connect().execute(
        "SELECT action FROM memory_audit ORDER BY rowid"
    ).fetchall()
    return [row["action"] for row in rows]


def test_upsert_records_audit_and_versions() -> None:
    with IsolatedMemoryDb():
        memory_db.upsert_memory(
            slug="study-plan",
            description="考试安排",
            content="用户有 2026 年期末考试安排。",
            priority="normal",
        )
        memory_db.upsert_memory(
            slug="study-plan",
            description="考试安排更新",
            content="追加一条考试安排细节。",
            priority="normal",
        )
        actions = _audit_actions()
        assert actions == ["create", "upsert_append"], f"unexpected audit actions: {actions}"
        version_count = memory_db.connect().execute(
            "SELECT COUNT(*) FROM memory_versions"
        ).fetchone()[0]
        assert version_count == 1, f"append should snapshot previous content, got {version_count}"


def test_destructive_mutations_return_contract() -> None:
    with IsolatedMemoryDb():
        memory_db.upsert_memory(
            slug="gift-preference",
            description="礼物偏好",
            content="用户提到礼物偏好用于测试。",
        )
        edit = memory_db.replace_memory_result(
            slug="gift-preference",
            description="礼物偏好已编辑",
            body="用户提到礼物偏好已更新。",
            reason="eval edit",
        )
        archive = memory_db.archive_memory_result("gift-preference", reason="eval archive")
        history = memory_db.list_history("gift-preference")
        delete = memory_db.delete_memory_result("gift-preference", reason="eval delete")
        restore = memory_db.restore_memory_from_history_result(
            "gift-preference",
            str(history[0]["version"]),
            reason="eval restore",
        )

        for name, result in {
            "edit": edit,
            "archive": archive,
            "delete": delete,
            "restore": restore,
        }.items():
            data = result.to_dict()
            assert data["ok"], f"{name} failed: {data}"
            assert data["changed_rows"] == 1, f"{name} changed_rows missing: {data}"
            assert data["audit_id"], f"{name} audit_id missing: {data}"
            if name != "restore":
                assert data["version_id"], f"{name} version_id missing: {data}"


def test_cjk_fts_fallback_ignores_generic_question_tokens() -> None:
    with IsolatedMemoryDb():
        memory_db.upsert_memory(
            slug="exam-schedule-2026",
            description="2026 年期末考试安排",
            content="用户在 2026 年 6 月 25 日至 7 月 1 日期间有期末考试安排。",
        )
        memory_db.upsert_memory(
            slug="generic-question",
            description="泛化问答",
            content="这里包含很多什么、怎么、为什么之类的泛化词。",
        )
        rows = memory_db.search_fts("考试安排是什么", limit=5)
        slugs = [row["slug"] for row in rows]
        assert slugs and slugs[0] == "exam-schedule-2026", f"bad CJK fallback order: {slugs}"
        assert "generic-question" not in slugs, f"generic tokens polluted fallback: {slugs}"


def main() -> int:
    evaluator = HarnessEvaluator()
    cases: dict[str, Callable[[], None]] = {
        "memory_upsert_audit_versions": test_upsert_records_audit_and_versions,
        "memory_mutation_contract": test_destructive_mutations_return_contract,
        "memory_cjk_fallback": test_cjk_fts_fallback_ignores_generic_question_tokens,
    }
    for name, check in cases.items():
        evaluator.add(name, check)
    results = evaluator.run()
    print(json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
