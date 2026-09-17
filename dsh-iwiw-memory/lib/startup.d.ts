/**
 * startup.ts — 启动补账（A）的宿主侧编排。
 *
 * 设计要点（每条都对应一个已实测的宿主事实，不是推测）：
 *  - 用「每次插件启动一次后台补账」替换旧的 10 分钟空闲轮询；不再看空闲时长，
 *    也没有峰时抑制——补账与聊天活跃度无关。
 *  - A 只判断「有没有新完成的对话」，触发整理**已有数据库记忆**；不从会话里
 *    抽取事实。
 *  - 水位是两个独立边界，不能合成一个值：
 *      lastRun            = 已成功检查的**会话事件**边界
 *      lastConsolidatedAt = 已成功整理的**记忆**边界
 *    先写记忆、后完成轮次时，若只推 lastRun 会漏审，故必须分开。
 *  - 所有时间窗口在任务开始时固定，成功才推进；不在 finally 里写「当前时间」。
 *  - 会话读取一律走宿主公开能力：cold 用 resolveCurrentLog 定位当前代并做 mtime
 *    粗筛，完整内容统一由 sessionQuery.readSession 读取。绝不自己拼路径或解码 zstd。
 *
 * 可整块剥离：删除本文件 + index.ts 里的一段挂载即可，内核 memory_agent/ 零改动。
 */
import net from "node:net";
import { z } from "zod";
export declare const StateSchema: z.ZodObject<{
    lastRun: z.ZodNullable<z.ZodNumber>;
    lastConsolidatedAt: z.ZodNullable<z.ZodNumber>;
}, z.core.$strip>;
export type StartupState = z.infer<typeof StateSchema>;
export declare const INITIAL_STATE: StartupState;
/** storage domain 描述符。scopeKey 是 Python 返回的数据库标识，
 *  避免同一个 storage 根下两个不同目标库互相覆盖水位。 */
export declare const makeDomainSpec: (scopeKey: string) => {
    name: string;
    version: number;
    global: {
        schema: z.ZodObject<{
            lastRun: z.ZodNullable<z.ZodNumber>;
            lastConsolidatedAt: z.ZodNullable<z.ZodNumber>;
        }, z.core.$strip>;
        initial: {
            lastRun: number | null;
            lastConsolidatedAt: number | null;
        };
    };
    tables: {};
};
/**
 * 取 DB 范围的后台任务互斥锁。
 *
 * 不用 get()+set(lease)：两个进程可能同时读到「空闲」再同时写。
 * 不用 wx 锁文件：进程被 kill 后会遗留文件需要 TTL/清理。
 * 命名管道由 OS 在进程退出时释放，没有遗留状态。
 */
export declare function acquireStartupLock(dbKey: string): Promise<net.Server | null>;
export declare function releaseStartupLock(server: net.Server | null): Promise<void>;
export declare function scopeKeyOf(databaseId: string): string;
/** 只认「真正完成」的轮次：turn/end + reason.kind === 'completed'。
 *  host 在 cold replay 时会合成 interrupted 收尾（不写盘），那不是补账证据。 */
export declare function completedInWindow(event: any, from: number, to: number): boolean;
export declare function countCompletedTurns(events: any[], from: number, to: number): number;
export interface HostCapabilities {
    ok: boolean;
    reason?: string;
}
/** 缺能力就整体停用后台补账并按普通内存插件运行，绝不抛出到主 apply。 */
export declare function probeHost(sctx: any): HostCapabilities;
export interface ReadResult {
    events: any[];
    inheritedEventCount: number;
}
/** 读一个会话的完整事件链。宿主会对 live 优先读内存，对 cold 读持久化日志。 */
export declare function readSessionLog(sctx: any, record: any, signal: AbortSignal): Promise<ReadResult>;
/** 列主会话（排除 subagent 分支）。不做 cwd 过滤：config.cwd 是插件部署目录，
 *  不是用户会话所属工作区。 */
export declare function listMainSessions(sctx: any, signal: AbortSignal): Promise<any[]>;
/** cold 会话的 mtime 粗筛：文件没动过就不必完整解码。
 *  路径不可得返回 true（宁可多读，不可漏读）。 */
export declare function coldLogFresh(sctx: any, record: any, since: number, signal: AbortSignal): Promise<boolean>;
export interface StartupOptions {
    /** 取一次目标库标识（Python memory_stats 的 database_id）。 */
    getDatabaseId: () => Promise<string | null>;
    /** 固定窗口整理。err 非空表示未成功。 */
    consolidate: (sinceMs: number, untilMs: number) => Promise<{
        ok: boolean;
        error?: string;
        reviewed?: number;
        changedRows?: number;
    }>;
    /** 完成情况上报（供下一次对话一次性告知）。 */
    report: (text: string) => void;
    log: {
        info: (msg: string) => void;
        warn: (msg: string, err?: unknown) => void;
    };
    signal: AbortSignal;
    now?: () => number;
}
export interface StartupOutcome {
    status: "first-run" | "noop" | "consolidated" | "busy" | "clock-regressed" | "failed" | "disabled";
    from?: number | null;
    cutoff?: number;
    sessionsListed?: number;
    sessionsRead?: number;
    completedTurns?: number;
    reviewed?: number;
    error?: string;
}
export declare function runStartup(sctx: any, options: StartupOptions): Promise<StartupOutcome>;
