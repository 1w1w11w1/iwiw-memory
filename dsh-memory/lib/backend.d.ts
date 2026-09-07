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
    constructor(command: string, args: string[], cwd: string);
    ensureConnected(): Promise<void>;
    callTool(name: string, args: Record<string, unknown>): Promise<string>;
    close(): Promise<void>;
}
