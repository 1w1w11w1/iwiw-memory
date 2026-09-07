import type { ContentBlock } from "@deepseek-ai/dsh-llm";
type JsonValue = string | number | boolean | null | JsonValue[] | { [k: string]: JsonValue };
import { MemoryBackend } from "./backend.js";

/** 5 个 memory_* 工具：纯转给 Python memory_agent.mcp_server，结果转 JSON。
 *  模型既能主动调用（检索/维护），又能在 register 时声明给 harness 内部用。
 */
export class MemoryTools {
  constructor(private readonly backend: MemoryBackend) {}

  async search(params: { query: string; top_k?: number }): Promise<JsonValue> {
    return this.parse(await this.backend.callTool("search_memories", {
      query: params.query,
      top_k: params.top_k ?? 5,
    }));
  }

  async extract(params: { message: string; context?: string }): Promise<JsonValue> {
    return this.parse(await this.backend.callTool("extract_and_save", {
      message: params.message,
      context: params.context ?? "",
    }));
  }

  async list(params: { priority?: "core" | "normal" | "archive"; limit?: number }): Promise<JsonValue> {
    return this.parse(await this.backend.callTool("list_memories", {
      priority: params.priority,
      limit: params.limit ?? 20,
    }));
  }

  async update(params: { slug: string; description: string; body: string; priority?: string }): Promise<JsonValue> {
    return this.parse(await this.backend.callTool("memory_update", {
      slug: params.slug,
      description: params.description,
      body: params.body,
      priority: params.priority ?? "normal",
    }));
  }

  async stats(): Promise<JsonValue> {
    return this.parse(await this.backend.callTool("memory_stats", {}));
  }

  private parse(text: string): JsonValue {
    try { return JSON.parse(text) as JsonValue; } catch { return text; }
  }
}

/** 构造 model-facing 文本块，保留 MCP 原始 JSON。 */
export function textContent(value: JsonValue): ContentBlock[] {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return [{ type: "text", text }];
}
