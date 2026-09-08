import type { ContentBlock } from "@deepseek-ai/dsh-llm";
type JsonValue = string | number | boolean | null | JsonValue[] | {
    [k: string]: JsonValue;
};
import { MemoryBackend } from "./backend.js";
/** 4 个 memory_* 工具：桥接到 Python memory_agent.mcp_server，
 *  与 CLI chat 的模型工具面同源（执行逻辑单源在内核 model_tools.py）。
 */
export declare class MemoryTools {
    private readonly backend;
    constructor(backend: MemoryBackend);
    remember(params: {
        description: string;
        body: string;
        slug?: string;
        priority?: string;
    }): Promise<JsonValue>;
    search(params: {
        query: string;
        top_k?: number;
    }): Promise<JsonValue>;
    read(params: {
        slug: string;
    }): Promise<JsonValue>;
    list(params: {
        priority?: string;
        limit?: number;
    }): Promise<JsonValue>;
    private parse;
}
/** 构造 model-facing 文本块，保留 MCP 原始 JSON。 */
export declare function toTextBlocks(value: unknown): ContentBlock[];
export {};
