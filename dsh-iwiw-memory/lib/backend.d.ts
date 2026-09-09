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
    /** 内核连接异常回调（callTool 重试仍失败时触发；宿主用于系统通知）。 */
    private readonly onError?;
    private client;
    private transport;
    constructor(command: string, args: string[], cwd: string, 
    /** 覆盖子进程环境变量（如 MEMORY_AGENT_DB_PATH 隔离库）。
     *  注意：MCP SDK 默认不继承完整父进程 env（白名单机制），
     *  必须显式合并 process.env，否则 DB 路径等覆盖静默失效。 */
    env?: Record<string, string> | undefined, 
    /** 内核连接异常回调（callTool 重试仍失败时触发；宿主用于系统通知）。 */
    onError?: ((e: unknown) => void) | undefined);
    ensureConnected(): Promise<void>;
    callTool(name: string, args: Record<string, unknown>): Promise<string>;
    private _call;
    close(): Promise<void>;
}
