import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

export interface Runtime {
  python: string;
  cwd: string;
}

/** 包内内核目录（build 时从仓库根 memory_agent/ 复制到 python/）。 */
const bundledCwd = join(dirname(fileURLToPath(import.meta.url)), "..", "python");

/** 依赖探测：目标解释器能否 import mcp 与 jieba。 */
function depsOk(python: string, cwd: string): boolean {
  const r = spawnSync(python, ["-c", "import mcp, jieba"], { cwd, encoding: "utf8", timeout: 60_000 });
  return r.status === 0;
}

/**
 * 解析内核运行时（发布自举，官方 Config 范式）：
 * 1. cwd 缺省 → 包内自带内核（python/，build 时复制）；
 * 2. 目标解释器缺依赖（mcp/jieba）→ 在 <DSH_HOME>/iwiw-memory-venv 建专用 venv
 *    并安装包内 requirements（一次性，之后复用）。
 */
export function ensureRuntime(opts: { python?: string; cwd?: string }): Runtime {
  const cwd = opts.cwd?.trim() || bundledCwd;
  let python = opts.python?.trim() || "python";
  if (!existsSync(cwd)) throw new Error(`[dsh-iwiw-memory] 内核目录不存在: ${cwd}`);
  if (depsOk(python, cwd)) return { python, cwd };

  const dshHome = process.env.DSH_HOME || join(homedir(), ".dsh");
  const venvDir = join(dshHome, "iwiw-memory-venv");
  const venvPython = process.platform === "win32" ? join(venvDir, "Scripts", "python.exe") : join(venvDir, "bin", "python");
  if (!existsSync(venvPython)) {
    console.info("[dsh-iwiw-memory] 首次运行：正在创建内核专用虚拟环境（一次性，约 10 秒）...");
    const v = spawnSync(python, ["-m", "venv", venvDir], { encoding: "utf8", timeout: 120_000 });
    if (v.status !== 0) throw new Error(`[dsh-iwiw-memory] venv 创建失败: ${v.stderr?.slice(0, 300)}`);
  }
  if (!depsOk(venvPython, cwd)) {
    console.info("[dsh-iwiw-memory] 正在安装内核依赖（mcp/jieba，一次性，可能需要几分钟）...");
    // --no-cache-dir：自举不依赖用户 pip 缓存的健康状态（缓存文件权限损坏会 Errno 13）
    const p = spawnSync(
      venvPython,
      ["-m", "pip", "install", "--no-cache-dir", "-r", join(cwd, "requirements.txt")],
      { encoding: "utf8", timeout: 600_000 },
    );
    if (p.status !== 0) {
      const detail = ((p.stderr ?? "") + "\n" + (p.stdout ?? "")).trim().slice(-400);
      throw new Error(`[dsh-iwiw-memory] 内核依赖安装失败:\n${detail}`);
    }
  }
  if (!depsOk(venvPython, cwd)) throw new Error("[dsh-iwiw-memory] 内核依赖自举失败（venv 内仍缺 mcp/jieba）");
  return { python: venvPython, cwd };
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
