import { Context } from "@deepseek-ai/cordis";
import Schema from "@deepseek-ai/schemastery";
/** dsh-iwiw-memory：IwIw 记忆内核的 DSH 插件 —— 跨会话记忆（模型自主工具化写入）。 */
export declare const name = "dsh-iwiw-memory";
/** 必须显式声明 host 端用到的 cordis 服务，否则 ctx 访问器会抛 "cannot get property ... without inject"。 */
export declare const inject: string[];
interface PluginConfig {
    /** Python 解释器路径（含 mcp/jieba 依赖的 venv 或系统 Python）。 */
    python?: string;
    /** memory_agent 工作目录。 */
    cwd?: string;
    /** 常驻层注入段总字符预算（与内核 MEMORY_RECALL_MAX_CHARS 对齐）。 */
    coreMaxChars?: number;
    /** 每消息命中注入的条数上限（默认 3）。 */
    hitTopK?: number;
    /** 常驻层集合（模式配置，类型轴保持纯净）：这些 mem_type 全量注入；
     *  rules 层单独包装为「准则」段。chat 模式默认 ["profile","rules"]，dev 模式建议 ["rules"]。 */
    standingLayers?: string[];
    /** reflect steering：连续 N 个模型步未写入记忆后注入一次性回顾提示；0 关闭（默认 7）。 */
    reflectTurns?: number;
    /** 记忆巩固（consolidate）：空闲 N 分钟后触发（峰时抑制见 isPeakTime）；0 关闭（默认 180）。 */
    consolidateIdleMinutes?: number;
    /** 覆盖 MCP 子进程环境变量（如 MEMORY_AGENT_DB_PATH 指向隔离库）。 */
    env?: Record<string, string>;
}
/** 峰时抑制：9-12 / 14-18 及各自前 15 分钟不触发巩固（避免打扰活跃时段）。 */
export declare function isPeakTime(d: Date): boolean;
/** settings schema（schemastery 对象）：设置通道要求 schema 可 JSON 序列化——
 *  host describe 时序列化信封，渲染端 rehydrate+validate（纯函数会静默产出空镜像）。 */
export declare const SETTINGS_SCHEMA: Schema<Schemastery.ObjectS<{
    hitTopK: Schema<number, number>;
    coreMaxChars: Schema<number, number>;
    reflectTurns: Schema<number, number>;
    consolidateIdleMinutes: Schema<number, number>;
    standingLayers: Schema<string, string>;
}>, Schemastery.ObjectT<{
    hitTopK: Schema<number, number>;
    coreMaxChars: Schema<number, number>;
    reflectTurns: Schema<number, number>;
    consolidateIdleMinutes: Schema<number, number>;
    standingLayers: Schema<string, string>;
}>>;
export declare const apply: (ctx: Context, config?: PluginConfig) => Promise<() => Promise<void>>;
export {};
