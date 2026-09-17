/**
 * capture.ts — 插件自有的开发者工具命令（纯开发者工具，可整块剥离）。
 *
 * 三个入口，都只读现场、不改记忆：
 *
 *   入口 A \`/memo <想法>\` —— 在别的项目会话里跑。
 *     读当前会话明文 JSONL + 这句话，落 debug-inbox/ 顶层。
 *     比 DSH 原生 /export 少两步：不经浏览器下载目录、不需手工搬运。
 *
 *   入口 B \`/recall\` —— 在本项目会话里跑。
 *     取 inbox 最新 memo，经 memory_agent.debug_bundle 解析成逐轮事实链回灌上下文。
 *     只搬现场，不自动写记忆（是否值得长期保存由人在对话里判断，走既有 memory_remember）。
 *
 *   入口 C \`/iwiw-prompt\` —— 回显本插件此刻注入的 system prompt 段全文，
 *     用于核对注入内容。段文本取自 prompts.ts 的唯一构造源，与注册同源。
 *
 * 迁移/移除：删除本文件 + index.ts 里两行挂载 + package.json 的 peerDependency 即可，
 * 内核 memory_agent/ 零改动，无环境变量、无 DB schema 变更。
 */

import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { execFile } from "node:child_process";
import { mkdirSync, statSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { promisify } from "node:util";
import { iwiwSections } from "./prompts.js";

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
async function loadSessionLogExport(profileDir: string): Promise<any> {
  const req = createRequire(join(profileDir, "noop.js"));
  const resolved = req.resolve("@deepseek-ai/dsh-session-log-export");
  return import(pathToFileURL(resolved).href);
}

/** 从会话 cwd 末段取项目名，用于 memo 文件名前缀。 */
function projectNameOf(cwd: string | undefined): string {
  if (!cwd) return "unknown";
  const parts = cwd.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean);
  const last = parts[parts.length - 1] ?? "unknown";
  return last.replace(/[^A-Za-z0-9_-]/g, "_") || "unknown";
}

/** 时间戳（本地，文件名安全）：20261116-153012 */
function stamp(d: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

/** 取 inbox 顶层最新的 memo/导出包（与 debug_bundle.newest_in_inbox 同规则：只看顶层文件）。 */
function newestMemo(): string | null {
  try {
    const cands = readdirSync(INBOX_DIR)
      .filter((n) => n.endsWith(".jsonl") || n.endsWith(".zip"))
      .map((n) => join(INBOX_DIR, n));
    if (cands.length === 0) return null;
    return cands.reduce((a, b) => (statSync(a).mtimeMs >= statSync(b).mtimeMs ? a : b));
  } catch {
    return null;
  }
}

/** 调本项目 venv 的 python 跑 debug_bundle 解析，返回事实链文本。 */
async function parseMemo(python: string, path: string): Promise<{ ok: true; text: string } | { ok: false; text: string }> {
  try {
    const { stdout } = await execFileAsync(
      python,
      ["-m", "memory_agent.debug_bundle", path],
      { cwd: PROJECT_ROOT, timeout: 60_000, maxBuffer: 16 * 1024 * 1024 },
    );
    return { ok: true, text: stdout.trim() };
  } catch (e: any) {
    const detail = (e?.stderr || e?.stdout || String(e)).toString().trim();
    return { ok: false, text: detail.slice(0, 2000) };
  }
}

interface CaptureConfig {
  /** 含 mcp/jieba 依赖的 Python 解释器（与 index.ts 的 config.python 同源）。 */
  python: string;
  /** 当前 DSH profile 目录（用于解析 DSH 内部包）。 */
  profileDir: string;
  /** 读取当前注入段的正文（index.ts 的 core/rules 缓存）。 */
  promptText: { core: () => string; rules: () => string };
}

/**
 * 注册 /memo 与 /recall。
 * @returns disposer 列表（调用方负责统一释放）。
 */
export function registerCaptureCommands(ctx: any, config: CaptureConfig): Array<() => void> {
  const disposers: Array<() => void> = [];

  // ── 入口 A：/memo <想法> ──
  disposers.push(ctx.commands.register({
    name: "memo",
    description: "把当前会话现场 + 一句想法落进记忆项目的 debug-inbox",
    input: { hint: "<想法>" },
    handler: async (invocation: any) => {
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
      } catch (e: any) {
        return { kind: "error", text: `/memo 失败：${String(e?.message ?? e)}` };
      }
    },
  }));

  // ── 入口 C：/iwiw-prompt ──
  disposers.push(ctx.commands.register({
    name: "iwiw-prompt",
    description: "回显 iwiw 此刻注入的 system prompt 段",
    handler: () => {
      const sections = iwiwSections(config.promptText);
      const body = sections
        .map((s) => {
          const text = s.text();
          return `### ${s.name} (order=${s.order})\n\n${text || "（本段为空，未注入）"}`;
        })
        .join("\n\n---\n\n");
      return { kind: "success", text: `iwiw 注入的 system prompt（${sections.length} 段）\n\n${body}` };
    },
  }));

  // ── 入口 B：/recall ──
  disposers.push(ctx.commands.register({
    name: "recall",
    description: "取 debug-inbox 最新 memo，解析成逐轮事实链",
    handler: async (invocation: any) => {
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
