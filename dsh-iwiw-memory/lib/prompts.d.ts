import type { ContentBlock } from "@deepseek-ai/dsh-llm";
/** core 记忆（must-load）段名；正文由 apply 时拉取，避免启动抢 Python 资源。 */
export declare const MEMORY_SECTION_NAME = "iwiw-memory:core";
/** rules 准则段名。 */
export declare const RULES_SECTION_NAME = "iwiw-memory:rules";
/**
 * 剥离只对**检索**有意义的字段（注入视图专用，不动库里的正文）。
 *
 * `关键词: …` 行是 FTS 同义词扩展的锚点，只在按话题召回时起作用。
 * 常驻层是全量无条件注入、永不经过 FTS 的，这行纯属空转——实测占常驻注入 17.6%。
 * 剥离只发生在拼进 prompt 之前；memory_read / memory_search 取回的仍是完整正文。
 *
 * 规则（确定性，无 LLM）：
 *   - 独立成行的 `关键词: …` 整行删除；
 *   - `关键词: …` 粘在最后一个非空行尾部时，从该处截断（实测全库 1 例）。
 * 正文中间顺带提到「关键词」的句子不受影响。
 */
export declare function stripRetrievalFields(text: string): string;
/**
 * iwiw 注入的全部 system prompt 段 —— 唯一构造源。
 * 注册（index.ts）与 /iwiw-prompt 回显（capture.ts）共用同一份，两者不会漂移。
 * 取值惰性：每次调用读当下的 core/rules 缓存。
 */
export declare function iwiwSections(get: {
    core: () => string;
    rules: () => string;
}): Array<{
    name: string;
    order: number;
    text: () => string;
}>;
/** 工具使用提示（静态）：告诉模型有哪些 memory_* 工具以及何时该用。 */
export declare const TOOL_GUIDE_SECTION_NAME = "iwiw-memory:tools";
export declare const toolGuideSection: {
    name: string;
    order: number;
    text: string;
};
/** 抽取助手：把任意值转成可展示的 ContentBlock 列表。 */
export declare function toTextBlocks(value: unknown): ContentBlock[];
