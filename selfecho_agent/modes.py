from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IntentDecision:
    intent: str
    path: str
    reason: str


class IntentRouter:
    """Small deterministic router before richer model-based routing exists."""

    SAFETY_TERMS = ("不活了", "自杀", "轻生", "死了算了", "撑不下去")
    MEMORY_TERMS = ("记忆", "整理上下文", "保存下来", "删除记忆", "合并记忆", "归档")
    PLAN_TERMS = ("计划", "方案", "怎么推进", "架构", "设计", "拆解", "流程")
    EXECUTION_TERMS = ("运行", "执行", "修改", "创建", "删除", "测试", "提交", "打开", "读取", "文件")
    PROJECT_TERMS = ("工作目录", "项目目录", "这个目录", "能看到什么", "文件夹", "代码", "仓库", "代码库")
    LOCAL_SEARCH_TERMS = ("搜索", "查找", "找一下", "找找", "grep", "rg")
    LOCAL_SEARCH_SCOPE_TERMS = PROJECT_TERMS + ("项目",)
    SUPPORT_TERMS = ("难受", "焦虑", "烦", "痛苦", "想哭", "陪我想想", "不知道怎么办")

    def classify(self, message: str) -> IntentDecision:
        text = message.strip().lower()
        if any(term in text for term in self.SAFETY_TERMS):
            return IntentDecision("safety", "safety", "检测到高情绪或安全风险表达")
        if any(term in text for term in self.MEMORY_TERMS):
            return IntentDecision("memory", "deliberate", "用户正在请求记忆或上下文维护")
        if any(term in text for term in self.LOCAL_SEARCH_TERMS) and any(term in text for term in self.LOCAL_SEARCH_SCOPE_TERMS):
            return IntentDecision("execution", "deliberate", "用户请求在当前项目中进行只读检索")
        if any(term in text for term in self.EXECUTION_TERMS + self.PROJECT_TERMS):
            return IntentDecision("execution", "deliberate", "用户的问题需要项目定位或可能涉及工具动作")
        if any(term in text for term in self.PLAN_TERMS):
            return IntentDecision("planning", "deliberate", "用户正在请求结构化方案或推进路径")
        if any(term in text for term in self.SUPPORT_TERMS):
            return IntentDecision("support", "light", "用户更像是在表达状态，需要先承接和整理")
        return IntentDecision("analysis", "light", "默认进行轻量分析与对话")
