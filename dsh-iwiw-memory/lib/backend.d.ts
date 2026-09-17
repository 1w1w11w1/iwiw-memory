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
    /** 覆盖子进程环境变量（如 MEMORY_AGENT_DB_PATH 隔离库）。
     *  注意：MCP SDK 默认不继承完整父进程 env（白名单机制），
     *  必须显式合并 process.env，否则 DB 路径等覆盖静默失效。 */
    private readonly env?;
    private client;
    private transport;
    constructor(command: string, args: string[], cwd: string, 
    /** 覆盖子进程环境变量（如 MEMORY_AGENT_DB_PATH 隔离库）。
     *  注意：MCP SDK 默认不继承完整父进程 env（白名单机制），
     *  必须显式合并 process.env，否则 DB 路径等覆盖静默失效。 */
    env?: Record<string, string> | undefined);
    ensureConnected(): Promise<void>;
    callTool(name: string, args: Record<string, unknown>, options?: BackgroundCallOptions): Promise<string>;
    private _call;
    close(): Promise<void>;
}
