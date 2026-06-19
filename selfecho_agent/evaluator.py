from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class HarnessEvalCase:
    name: str
    check: Callable[[], None]


@dataclass(frozen=True)
class HarnessEvalResult:
    name: str
    ok: bool
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "summary": self.summary,
            "details": self.details,
        }


class HarnessEvaluator:
    """Runs deterministic harness checks without calling a model."""

    def __init__(self, cases: list[HarnessEvalCase] | None = None) -> None:
        self.cases = cases or []

    def add(self, name: str, check: Callable[[], None]) -> None:
        self.cases.append(HarnessEvalCase(name=name, check=check))

    def run(self) -> list[HarnessEvalResult]:
        results: list[HarnessEvalResult] = []
        for case in self.cases:
            try:
                case.check()
            except AssertionError as exc:
                results.append(HarnessEvalResult(case.name, False, str(exc) or "assertion failed"))
            except Exception as exc:  # pragma: no cover - eval output should capture unexpected failures.
                results.append(
                    HarnessEvalResult(
                        case.name,
                        False,
                        f"unexpected error: {exc}",
                        {"error_type": type(exc).__name__},
                    )
                )
            else:
                results.append(HarnessEvalResult(case.name, True, "passed"))
        return results
