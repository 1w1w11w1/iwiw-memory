/**
 * capture.ts — 跨项目「想法带回」管道（纯开发者工具，可整块剥离）。
 *
 * 两个入口，都只服务一个场景：在别的项目开发时冒出优化想法，
 * 把它连同当时的会话现场带回本项目（E:/desktop/111）讨论。
 *
 *   入口 A \`/memo <想法>\` —— 在别的项目会话里跑。
 *     读当前会话明文 JSONL + 这句话，落 debug-inbox/ 顶层。
 *     比 DSH 原生 /export 少两步：不经浏览器下载目录、不需手工搬运。
 *
 *   入口 B \`/recall\` —— 在本项目会话里跑。
 *     取 inbox 最新 memo，经 memory_agent.debug_bundle 解析成逐轮事实链回灌上下文。
 *     只搬现场，不自动写记忆（是否值得长期保存由人在对话里判断，走既有 memory_remember）。
 *
 * 迁移/移除：删除本文件 + index.ts 里两行挂载 + package.json 的 peerDependency 即可，
 * 内核 memory_agent/ 零改动，无环境变量、无配置项、无 DB schema 变更。
 */
interface CaptureConfig {
    /** 含 mcp/jieba 依赖的 Python 解释器（与 index.ts 的 config.python 同源）。 */
    python: string;
    /** 当前 DSH profile 目录（用于解析 DSH 内部包）。 */
    profileDir: string;
}
/**
 * 注册 /memo 与 /recall。
 * @returns disposer 列表（调用方负责统一释放）。
 */
export declare function registerCaptureCommands(ctx: any, config: CaptureConfig): Array<() => void>;
export {};
