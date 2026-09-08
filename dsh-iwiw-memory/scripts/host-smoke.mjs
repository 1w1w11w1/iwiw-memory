/**
 * host-smoke.mjs — 插件宿主冒烟（可见验证，不进生产 profile）
 *
 * 用真实 MCP 后端（临时库）加载编译产物：
 *   1. apply(ctx) → 打印注册的工具与 system prompt sections
 *   2. 逐个调用 memory_remember / memory_search / memory_read / memory_list
 *   3. 验证 remember 后 core 注入段刷新（A2）
 * 运行：node scripts/host-smoke.mjs
 */
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import { apply } from "../lib/index.js";

// 隔离库：显式经 config.env 传给 MCP 子进程（MCP SDK 不继承完整父进程 env）
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "iwiw-smoke-"));
const dbPath = path.join(tmp, "memory.db");

const python = process.env.SMOKE_PYTHON ?? "E:/desktop/111/.venv/Scripts/python.exe";
const cwd = process.env.SMOKE_CWD ?? "E:/desktop/111";

const registered = [];
const sections = [];
const ctx = {
  logger: {
    info: (...a) => console.log("[info]", ...a),
    warn: (...a) => console.log("[warn]", ...a),
    error: (...a) => console.log("[error]", ...a),
  },
  tools: { register: (def) => { registered.push(def); return () => {}; } },
  systemPrompt: { section: (s) => { sections.push(s); return () => {}; } },
};

console.log("=== 1. apply ===");
const dispose = await apply(ctx, { python, cwd, env: { MEMORY_AGENT_DB_PATH: dbPath } });

console.log("\n=== 2. 注册结果 ===");
console.log("tools:", registered.map((d) => d.name).join(", "));
console.log("sections:", sections.map((s) => s.name + "(order=" + s.order + ")").join(", "));
const core = sections.find((s) => s.name.includes("core"));
const coreText = () => (typeof core.text === "function" ? core.text() : core.text);
console.log("core 初始:", JSON.stringify(coreText()));

console.log("\n=== 3. 工具调用（临时库）===");
const byName = Object.fromEntries(registered.map((d) => [d.name, d]));

const r1 = await byName.memory_remember.execute({
  description: "用户对花生过敏",
  body: "用户对花生及花生制品过敏，误食会出现皮疹与呼吸困难。",
  priority: "core",
});
const slug = r1?.result?.slug;
console.log("memory_remember →", JSON.stringify(r1).slice(0, 200));

const r2 = await byName.memory_search.execute({ query: "花生 过敏" });
console.log("memory_search →", JSON.stringify(r2).slice(0, 200));

const r3 = await byName.memory_read.execute({ slug });
console.log("memory_read →", JSON.stringify(r3).slice(0, 160));

const r4 = await byName.memory_list.execute({ priority: "core", limit: 10 });
console.log("memory_list →", JSON.stringify(r4).slice(0, 200));

console.log("\n=== 4. core 注入段刷新验证（A2）===");
await new Promise((res) => setTimeout(res, 600));
const refreshed = coreText();
console.log("core 刷新后:", refreshed.slice(0, 300));
const a2ok = refreshed.includes(slug);
console.log("A2 写入可见:", a2ok ? "PASS" : "FAIL");
// 判别调试：MCP 数据 vs 缓存链路
const recheck = await byName.memory_list.execute({ priority: "core" });
console.log("[debug] 复检 MCP list:", JSON.stringify(recheck).slice(0, 200));
console.log("[debug] 此刻 coreText:", JSON.stringify(coreText()).slice(0, 200));

console.log("\n=== 5. dispose ===");
await dispose();
fs.rmSync(tmp, { recursive: true, force: true });
console.log(a2ok ? "SMOKE ALL PASS" : "SMOKE FAILED");
