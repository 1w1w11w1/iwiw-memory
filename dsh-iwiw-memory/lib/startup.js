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
import { createHash } from "node:crypto";
import { stat } from "node:fs/promises";
import { z } from "zod";
// ── 状态 schema ────────────────────────────────────────────────────────────────
export const StateSchema = z.object({
    lastRun: z.number().int().nonnegative().nullable(),
    lastConsolidatedAt: z.number().int().nonnegative().nullable(),
});
export const INITIAL_STATE = {
    lastRun: null,
    lastConsolidatedAt: null,
};
/** storage domain 描述符。scopeKey 是 Python 返回的数据库标识，
 *  避免同一个 storage 根下两个不同目标库互相覆盖水位。 */
export const makeDomainSpec = (scopeKey) => ({
    name: `dsh-iwiw-memory-startup-${scopeKey}`,
    version: 1,
    global: { schema: StateSchema, initial: INITIAL_STATE },
    tables: {},
});
// ── 跨进程占位锁 ──────────────────────────────────────────────────────────────
/**
 * 取 DB 范围的后台任务互斥锁。
 *
 * 不用 get()+set(lease)：两个进程可能同时读到「空闲」再同时写。
 * 不用 wx 锁文件：进程被 kill 后会遗留文件需要 TTL/清理。
 * 命名管道由 OS 在进程退出时释放，没有遗留状态。
 */
export function acquireStartupLock(dbKey) {
    const server = net.createServer((socket) => socket.destroy());
    return new Promise((resolve, reject) => {
        server.once("error", (error) => {
            if (error.code === "EADDRINUSE")
                resolve(null);
            else
                reject(error);
        });
        server.listen(`\\\\.\\pipe\\dsh-iwiw-memory-startup-${dbKey}`, () => resolve(server));
    });
}
export function releaseStartupLock(server) {
    if (!server)
        return Promise.resolve();
    return new Promise((resolve) => server.close(() => resolve()));
}
export function scopeKeyOf(databaseId) {
    return createHash("sha256").update(databaseId).digest("hex").slice(0, 16);
}
// ── 会话事件判定 ──────────────────────────────────────────────────────────────
/** 只认「真正完成」的轮次：turn/end + reason.kind === 'completed'。
 *  host 在 cold replay 时会合成 interrupted 收尾（不写盘），那不是补账证据。 */
export function completedInWindow(event, from, to) {
    return event?.type === "turn/end"
        && event?.data?.reason?.kind === "completed"
        && Number.isFinite(event?.time)
        && event.time > from
        && event.time <= to;
}
export function countCompletedTurns(events, from, to) {
    let n = 0;
    for (const e of events)
        if (completedInWindow(e, from, to))
            n += 1;
    return n;
}
/** 缺能力就整体停用后台补账并按普通内存插件运行，绝不抛出到主 apply。 */
export function probeHost(sctx) {
    if (!sctx?.storageDomain?.open)
        return { ok: false, reason: "storageDomain missing" };
    if (!sctx?.sessionQuery?.listSessions)
        return { ok: false, reason: "sessionQuery missing" };
    if (!sctx?.sessionQuery?.readSession)
        return { ok: false, reason: "sessionQuery.readSession missing" };
    if (!sctx?.sessionPersistence)
        return { ok: false, reason: "sessionPersistence missing" };
    if (typeof sctx.sessionPersistence.resolveCurrentLog !== "function") {
        return { ok: false, reason: "sessionPersistence.resolveCurrentLog missing" };
    }
    return { ok: true };
}
/** 读一个会话的完整事件链。宿主会对 live 优先读内存，对 cold 读持久化日志。 */
export async function readSessionLog(sctx, record, signal) {
    if (signal.aborted)
        throw signal.reason ?? new Error("aborted");
    const id = record?.header?.id;
    if (!id)
        throw new Error("session record missing id");
    const snap = await sctx.sessionQuery.readSession(id);
    if (signal.aborted)
        throw signal.reason ?? new Error("aborted");
    const inheritedEventCount = snap?.inheritedEventCount;
    if (!Array.isArray(snap?.events)
        || !Number.isInteger(inheritedEventCount)
        || inheritedEventCount < 0
        || inheritedEventCount > snap.events.length) {
        throw new Error(`invalid session snapshot: ${id}`);
    }
    return { events: snap.events, inheritedEventCount };
}
/** 列主会话（排除 subagent 分支）。不做 cwd 过滤：config.cwd 是插件部署目录，
 *  不是用户会话所属工作区。 */
export async function listMainSessions(sctx, signal) {
    const records = await sctx.sessionQuery.listSessions(signal);
    if (!Array.isArray(records))
        throw new Error("sessionQuery.listSessions returned a non-array result");
    return records.filter((r) => r?.header?.origin !== "subagent");
}
/** cold 会话的 mtime 粗筛：文件没动过就不必完整解码。
 *  路径不可得返回 true（宁可多读，不可漏读）。 */
export async function coldLogFresh(sctx, record, since, signal) {
    if (record?.live)
        return true;
    const id = record?.header?.id;
    if (!id)
        throw new Error("session record missing id");
    const path = await sctx.sessionPersistence.resolveCurrentLog(id, signal);
    if (!path)
        return true;
    const st = await stat(path);
    return st.mtimeMs >= since;
}
export async function runStartup(sctx, options) {
    const now = options.now ?? (() => Date.now());
    const assertActive = () => {
        if (options.signal.aborted)
            throw options.signal.reason ?? new Error("aborted");
    };
    if (options.signal.aborted)
        return { status: "failed", error: "aborted" };
    const caps = probeHost(sctx);
    if (!caps.ok) {
        options.log.warn(`[dsh-iwiw-memory] startup reconcile disabled: ${caps.reason}`);
        return { status: "disabled", error: caps.reason };
    }
    const databaseId = await options.getDatabaseId();
    if (!databaseId) {
        options.log.warn("[dsh-iwiw-memory] startup reconcile disabled: backend exposes no database_id (upgrade the Python kernel)");
        return { status: "disabled", error: "no database_id" };
    }
    const scopeKey = scopeKeyOf(databaseId);
    // 顺序很重要：先拿锁，再打开 domain 重新加载状态。
    // 反过来会先缓存旧水位再等锁，等到的锁形同虚设。
    const lock = await acquireStartupLock(scopeKey);
    if (!lock) {
        options.log.info("[dsh-iwiw-memory] startup reconcile skipped: another process holds the db lock");
        return { status: "busy" };
    }
    let domain = null;
    try {
        domain = await sctx.storageDomain.open(makeDomainSpec(scopeKey));
        const state = StateSchema.parse(domain.global.get());
        const cutoff = now();
        const save = async (next) => {
            assertActive();
            await domain.global.set(StateSchema.parse(next));
        };
        // 时钟回拨：不扫描、不巩固、不推进也不降低水位。
        // 不能用 Math.max(lastRun, now) 假装解决——回拨期间的新事件也可能小于旧水位。
        if (state.lastRun !== null && cutoff < state.lastRun) {
            options.log.warn(`[dsh-iwiw-memory] clock regressed (lastRun=${state.lastRun} > now=${cutoff}); `
                + "skipping reconcile and keeping the watermark. Re-scan explicitly after fixing the clock.");
            return { status: "clock-regressed", from: state.lastRun, cutoff };
        }
        // 首次启动：只写基线水位，不列会话、不 stat、不冷读、不调 LLM。
        // 这明确放弃首次基线之前的存量整理（从零开始）。
        if (state.lastRun === null) {
            await save({ ...state, lastRun: cutoff, lastConsolidatedAt: cutoff });
            options.log.info(`[dsh-iwiw-memory] startup baseline initialized at ${cutoff}`);
            return { status: "first-run", from: null, cutoff };
        }
        const from = state.lastRun;
        const sessions = await listMainSessions(sctx, options.signal);
        let sessionsRead = 0;
        let completedTurns = 0;
        for (const record of sessions) {
            if (options.signal.aborted)
                return { status: "failed", error: "aborted", from, cutoff };
            const fresh = await coldLogFresh(sctx, record, from, options.signal);
            if (!fresh)
                continue;
            const read = await readSessionLog(sctx, record, options.signal);
            sessionsRead += 1;
            // 跳过 fork 继承的旧事件前缀，避免分支继承重复计入。
            const own = read.events.slice(read.inheritedEventCount);
            completedTurns += countCompletedTurns(own, from, cutoff);
            // 顺序读取：每读完一个会话让出一次事件循环，不阻塞宿主。
            await new Promise((r) => setImmediate(r));
        }
        // 没有新完成的轮次：只推进 lastRun，lastConsolidatedAt 原地不动。
        if (completedTurns === 0) {
            await save({ ...state, lastRun: cutoff });
            options.log.info(`[dsh-iwiw-memory] startup reconcile: no completed turns in (${from}, ${cutoff}]`);
            return { status: "noop", from, cutoff, sessionsListed: sessions.length, sessionsRead, completedTurns: 0 };
        }
        // 有完成轮次：整理从 lastConsolidatedAt 到 cutoff 的**记忆**窗口。
        // 窗口起点用 lastConsolidatedAt 而不是 lastRun，这样「先写记忆、后完成轮次」
        // 的那段记忆不会被跳过。
        const sinceMs = state.lastConsolidatedAt ?? from;
        assertActive();
        const result = await options.consolidate(sinceMs, cutoff);
        if (!result.ok) {
            // 失败不推进水位：下次启动按旧水位重试（后端要求可安全重放）。
            options.log.warn(`[dsh-iwiw-memory] startup consolidate failed: ${result.error}`);
            return { status: "failed", from, cutoff, completedTurns, error: result.error };
        }
        await save({ ...state, lastRun: cutoff, lastConsolidatedAt: cutoff });
        const reviewed = result.reviewed ?? 0;
        options.report([
            "## 记忆巩固报告",
            "",
            `启动补账完成：检查了 ${sessionsRead} 个会话 / ${completedTurns} 个已完成轮次，审查 ${reviewed} 条近期记忆。`,
        ].join("\n"));
        options.log.info(`[dsh-iwiw-memory] startup reconcile done: sessionsRead=${sessionsRead} completedTurns=${completedTurns} reviewed=${reviewed}`);
        return { status: "consolidated", from, cutoff, sessionsListed: sessions.length, sessionsRead, completedTurns, reviewed };
    }
    catch (error) {
        // 状态写入失败、domain 打开失败等都算失败：不冒充完成。
        options.log.warn("[dsh-iwiw-memory] startup reconcile failed", error);
        return { status: "failed", error: String(error) };
    }
    finally {
        try {
            await domain?.close();
        }
        catch { }
        await releaseStartupLock(lock);
    }
}
