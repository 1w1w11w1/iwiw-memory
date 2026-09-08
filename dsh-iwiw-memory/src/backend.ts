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
  ) {}

  async ensureConnected(): Promise<void> {
    if (this.client) return;
    this.transport = new StdioClientTransport({
      command: this.command,
      args: this.args,
      cwd: this.cwd,
    });
    this.client = new Client({ name: "dsh-iwiw-memory", version: "0.1.0" });
    await this.client.connect(this.transport);
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<string> {
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
