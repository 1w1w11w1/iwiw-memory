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
        level?: string;
        slug?: string;
        priority?: string;
    }): Promise<JsonValue>;
    search(params: {
        query: string;
        top_k?: number;
        session_id?: string;
        context?: string[];
        exclude_mem_types?: string[];
    }): Promise<JsonValue>;
    /** 命中自增（使用强化）：记忆被实际注入时调用，供维护排序。 */
    touch(slugs: string[]): Promise<JsonValue>;
    /** 记忆巩固（consolidate）：低风险修正自动留痕执行，归档走审批。 */
    consolidate(sinceHours?: number, limit?: number): Promise<JsonValue>;
    read(params: {
        slug: string;
    }): Promise<JsonValue>;
    list(params: {
        priority?: string;
        mem_type?: string;
        limit?: number;
    }): Promise<JsonValue>;
    private parse;
}
/** 构造 model-facing 文本块，保留 MCP 原始 JSON。 */
export declare function toTextBlocks(value: unknown): ContentBlock[];
export {};
