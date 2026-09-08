/** 5 个 memory_* 工具：纯转给 Python memory_agent.mcp_server，结果转 JSON。
 *  模型既能主动调用（检索/维护），又能在 register 时声明给 harness 内部用。
 */
export class MemoryTools {
    backend;
    constructor(backend) {
        this.backend = backend;
    }
    async search(params) {
        return this.parse(await this.backend.callTool("search_memories", {
            query: params.query,
            top_k: params.top_k ?? 5,
        }));
    }
    async extract(params) {
        return this.parse(await this.backend.callTool("extract_and_save", {
            message: params.message,
            context: params.context ?? "",
        }));
    }
    async list(params) {
        return this.parse(await this.backend.callTool("list_memories", {
            priority: params.priority,
            limit: params.limit ?? 20,
        }));
    }
    async update(params) {
        return this.parse(await this.backend.callTool("memory_update", {
            slug: params.slug,
            description: params.description,
            body: params.body,
            priority: params.priority ?? "normal",
        }));
    }
    async stats() {
        return this.parse(await this.backend.callTool("memory_stats", {}));
    }
    parse(text) {
        try {
            return JSON.parse(text);
        }
        catch {
            return text;
        }
    }
}
/** 构造 model-facing 文本块，保留 MCP 原始 JSON。 */
export function textContent(value) {
    const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    return [{ type: "text", text }];
}
