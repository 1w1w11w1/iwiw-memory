import type { ContentBlock } from "@deepseek-ai/dsh-llm";
/** core 记忆（must-load）段名；正文由 apply 时拉取，避免启动抢 Python 资源。 */
export declare const MEMORY_SECTION_NAME = "iwiw-memory:core";
/** core 启动段：always-load 长期事实（身份、健康、关系、重大决策）。 */
export declare function coreSectionText(coreText: string): {
    name: string;
    order: number;
    text: string;
};
/** 工具使用提示（静态）：告诉模型有哪些 memory_* 工具以及何时该用。 */
export declare const TOOL_GUIDE_SECTION_NAME = "iwiw-memory:tools";
export declare const toolGuideSection: {
    name: string;
    order: number;
    text: string;
};
/** 抽取助手：把任意值转成可展示的 ContentBlock 列表。 */
export declare function toTextBlocks(value: unknown): ContentBlock[];
