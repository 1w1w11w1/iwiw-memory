/** 4 个 memory_* 工具：桥接到 Python memory_agent.mcp_server，
 *  与 CLI chat 的模型工具面同源（执行逻辑单源在内核 model_tools.py）。
 */
export class MemoryTools {
    backend;
    constructor(backend) {
        this.backend = backend;
    }
    async remember(params) {
        const args = { description: params.description, body: params.body };
        if (params.level)
            args.level = params.level;
        if (params.slug)
            args.slug = params.slug;
        if (params.priority)
            args.priority = params.priority;
        return this.parse(await this.backend.callTool("memory_remember", args));
    }
    async search(params) {
        const args = { query: params.query, top_k: params.top_k ?? 5 };
        if (params.session_id)
            args.session_id = params.session_id;
        if (params.context?.length)
            args.context = params.context;
        if (params.exclude_mem_types?.length)
            args.exclude_mem_types = params.exclude_mem_types;
        return this.parse(await this.backend.callTool("search_memories", args));
    }
    /** 命中自增（使用强化）：记忆被实际注入时调用，供维护排序。 */
    async touch(slugs) {
        if (!slugs.length)
            return { ok: true, touched: 0 };
        return this.parse(await this.backend.callTool("touch_memories", { slugs }));
    }
    /** 记忆巩固（consolidate）：低风险修正自动留痕执行，归档走审批。 */
    async consolidate(sinceHours, limit) {
        const args = {};
        if (sinceHours !== undefined)
            args.since_hours = sinceHours;
        if (limit !== undefined)
            args.limit = limit;
        return this.parse(await this.backend.callTool("run_consolidate", args));
    }
    async read(params) {
        return this.parse(await this.backend.callTool("read_memory", params));
    }
    async list(params) {
        const args = { limit: params.limit ?? 20 };
        if (params.priority)
            args.priority = params.priority;
        if (params.mem_type)
            args.mem_type = params.mem_type;
        return this.parse(await this.backend.callTool("list_memories", args));
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
export function toTextBlocks(value) {
    const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    return [{ type: "text", text }];
}
