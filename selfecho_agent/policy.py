from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .modes import IntentDecision


@dataclass(frozen=True)
class RiskPolicy:
    risk_level: str
    requires_confirmation: bool
    categories: list[str] = field(default_factory=list)
    rollback_required: bool = False
    guidance: str = ""
    recovery_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "requires_confirmation": self.requires_confirmation,
            "categories": self.categories,
            "rollback_required": self.rollback_required,
            "guidance": self.guidance,
            "recovery_steps": self.recovery_steps,
        }


class PolicyEngine:
    """Deterministic safety policy until a richer tool permission model exists."""

    NETWORK_TERMS = ("联网", "网上", "互联网", "浏览器", "网页", "下载", "请求", "api")
    GIT_TERMS = ("git", "commit", "push", "pull", "merge", "rebase", "checkout")
    FILE_WRITE_TERMS = ("修改", "编辑", "写入", "删除", "创建", "移动", "重命名", "覆盖", "保存")
    MEMORY_WRITE_TERMS = ("写入记忆", "删除记忆", "合并记忆", "归档", "回滚记忆", "整理上下文")
    IRREVERSIBLE_TERMS = ("永久删除", "清空", "覆盖", "无法回退", "不可恢复")

    def assess(self, message: str, decision: IntentDecision) -> RiskPolicy:
        text = message.lower()
        categories: list[str] = []

        if any(term in text for term in self.NETWORK_TERMS):
            categories.append("network")
        if any(term in text for term in self.GIT_TERMS):
            categories.append("git")
        if any(term in text for term in self.FILE_WRITE_TERMS):
            categories.append("file_write")
        if any(term in text for term in self.MEMORY_WRITE_TERMS) or decision.intent == "memory":
            categories.append("memory_write")
        if any(term in text for term in self.IRREVERSIBLE_TERMS):
            categories.append("hard_to_rollback")

        categories = sorted(set(categories))
        if "hard_to_rollback" in categories or "git" in categories:
            risk_level = "high"
        elif {"file_write", "memory_write", "network"} & set(categories):
            risk_level = "medium"
        else:
            risk_level = "low"

        requires_confirmation = risk_level in {"medium", "high"}
        rollback_required = bool({"file_write", "memory_write", "git", "hard_to_rollback"} & set(categories))
        return RiskPolicy(
            risk_level=risk_level,
            requires_confirmation=requires_confirmation,
            categories=categories,
            rollback_required=rollback_required,
            guidance=self._guidance(risk_level, categories),
            recovery_steps=self._recovery_steps(categories),
        )

    def _guidance(self, risk_level: str, categories: list[str]) -> str:
        if risk_level == "high":
            return "高风险动作必须先说明影响范围、备份和回退方案，再等待用户批准。"
        if risk_level == "medium":
            return "中风险动作应先给出计划和确认点，不直接执行写入、联网或记忆修改。"
        if categories:
            return "检测到潜在工具意图，但当前可先以分析和计划为主。"
        return "低风险对话，可直接回答或继续澄清。"

    def _recovery_steps(self, categories: list[str]) -> list[str]:
        steps = ["记录 run trace 和错误细节"]
        if "network" in categories:
            steps.extend(["尝试重新连接", "测试可用模型或网络服务"])
        if "file_write" in categories or "memory_write" in categories:
            steps.extend(["检查是否存在备份", "提供按时间线回退入口"])
        if "git" in categories:
            steps.extend(["读取当前 git 状态", "避免自动改写历史，必要时提示用户确认"])
        steps.append("多次恢复无效时显式提示用户")
        return steps
