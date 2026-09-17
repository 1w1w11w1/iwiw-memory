import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
/** 区分「可重试的传输失败」与「业务结果/用户取消」。
 *  只有确认的连接类失败才允许断开重连；业务错误重试会重复收费。 */
function isTransportFailure(error) {
    const msg = String(error?.message ?? error);
    if (/abort|cancel/i.test(msg))
        return false;
    return /(-32000|connection closed|ECONNRESET|EPIPE|not connected|transport|socket hang up)/i.test(msg);
}
/**
 * 记忆内核后端连接：惰性 spawn Python memory_agent.mcp_server，
 * 通过 MCP stdio 调用（检索/提取/维护等）。单例复用。
 */
export class MemoryBackend {
    command;
    args;
    cwd;
    env;
    client = null;
    transport = null;
    constructor(command, args, cwd, 
    /** 覆盖子进程环境变量（如 MEMORY_AGENT_DB_PATH 隔离库）。
     *  注意：MCP SDK 默认不继承完整父进程 env（白名单机制），
     *  必须显式合并 process.env，否则 DB 路径等覆盖静默失效。 */
    env) {
        this.command = command;
        this.args = args;
        this.cwd = cwd;
        this.env = env;
    }
    async ensureConnected() {
        if (this.client)
            return;
        this.transport = new StdioClientTransport({
            command: this.command,
            args: this.args,
            cwd: this.cwd,
            env: { ...process.env, ...(this.env ?? {}) },
        });
        this.client = new Client({ name: "dsh-iwiw-memory", version: "0.1.0" });
        await this.client.connect(this.transport);
    }
    async callTool(name, args, options) {
        try {
            return await this._call(name, args, options);
        }
        catch (e) {
            // 只有确认的传输断开才重连重试：业务错误/超时/取消重试会重复收费或
            // 把用户取消当成失败重跑。子进程崩溃后 client 仍非 null，不重置会永久失败。
            if (!isTransportFailure(e))
                throw e;
            await this.close().catch(() => { });
            return await this._call(name, args, options);
        }
    }
    async _call(name, args, options) {
        await this.ensureConnected();
        const requestOptions = options
            ? {
                signal: options.signal,
                timeout: options.timeoutMs ?? 60_000,
                resetTimeoutOnProgress: options.resetTimeoutOnProgress ?? true,
                maxTotalTimeout: options.maxTotalTimeoutMs,
                onprogress: options.onprogress,
            }
            : undefined;
        const result = await this.client.callTool({ name, arguments: args }, undefined, requestOptions);
        // content 可能是 text 块或带 format 的块；宽容提取文本
        const blocks = (result.content ?? []);
        const text = blocks.filter((b) => typeof b.text === "string").map((b) => b.text).join("\n");
        return text;
    }
    async close() {
        if (this.transport)
            await this.transport.close();
        this.client = null;
        this.transport = null;
    }
}
