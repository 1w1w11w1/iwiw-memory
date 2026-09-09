/** core 记忆（must-load）段名；正文由 apply 时拉取，避免启动抢 Python 资源。 */
export const MEMORY_SECTION_NAME = "iwiw-memory:core";
/** core 启动段：always-load 长期事实（身份、健康、关系、重大决策）。 */
export function coreSectionText(coreText) {
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
/** 工具使用提示（静态）：告诉模型有哪些 memory_* 工具以及何时该用。 */
export const TOOL_GUIDE_SECTION_NAME = "iwiw-memory:tools";
export const toolGuideSection = {
    name: TOOL_GUIDE_SECTION_NAME,
    order: 110,
    text: [
        "## 记忆工具",
        "",
        "你可以使用以下记忆工具（按需调用，无需用户要求）：",
        "- memory_remember(description, body, level?, slug?)：把值得长期保存的稳定信息写入记忆；写入即全文替换；新建前先 memory_search 查重。",
        "- memory_search(query, top_k=5)：按关键词检索相关长期事实。",
        "- memory_read(slug)：读取一条记忆的完整正文。",
        "- memory_list(priority?, mem_type?, limit=20)：列出记忆条目。",
        "",
        "level 取值：profile=用户身份画像/健康/偏好；fact=一般事实；lesson=教训与经验；rules=用户要求持续遵守的准则；project=项目脉络与决策。",
        "什么时候调用 memory_search：用户话题涉及个人事实、长期计划、健康、关系、偏好或历史决策时优先调。",
        "什么时候调用 memory_remember：出现身份画像（profile）、一般事实（fact）、教训（lesson）、用户要求持续遵守的准则（rules）、项目脉络（project），或用户明确要求记住/更正时。",
        "调用原则：用户的最新明示更正优先（如「其实我不喜欢 X」应理解为覆盖而非并列）；一次性、临时话题不要写。",
    ].join("\n"),
};
/** 抽取助手：把任意值转成可展示的 ContentBlock 列表。 */
export function toTextBlocks(value) {
    const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    return [{ type: "text", text }];
}
