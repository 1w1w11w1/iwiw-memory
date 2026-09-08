import { Context } from "@deepseek-ai/cordis";
/** dsh-iwiw-memory：IwIw 记忆内核的 DSH 插件 —— 跨会话记忆（模型自主工具化写入）。 */
export declare const name = "dsh-iwiw-memory";
interface PluginConfig {
    /** Python 解释器路径（含 mcp/jieba 依赖的 venv 或系统 Python）。 */
    python?: string;
    /** memory_agent 工作目录。 */
    cwd?: string;
    /** core 注入段总字符预算（与内核 MEMORY_RECALL_MAX_CHARS 对齐）。 */
    coreMaxChars?: number;
}
export declare const apply: (ctx: Context, config?: PluginConfig) => Promise<() => Promise<void>>;
export {};
