/** 后台调用的超时/进度策略。MCP JS SDK 默认请求超时 60s：
 *  跨多批的 consolidate 不能沿用默认值，也不能把业务超时当成管道断开去重试整个任务。 */
export interface BackgroundCallOptions {
    signal?: AbortSignal;
    /** 单次请求超时（每批 LLM 都会发进度，可借此保活）。 */
    timeoutMs?: number;
    /** 进度通知到达即重置单次超时。 */
    resetTimeoutOnProgress?: boolean;
    /** 整个调用的硬上限。 */
    maxTotalTimeoutMs?: number;
    onprogress?: () => void;
}
/**
 * 记忆内核后端连接：惰性 spawn Python memory_agent.mcp_server，
 * 通过 MCP stdio 调用（检索/提取/维护等）。单例复用。
 */
export declare class MemoryBackend {
    private readonly command;
    private readonly args;
    private readonly cwd;
    private client;
    private transport;
    private env;
    /** 连接建立中的 Promise：并发调用不能各 spawn 一个子进程。 */
    private connecting;
    /** 连接代次：close() 自增，用来识别「调用期间被重连过」。 */
    private epoch;
    constructor(command: string, args: string[], cwd: string, 
    /** 覆盖子进程环境变量（如 MEMORY_AGENT_DB_PATH 隔离库）。
     *  注意：MCP SDK 默认不继承完整父进程 env（白名单机制），
     *  必须显式合并 process.env，否则 DB 路径等覆盖静默失效。 */
    env?: Record<string, string>);
    /**
     * 替换子进程环境变量。**不自动重连**——已连接的 client 仍用旧环境，
     * 调用方需在改完后 close()，让下一次 callTool 用新环境重新 spawn。
     * 这样「设置页改模型」不需要重启 DSH。
     */
    setEnv(env: Record<string, string>): void;
    ensureConnected(): Promise<void>;
    callTool(name: string, args: Record<string, unknown>, options?: BackgroundCallOptions): Promise<string>;
    private _call;
    close(): Promise<void>;
}
