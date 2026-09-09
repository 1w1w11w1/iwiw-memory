import { Context } from "@deepseek-ai/cordis";
/** dsh-iwiw-memory：IwIw 记忆内核的 DSH 插件 —— 跨会话记忆（模型自主工具化写入）。 */
export declare const name = "dsh-iwiw-memory";
/** 必须显式声明 host 端用到的 cordis 服务，否则 ctx 访问器会抛 "cannot get property ... without inject"。 */
export declare const inject: string[];
interface PluginConfig {
    /** Python 解释器路径（含 mcp/jieba 依赖的 venv 或系统 Python）。 */
    python?: string;
    /** memory_agent 工作目录。 */
    cwd?: string;
    /** core 注入段总字符预算（与内核 MEMORY_RECALL_MAX_CHARS 对齐）。 */
    coreMaxChars?: number;
    /** 每消息命中注入的条数上限（默认 3）。 */
    hitTopK?: number;
    /** 常驻层集合（模式配置，类型轴保持纯净）：这些 mem_type 全量注入；
     *  rules 层单独包装为「准则」段。chat 模式默认 ["profile","rules"]，dev 模式建议 ["rules"]。 */
    standingLayers?: string[];
    /** reflect steering：连续 N 个模型步未写入记忆后注入一次性回顾提示；0 关闭（默认 7）。 */
    reflectTurns?: number;
    /** dream 空闲整理：空闲 N 分钟后触发（峰时抑制见 isPeakTime）；0 关闭（默认 180）。 */
    dreamIdleMinutes?: number;
    /** 覆盖 MCP 子进程环境变量（如 MEMORY_AGENT_DB_PATH 指向隔离库）。 */
    env?: Record<string, string>;
}
/** 峰时抑制（meow 同款）：9-12 / 14-18 及各自前 15 分钟不触发 dream。 */
export declare function isPeakTime(d: Date): boolean;
export declare const apply: (ctx: Context, config?: PluginConfig) => Promise<() => Promise<void>>;
export {};
