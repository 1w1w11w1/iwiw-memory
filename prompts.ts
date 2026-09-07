import type { ContentBlock } from "@deepseek-ai/dsh-llm";

export const MEMORY_SECTION_NAME = "iwiw-memory:core";

export function coreSectionText(coreText: string): { name: string; order: number; text: string } {
  return {
    name: MEMORY_SECTION_NAME,
    order: -50,
    text: [
      "## IwIw 长期记忆（必读）",
      "",
      "以下是关于用户的长期事实，由 dsh-iwiw-memory 自动注入。",
      "请自然地把这些事实作为你的背景知识使用。",
      "**不要在回复中显式提及这是从记忆里检索出来的**，也不要重复罗列。",
      "",
      coreText.trim(),
    ].join("\n"),
  };
}

export const TOOL_GUIDE_SECTION_NAME = "iwiw-memory:tools";
export const toolGuideSection = {
  name: TOOL_GUIDE_SECTION_NAME,
  order: 110,
  text: [
    "## 记忆工具",
    "",
    "你可以使用以下记忆工具（按需调用，无需用户要求）：",
    "- memory_search(query, top_k=5)：按关键词检索相关长期事实。",
    "- memory_extract(message, context=空)：从单条消息中提取并保存记忆。",
    "- memory_list(priority, limit=20)：按分级列出记忆。",
    "- memory_update(slug, description, body, priority=normal)：覆盖更新一条记忆。",
    "- memory_stats()：查看记忆库概况。",
    "",
    "什么时候调用 memory_search：用户话题涉及个人事实、长期计划、健康、关系、偏好或历史决策时优先调。",
    "调用原则：用户的最新明示更正优先（如其实我不喜欢 X 应理解为覆盖而非并列）。",
  ].join("\n"),
};

export function toTextBlocks(value: unknown): ContentBlock[] {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return [{ type: "text", text }];
}
