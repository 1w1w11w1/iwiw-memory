/**
 * capture.ts — 插件自有的开发者工具命令（纯开发者工具，可整块剥离）。
 *
 * 三个入口，都只读现场、不改记忆：
 *
 *   入口 A \`/memo <想法>\` —— 在别的项目会话里跑。
 *     读当前会话明文 JSONL + 这句话，落 debug-inbox/ 顶层。
 *     比 DSH 原生 /export 少两步：不经浏览器下载目录、不需手工搬运。
 *
 *   入口 B \`/recall\` —— 在本项目会话里跑。
 *     取 inbox 最新 memo，经 memory_agent.debug_bundle 解析成逐轮事实链回灌上下文。
 *     只搬现场，不自动写记忆（是否值得长期保存由人在对话里判断，走既有 memory_remember）。
 *
 *   入口 C \`/iwiw-prompt\` —— 回显本插件此刻注入的 system prompt 段全文，
 *     用于核对注入内容。段文本取自 prompts.ts 的唯一构造源，与注册同源。
 *
 * 迁移/移除：删除本文件 + index.ts 里两行挂载 + package.json 的 peerDependency 即可，
 * 内核 memory_agent/ 零改动，无环境变量、无 DB schema 变更。
 */
interface CaptureConfig {
    /** 含 mcp/jieba 依赖的 Python 解释器（与 index.ts 的 config.python 同源）。 */
    python: string;
    /** 当前 DSH profile 目录（用于解析 DSH 内部包）。 */
    profileDir: string;
    /** 读取当前注入段的正文（index.ts 的 core/rules 缓存）。 */
    promptText: {
        core: () => string;
        rules: () => string;
    };
}
/**
 * 注册 /memo 与 /recall。
 * @returns disposer 列表（调用方负责统一释放）。
 */
export declare function registerCaptureCommands(ctx: any, config: CaptureConfig): Array<() => void>;
export {};
