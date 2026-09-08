import { Context } from "@deepseek-ai/cordis";
/** dsh-iwiw-memory：IwIw 记忆内核的 DSH 插件 —— 跨会话记忆 + 自动学习。 */
export declare const name = "dsh-iwiw-memory";
interface PluginConfig {
    /** Python 解释器路径（含 mcp/jieba 依赖的 venv 或系统 Python）。 */
    python?: string;
    /** memory_agent 工作目录。 */
    cwd?: string;
    /** 启动时是否拉取 core 段（默认 true）。 */
    injectCore?: boolean;
}
export declare const apply: (ctx: Context, config?: PluginConfig) => Promise<() => Promise<void>>;
export {};
