import type { ContentBlock } from "@deepseek-ai/dsh-llm";
type JsonValue = string | number | boolean | null | JsonValue[] | { [k: string]: JsonValue };
import { MemoryBackend, type BackgroundCallOptions } from "./backend.js";

/** consolidate 后台调用的资源上限：单次请求 200s，整体 30 分钟。
 *
 *  数值依据（实测，非估算）：
 *  - 单批 LLM 耗时与批大小无关：5/10/20 条分别 23.7 / 130.4 / 44.4 秒（方差 5.5 倍），
 *    是服务端波动。内核侧每批超时默认 180s，客户端须大于它，
 *    否则内核还没判超时、客户端先放弃，错误归因落到错误的层。
 *  - progress 只在每批完成后上报，首批完成前没有重置机会，所以单次上限必须
 *    覆盖最坏单批（130.4s），不能只覆盖均值。
 *  - 整体上限按最坏情况算：129 条候选 / 每批 20 ≈ 7 批 × 180s ≈ 21 分钟，取 30 分钟。
 *  超过此规模需要另加批次游标，不靠无限加大 timeout。 */
const CONSOLIDATE_CALL: BackgroundCallOptions = {
  timeoutMs: 200_000,
  resetTimeoutOnProgress: true,
  maxTotalTimeoutMs: 30 * 60_000,
  onprogress: () => {},
};

export interface ConsolidateOutcome {
  ok: boolean;
  error?: string;
  reviewed?: number;
  changedRows?: number;
  raw?: JsonValue;
}

/** 严格判定 consolidate 结果：MCP 返回字符串、{error:...}、complete=false
 *  或结构缺失都不算成功——否则会向上冒充「已补账」并推进水位。 */
export function consolidateOutcome(value: JsonValue): ConsolidateOutcome {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return { ok: false, error: `unexpected consolidate payload: ${typeof value}`, raw: value };
  }
  const d = value as Record<string, any>;
  if (d.error) return { ok: false, error: String(d.error), raw: value };
  if (Array.isArray(d.errors) && d.errors.length > 0) {
    return { ok: false, error: String(d.errors[0]), raw: value };
  }
  if (d.complete !== true) {
    return { ok: false, error: "consolidate did not report complete", raw: value };
  }
  const changedRows = (Array.isArray(d.auto_fixed) ? d.auto_fixed.length : 0)
    + (Array.isArray(d.pending) ? d.pending.length : 0);
  return { ok: true, reviewed: Number(d.reviewed ?? 0), changedRows, raw: value };
}

/** 4 个 memory_* 工具：桥接到 Python memory_agent.mcp_server，
 *  与 CLI chat 的模型工具面同源（执行逻辑单源在内核 model_tools.py）。
 */
export class MemoryTools {
  constructor(private readonly backend: MemoryBackend) {}

  async remember(params: {
    description: string; body: string; level?: string; slug?: string; priority?: string; project?: string;
  }): Promise<JsonValue> {
    const args: Record<string, unknown> = { description: params.description, body: params.body };
    if (params.level) args.level = params.level;
    if (params.slug) args.slug = params.slug;
    if (params.priority) args.priority = params.priority;
    // 写入即打项目标识：project 型记忆的检索隔离完全依赖它（见 model_tools 的 metadata 写入）
    if (params.project) args.project = params.project;
    return this.parse(await this.backend.callTool("memory_remember", args));
  }

  async search(params: {
    query: string; top_k?: number;
    session_id?: string; context?: string[]; exclude_mem_types?: string[];
    project?: string;
  }): Promise<JsonValue> {
    const args: Record<string, unknown> = { query: params.query, top_k: params.top_k ?? 5 };
    if (params.session_id) args.session_id = params.session_id;
    if (params.context?.length) args.context = params.context;
    if (params.exclude_mem_types?.length) args.exclude_mem_types = params.exclude_mem_types;
    if (params.project) args.project = params.project;
    return this.parse(await this.backend.callTool("search_memories", args));
  }

  /** 命中自增（使用强化）：记忆被实际注入时调用，供维护排序。 */
  async touch(slugs: string[]): Promise<JsonValue> {
    if (!slugs.length) return { ok: true, touched: 0 };
    return this.parse(await this.backend.callTool("touch_memories", { slugs }));
  }

  /** 记忆巩固（consolidate）：低风险修正自动留痕执行，归档走审批。
   *  给了窗口（sinceMs/untilMs）就是启动补账模式：limit 是每批大小，
   *  窗口内候选完整分批，不静默丢候选。 */
  async consolidate(
    options: { sinceHours?: number; limit?: number; sinceMs?: number; untilMs?: number } = {},
    callOptions?: BackgroundCallOptions,
  ): Promise<JsonValue> {
    const args: Record<string, unknown> = {};
    if (options.sinceHours !== undefined) args.since_hours = options.sinceHours;
    if (options.limit !== undefined) args.limit = options.limit;
    if (options.sinceMs !== undefined) args.since_ms = options.sinceMs;
    if (options.untilMs !== undefined) args.until_ms = options.untilMs;
    return this.parse(await this.backend.callTool("run_consolidate", args, callOptions));
  }

  /** 启动补账专用：固定窗口 + 严格结果判定。 */
  async consolidateWindow(sinceMs: number, untilMs: number, signal?: AbortSignal): Promise<ConsolidateOutcome> {
    try {
      const raw = await this.consolidate(
        { sinceMs, untilMs, limit: 20 },
        { ...CONSOLIDATE_CALL, signal },
      );
      return consolidateOutcome(raw);
    } catch (error) {
      return { ok: false, error: String(error) };
    }
  }

  /** 目标库标识（后台 domain 名称与跨进程锁 key 用）。旧后端没有该字段。 */
  async databaseId(): Promise<string | null> {
    try {
      const stats = await this.backend.callTool("memory_stats", {});
      const parsed = this.parse(stats);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const id = (parsed as Record<string, unknown>).database_id;
        if (typeof id === "string" && id.length > 0) return id;
      }
    } catch {
      // 后端不可用：由调用方停用后台能力，不冒险用错误的锁 key。
    }
    return null;
  }

  async read(params: { slug: string }): Promise<JsonValue> {
    return this.parse(await this.backend.callTool("read_memory", params));
  }

  async list(params: { priority?: string; mem_type?: string; limit?: number }): Promise<JsonValue> {
    const args: Record<string, unknown> = { limit: params.limit ?? 20 };
    if (params.priority) args.priority = params.priority;
    if (params.mem_type) args.mem_type = params.mem_type;
    return this.parse(await this.backend.callTool("list_memories", args));
  }

  private parse(text: string): JsonValue {
    try { return JSON.parse(text) as JsonValue; } catch { return text; }
  }
}

/** 构造 model-facing 文本块，保留 MCP 原始 JSON。 */
export function toTextBlocks(value: unknown): ContentBlock[] {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return [{ type: "text", text }];
}
