import type { ContentBlock } from "@deepseek-ai/dsh-llm";
type JsonValue = string | number | boolean | null | JsonValue[] | {
    [k: string]: JsonValue;
};
import { MemoryBackend, type BackgroundCallOptions } from "./backend.js";
export interface ConsolidateOutcome {
    ok: boolean;
    error?: string;
    reviewed?: number;
    changedRows?: number;
    raw?: JsonValue;
}
/** 严格判定 consolidate 结果：MCP 返回字符串、{error:...}、complete=false
 *  或结构缺失都不算成功——否则会向上冒充「已补账」并推进水位。 */
export declare function consolidateOutcome(value: JsonValue): ConsolidateOutcome;
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
        project?: string;
    }): Promise<JsonValue>;
    search(params: {
        query: string;
        top_k?: number;
        session_id?: string;
        context?: string[];
        exclude_mem_types?: string[];
        project?: string;
    }): Promise<JsonValue>;
    /** 命中自增（使用强化）：记忆被实际注入时调用，供维护排序。 */
    touch(slugs: string[]): Promise<JsonValue>;
    /** 记忆巩固（consolidate）：低风险修正自动留痕执行，归档走审批。
     *  给了窗口（sinceMs/untilMs）就是启动补账模式：limit 是每批大小，
     *  窗口内候选完整分批，不静默丢候选。 */
    consolidate(options?: {
        sinceHours?: number;
        limit?: number;
        sinceMs?: number;
        untilMs?: number;
    }, callOptions?: BackgroundCallOptions): Promise<JsonValue>;
    /** 启动补账专用：固定窗口 + 严格结果判定。 */
    consolidateWindow(sinceMs: number, untilMs: number, signal?: AbortSignal): Promise<ConsolidateOutcome>;
    /** 目标库标识（后台 domain 名称与跨进程锁 key 用）。旧后端没有该字段。 */
    databaseId(): Promise<string | null>;
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
