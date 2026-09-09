import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

/**
 * 记忆内核后端连接：惰性 spawn Python memory_agent.mcp_server，
 * 通过 MCP stdio 调用（检索/提取/维护等）。单例复用。
 */
export class MemoryBackend {
  private client: Client | null = null;
  private transport: StdioClientTransport | null = null;

  constructor(
    private readonly command: string,
    private readonly args: string[],
    private readonly cwd: string,
    /** 覆盖子进程环境变量（如 MEMORY_AGENT_DB_PATH 隔离库）。
     *  注意：MCP SDK 默认不继承完整父进程 env（白名单机制），
     *  必须显式合并 process.env，否则 DB 路径等覆盖静默失效。 */
    private readonly env?: Record<string, string>,
    /** 内核连接异常回调（callTool 重试仍失败时触发；宿主用于系统通知）。 */
    private readonly onError?: (e: unknown) => void,
  ) {}

  async ensureConnected(): Promise<void> {
    if (this.client) return;
    this.transport = new StdioClientTransport({
      command: this.command,
      args: this.args,
      cwd: this.cwd,
      env: { ...(process.env as Record<string, string>), ...(this.env ?? {}) },
    });
    this.client = new Client({ name: "dsh-iwiw-memory", version: "0.1.0" });
    await this.client.connect(this.transport);
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<string> {
    try {
      return await this._call(name, args);
    } catch (e) {
      // 子进程崩溃/管道断开后 client 仍非 null，不重置会永久失败——
      // 重置连接重试一次；重试仍失败则向上抛（调用方按工具错误处理）。
      await this.close().catch(() => {});
      try {
        return await this._call(name, args);
      } catch (retry) {
        // 重试仍失败 = 内核连接异常：回调宿主（系统通知用），原始错误继续上抛
        this.onError?.(retry);
        throw retry;
      }
    }
  }

  private async _call(name: string, args: Record<string, unknown>): Promise<string> {
    await this.ensureConnected();
    const result = await this.client!.callTool({ name, arguments: args });
    // content 可能是 text 块或带 format 的块；宽容提取文本
    const blocks = (result.content ?? []) as Array<{ type?: string; text?: string }>;
    const text = blocks.filter((b) => typeof b.text === "string").map((b) => b.text as string).join("\n");
    return text;
  }

  async close(): Promise<void> {
    if (this.transport) await this.transport.close();
    this.client = null;
    this.transport = null;
  }
}
