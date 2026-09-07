import type { ContentBlock } from "@deepseek-ai/dsh-llm";
type JsonValue = string | number | boolean | null | JsonValue[] | {
    [k: string]: JsonValue;
};
import { MemoryBackend } from "./backend.js";
/** 5 个 memory_* 工具：纯转给 Python memory_agent.mcp_server，结果转 JSON。
 *  模型既能主动调用（检索/维护），又能在 register 时声明给 harness 内部用。
 */
export declare class MemoryTools {
    private readonly backend;
    constructor(backend: MemoryBackend);
    search(params: {
        query: string;
        top_k?: number;
    }): Promise<JsonValue>;
    extract(params: {
        message: string;
        context?: string;
    }): Promise<JsonValue>;
    list(params: {
        priority?: "core" | "normal" | "archive";
        limit?: number;
    }): Promise<JsonValue>;
    update(params: {
        slug: string;
        description: string;
        body: string;
        priority?: string;
    }): Promise<JsonValue>;
    stats(): Promise<JsonValue>;
    private parse;
}
/** 构造 model-facing 文本块，保留 MCP 原始 JSON。 */
export declare function textContent(value: JsonValue): ContentBlock[];
export {};
