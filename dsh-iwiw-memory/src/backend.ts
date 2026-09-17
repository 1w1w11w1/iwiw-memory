import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import type { RequestOptions } from "@modelcontextprotocol/sdk/shared/protocol.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

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

/** 区分「可重试的传输失败」与「业务结果/用户取消」。
 *  只有确认的连接类失败才允许断开重连；业务错误重试会重复收费。 */
function isTransportFailure(error: unknown): boolean {
  const msg = String((error as any)?.message ?? error);
  if (/abort|cancel/i.test(msg)) return false;
  return /(-32000|connection closed|ECONNRESET|EPIPE|not connected|transport|socket hang up)/i.test(msg);
}

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

  async callTool(
    name: string,
    args: Record<string, unknown>,
    options?: BackgroundCallOptions,
  ): Promise<string> {
    try {
      return await this._call(name, args, options);
    } catch (e) {
      // 只有确认的传输断开才重连重试：业务错误/超时/取消重试会重复收费或
      // 把用户取消当成失败重跑。子进程崩溃后 client 仍非 null，不重置会永久失败。
      if (!isTransportFailure(e)) throw e;
      await this.close().catch(() => {});
      return await this._call(name, args, options);
    }
  }

  private async _call(
    name: string,
    args: Record<string, unknown>,
    options?: BackgroundCallOptions,
  ): Promise<string> {
    await this.ensureConnected();
    const requestOptions: RequestOptions | undefined = options
      ? {
          signal: options.signal,
          timeout: options.timeoutMs ?? 60_000,
          resetTimeoutOnProgress: options.resetTimeoutOnProgress ?? true,
          maxTotalTimeout: options.maxTotalTimeoutMs,
          onprogress: options.onprogress,
        }
      : undefined;
    const result = await this.client!.callTool({ name, arguments: args }, undefined, requestOptions);
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
