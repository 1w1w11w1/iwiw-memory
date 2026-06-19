from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContextSectionBudget:
    title: str
    source: str
    chars: int
    estimated_tokens: int
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "source": self.source,
            "chars": self.chars,
            "estimated_tokens": self.estimated_tokens,
            "warnings": self.warnings,
        }


@dataclass(frozen=True)
class ContextBudgetReport:
    sections: list[ContextSectionBudget]
    total_chars: int
    estimated_tokens: int
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sections": [section.to_dict() for section in self.sections],
            "total_chars": self.total_chars,
            "estimated_tokens": self.estimated_tokens,
            "warnings": self.warnings,
        }


class ContextBudget:
    """Lightweight context accounting before automatic trimming exists."""

    LARGE_SECTION_TOKENS = 2400
    LARGE_PROMPT_TOKENS = 10000

    def measure(self, sections: list[str]) -> ContextBudgetReport:
        budgets: list[ContextSectionBudget] = []
        seen_titles: dict[str, int] = {}
        warnings: list[str] = []
        for section in sections:
            title = self._title(section)
            seen_titles[title] = seen_titles.get(title, 0) + 1
            estimated_tokens = self._estimate_tokens(section)
            section_warnings: list[str] = []
            if estimated_tokens > self.LARGE_SECTION_TOKENS:
                section_warnings.append("section_is_large")
            budgets.append(
                ContextSectionBudget(
                    title=title,
                    source=self._source(title),
                    chars=len(section),
                    estimated_tokens=estimated_tokens,
                    warnings=section_warnings,
                )
            )

        for title, count in sorted(seen_titles.items()):
            if count > 1:
                warnings.append(f"duplicate_section:{title}")
        total_chars = sum(item.chars for item in budgets)
        total_tokens = sum(item.estimated_tokens for item in budgets)
        if total_tokens > self.LARGE_PROMPT_TOKENS:
            warnings.append("prompt_is_large")

        return ContextBudgetReport(
            sections=budgets,
            total_chars=total_chars,
            estimated_tokens=total_tokens,
            warnings=warnings,
        )

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        return max(1, round(len(text) / 4))

    def _title(self, section: str) -> str:
        first = section.strip().splitlines()[0] if section.strip() else "未命名上下文"
        return first.strip("# ").strip()

    def _source(self, title: str) -> str:
        if "长期记忆" in title:
            return "long_term_memory"
        if "当前工作目录" in title or "目录快照" in title:
            return "project_context"
        if "当前会话" in title or "最近对话" in title:
            return "session_context"
        if "运行决策" in title:
            return "routing"
        if "执行计划" in title:
            return "plan"
        if "工具观察" in title:
            return "tool_observation"
        if "身份" in title:
            return "identity"
        return "prompt"
