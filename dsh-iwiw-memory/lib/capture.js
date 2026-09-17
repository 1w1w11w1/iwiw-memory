/**
 * capture.ts — 跨项目「想法带回」管道（纯开发者工具，可整块剥离）。
 *
 * 两个入口，都只服务一个场景：在别的项目开发时冒出优化想法，
 * 把它连同当时的会话现场带回本项目（E:/desktop/111）讨论。
 *
 *   入口 A \`/memo <想法>\` —— 在别的项目会话里跑。
 *     读当前会话明文 JSONL + 这句话，落 debug-inbox/ 顶层。
 *     比 DSH 原生 /export 少两步：不经浏览器下载目录、不需手工搬运。
 *
 *   入口 B \`/recall\` —— 在本项目会话里跑。
 *     取 inbox 最新 memo，经 memory_agent.debug_bundle 解析成逐轮事实链回灌上下文。
 *     只搬现场，不自动写记忆（是否值得长期保存由人在对话里判断，走既有 memory_remember）。
 *
 * 迁移/移除：删除本文件 + index.ts 里两行挂载 + package.json 的 peerDependency 即可，
 * 内核 memory_agent/ 零改动，无环境变量、无配置项、无 DB schema 变更。
 */
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { execFile } from "node:child_process";
import { mkdirSync, statSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { promisify } from "node:util";
const execFileAsync = promisify(execFile);
/** memo 落盘目录：本项目 debug-inbox（已 gitignore，含会话明文，不可入库）。 */
const INBOX_DIR = "E:/desktop/111/debug-inbox";
/** memory_agent 所在仓库根（与插件的 python/cwd 部署配置同源）。 */
const PROJECT_ROOT = "E:/desktop/111";
/**
 * 解析 DSH 内部导出包。
 *
 * 两个已实证的坑：
 *  1. 插件本地 node_modules 里没有 @deepseek-ai/dsh-session-log-export（只有旧版 dsh-* 包），
 *     运行时靠 profile 目录逐级向上走到 npm 全局安装树解析 —— 所以从 profile 位置 createRequire。
 *  2. Windows 下 import 绝对路径必须转 file:// URL，否则 ERR_UNSUPPORTED_ESM_URL_SCHEME。
 */
async function loadSessionLogExport(profileDir) {
    const req = createRequire(join(profileDir, "noop.js"));
    const resolved = req.resolve("@deepseek-ai/dsh-session-log-export");
    return import(pathToFileURL(resolved).href);
}
/** 从会话 cwd 末段取项目名，用于 memo 文件名前缀。 */
function projectNameOf(cwd) {
    if (!cwd)
        return "unknown";
    const parts = cwd.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean);
    const last = parts[parts.length - 1] ?? "unknown";
    return last.replace(/[^A-Za-z0-9_-]/g, "_") || "unknown";
}
/** 时间戳（本地，文件名安全）：20261116-153012 */
function stamp(d = new Date()) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}
/** 取 inbox 顶层最新的 memo/导出包（与 debug_bundle.newest_in_inbox 同规则：只看顶层文件）。 */
function newestMemo() {
    try {
        const cands = readdirSync(INBOX_DIR)
            .filter((n) => n.endsWith(".jsonl") || n.endsWith(".zip"))
            .map((n) => join(INBOX_DIR, n));
        if (cands.length === 0)
            return null;
        return cands.reduce((a, b) => (statSync(a).mtimeMs >= statSync(b).mtimeMs ? a : b));
    }
    catch {
        return null;
    }
}
/** 调本项目 venv 的 python 跑 debug_bundle 解析，返回事实链文本。 */
async function parseMemo(python, path) {
    try {
        const { stdout } = await execFileAsync(python, ["-m", "memory_agent.debug_bundle", path], { cwd: PROJECT_ROOT, timeout: 60_000, maxBuffer: 16 * 1024 * 1024 });
        return { ok: true, text: stdout.trim() };
    }
    catch (e) {
        const detail = (e?.stderr || e?.stdout || String(e)).toString().trim();
        return { ok: false, text: detail.slice(0, 2000) };
    }
}
/**
 * 注册 /memo 与 /recall。
 * @returns disposer 列表（调用方负责统一释放）。
 */
export function registerCaptureCommands(ctx, config) {
    const disposers = [];
    // ── 入口 A：/memo <想法> ──
    disposers.push(ctx.commands.register({
        name: "memo",
        description: "把当前会话现场 + 一句想法落进记忆项目的 debug-inbox",
        input: { hint: "<想法>" },
        handler: async (invocation) => {
            const thought = String(invocation?.rawInput ?? "").trim();
            if (!thought) {
                return { kind: "error", text: "用法：/memo <一句话想法>" };
            }
            const session = invocation?.agent?.session;
            const sid = session?.header?.id;
            if (typeof sid !== "string") {
                return { kind: "error", text: "/memo 失败：拿不到当前会话 id" };
            }
            try {
                const mod = await loadSessionLogExport(config.profileDir);
                const { sessionLogExportDeps, flushLiveSessionLog, readSessionLogText } = mod;
                const deps = sessionLogExportDeps(ctx);
                if (!deps.sessionPersistence) {
                    return { kind: "error", text: "/memo 失败：sessionPersistence 服务不可用" };
                }
                // 先走 durability barrier，确保刚发生的事件也进导出（与原生 /export 同款）
                await flushLiveSessionLog(deps, sid, invocation.signal);
                const jsonl = await readSessionLogText(deps.sessionPersistence, sid, invocation.signal);
                if (typeof jsonl !== "string" || jsonl.length === 0) {
                    return { kind: "error", text: `/memo 失败：会话 ${sid} 读不到日志` };
                }
                mkdirSync(INBOX_DIR, { recursive: true });
                const name = `${projectNameOf(session?.header?.cwd)}-${stamp()}-memo.jsonl`;
                const outPath = join(INBOX_DIR, name);
                // 顶行写想法：不破坏 JSONL 可解析性（debug_bundle 跳过非 JSON 行）
                const header = JSON.stringify({
                    type: "memo/note", thought, sessionId: sid, cwd: session?.header?.cwd ?? null,
                });
                writeFileSync(outPath, `${header}\n${jsonl}`, "utf8");
                const kb = Math.round(Buffer.byteLength(jsonl, "utf8") / 1024);
                return {
                    kind: "success",
                    text: `已带回：debug-inbox/${name}（${kb} KB）\n想法：${thought}\n回本项目会话执行 /recall 拉取。`,
                };
            }
            catch (e) {
                return { kind: "error", text: `/memo 失败：${String(e?.message ?? e)}` };
            }
        },
    }));
    // ── 入口 B：/recall ──
    disposers.push(ctx.commands.register({
        name: "recall",
        description: "取 debug-inbox 最新 memo，解析成逐轮事实链",
        handler: async (invocation) => {
            const explicit = String(invocation?.rawInput ?? "").trim();
            const target = explicit || newestMemo();
            if (!target) {
                return { kind: "error", text: `debug-inbox 里没有可解析的包（${INBOX_DIR}）。先在别的项目会话执行 /memo。` };
            }
            const r = await parseMemo(config.python, target);
            if (!r.ok) {
                return { kind: "error", text: `/recall 解析失败：${r.text}` };
            }
            return { kind: "success", text: r.text };
        },
    }));
    return disposers;
}
