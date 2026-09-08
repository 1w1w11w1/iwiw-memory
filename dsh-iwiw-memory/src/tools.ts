import type { ContentBlock } from "@deepseek-ai/dsh-llm";
type JsonValue = string | number | boolean | null | JsonValue[] | { [k: string]: JsonValue };
import { MemoryBackend } from "./backend.js";

/** 4 个 memory_* 工具：桥接到 Python memory_agent.mcp_server，
 *  与 CLI chat 的模型工具面同源（执行逻辑单源在内核 model_tools.py）。
 */
export class MemoryTools {
  constructor(private readonly backend: MemoryBackend) {}

  async remember(params: { description: string; body: string; level?: string; slug?: string; priority?: string }): Promise<JsonValue> {
    const args: Record<string, unknown> = { description: params.description, body: params.body };
    if (params.level) args.level = params.level;
    if (params.slug) args.slug = params.slug;
    if (params.priority) args.priority = params.priority;
    return this.parse(await this.backend.callTool("memory_remember", args));
  }

  async search(params: { query: string; top_k?: number }): Promise<JsonValue> {
    return this.parse(await this.backend.callTool("search_memories", {
      query: params.query,
      top_k: params.top_k ?? 5,
    }));
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
