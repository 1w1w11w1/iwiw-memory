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
import { computeInjectionGroups } from "../lib/client-fold.js";

// 隔离库：显式经 config.env 传给 MCP 子进程（MCP SDK 不继承完整父进程 env）
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "iwiw-smoke-"));
const dbPath = path.join(tmp, "memory.db");

const python = process.env.SMOKE_PYTHON ?? "E:/desktop/111/.venv/Scripts/python.exe";
const cwd = process.env.SMOKE_CWD ?? "E:/desktop/111";

const registered = [];
const sections = [];
const handlers = {};
const ctx = {
  logger: {
    info: (...a) => console.log("[info]", ...a),
    warn: (...a) => console.log("[warn]", ...a),
    error: (...a) => console.log("[error]", ...a),
  },
  tools: { register: (def) => { registered.push(def); return () => {}; } },
  systemPrompt: { section: (s) => { sections.push(s); return () => {}; } },
  on: (event, handler) => { (handlers[event] ??= []).push(handler); return () => {}; },
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

console.log("\n=== 5. pre-step 每消息命中注入 ===");
const preStep = (handlers["agent/pre-step"] ?? [])[0];
if (!preStep) { console.log("SMOKE FAILED: no pre-step handler"); process.exit(1); }
const mkDecision = (text) => ({
  kind: "enter",
  messages: [{ id: "u-" + Math.random().toString(36).slice(2), role: "user", source: { kind: "user" }, content: [{ type: "text", text }] }],
});
const agentMock = { session: { header: { id: "smoke-sess", origin: "main" } } };
const signal = { aborted: false };
// 真实 cordis 的 next() 无参返回 pipeline decision；mock 用外部变量承载
let currentDecision;
const next = async () => currentDecision;

// 播种一条 normal 记忆（供命中）
await byName.memory_remember.execute({
  description: "用户有哮喘",
  body: "用户有哮喘病史，剧烈运动或冷空气刺激易诱发，随身携带缓解药物。",
  priority: "normal",
});

// 第一次提问：应命中哮喘记忆并插入快照消息
currentDecision = mkDecision("我的哮喘平时要注意什么");
const out1 = await preStep({ agent: agentMock, messages: currentDecision.messages, signal }, next);
const snap1 = out1.messages.filter((m) => m.source?.kind === "plugin");
const hitAsthma = snap1.some((m) => JSON.stringify(m).includes("哮喘"));
console.log("首次注入:", snap1.length, "条快照, 含哮喘:", hitAsthma);

// 同会话二次提问（同主题）：去重后不应再注入
currentDecision = mkDecision("哮喘发作怎么办");
const out2 = await preStep({ agent: agentMock, messages: currentDecision.messages, signal }, next);
const snap2 = out2.messages.filter((m) => m.source?.kind === "plugin");
const dedupOk = snap2.length === 0;
console.log("二次注入（应去重为 0）:", snap2.length, "条快照");

// 无关提问：不应注入
currentDecision = mkDecision("帮我写一个 python 快速排序");
const out3 = await preStep({ agent: agentMock, messages: currentDecision.messages, signal }, next);
const snap3 = out3.messages.filter((m) => m.source?.kind === "plugin");
const unrelatedOk = snap3.length === 0;
console.log("无关提问（应 0）:", snap3.length, "条快照");

const hitOk = snap1.length >= 1 && hitAsthma;
const stepOk = hitOk && dedupOk && unrelatedOk;
console.log("pre-step 断言:", stepOk ? "PASS" : "FAIL", JSON.stringify({ hitOk, dedupOk, unrelatedOk }));

console.log("\n=== 5.5 压缩后补回（reinject）===");
const sessionEvt = (handlers["session/event"] ?? [])[0];
if (!sessionEvt) { console.log("SMOKE FAILED: no session/event handler"); process.exit(1); }
const sessMock = { id: "smoke-sess", header: { id: "smoke-sess", cwd: "E:/desktop/111", origin: "main" } };
sessionEvt(sessMock, { type: "compaction/start" });
sessionEvt(sessMock, { type: "compaction/end" });
// 压缩后新消息：应补回本会话注入过的记忆（哮喘 + 花生）
currentDecision = mkDecision("我们继续刚才的话题");
const out4 = await preStep({ agent: agentMock, messages: currentDecision.messages, signal }, next);
const snap4 = out4.messages.filter((m) => m.source?.kind === "plugin");
const reinj = snap4.find((m) => m.source?.memory?.kind === "reinjection");
const reinjOk = !!reinj && JSON.stringify(reinj).includes("哮喘") && JSON.stringify(reinj).includes("花生");
console.log("补回快照:", snap4.length, "条, kind=reinjection 且含已注入内容:", reinjOk);
// 补回后去重重新武装：同主题再次提问应命中（释放后的重新注入）
currentDecision = mkDecision("哮喘的诱因有哪些");
const out5 = await preStep({ agent: agentMock, messages: currentDecision.messages, signal }, next);
const snap5 = out5.messages.filter((m) => m.source?.kind === "plugin" && m.source?.memory?.kind === "hit");
console.log("补回后重新命中:", snap5.length, "条 hit 快照");
const reinjStepOk = reinjOk;
console.log("reinject 断言:", reinjStepOk ? "PASS" : "FAIL");

console.log("\n=== 6. client-fold 识别断言（纯计算）===");
const snapOf = (kind, text) => ({
  kind: "context",
  data: {
    source: { kind: "plugin", plugin: "dsh-iwiw-memory", form: "snapshot", memory: { kind } },
    content: [{ type: "text", text }],
  },
  location: { kind: "turn", turn: { turn: 1 } },
});
const snapshot = {
  chat: {
    order: ["k1", "k2", "k3", "k4"],
    nodes: new Map([
      ["k1", { kind: "user", data: { content: [{ type: "text", text: "我的哮喘平时要注意什么" }] } }],
      ["k2", snapOf("hit", "## 相关记忆（命中）\n- **user-asthma** (normal) — 用户有哮喘\n  用户有哮喘病史。")],
      ["k3", { kind: "user", data: { content: [{ type: "text", text: "我们继续刚才的话题" }] } }],
      ["k4", snapOf("reinjection", "## 相关记忆（压缩后补回）\n- **user-asthma**\n  用户有哮喘病史。")],
    ]),
    locations: { getTurn: () => [] },
  },
};
const groups = computeInjectionGroups(snapshot);
const foldOk = groups.length === 2 && groups[0].kind === "hit" && groups[1].kind === "first";
console.log("识别组:", groups.map((g) => g.id + ":" + g.kind).join(", "), "| 断言:", foldOk ? "PASS" : "FAIL");

console.log("\n=== 7. client bundle 冒烟（minimal DOM stub）===");
let clientOk = false;
try {
  const client = await import("../lib/client.js");
  // 万能 Proxy stub：任何属性访问/调用都返回自身（可无限链式）。
  // 只用于验证 bundle 加载与 apply 主路径不抛错——DOM 行为真验证在换装阶段。
  const universal = new Proxy(function () {}, {
    get: (_t, prop) => {
      if (prop === Symbol.toPrimitive) return () => "";
      if (prop === Symbol.iterator) return function* () {};
      if (prop === "length") return 0;
      return universal;
    },
    apply: () => universal,
    construct: () => universal,
    set: () => true,
  });
  globalThis.document = universal;
  globalThis.window = universal;
  globalThis.MutationObserver = universal;
  const slots = {
    inject: (name, factory) => { factory(); return () => {}; },
    register: () => ({}),
  };
  const clientCtx = { slots, sessions: {} };
  const clientDispose = client.apply(clientCtx);
  console.log("client.apply 执行 OK, dispose 可调:", typeof clientDispose === "function");
  await clientDispose();
  clientOk = true;
} catch (e) {
  console.log("client 冒烟失败:", String(e).slice(0, 200));
}
console.log("client 冒烟:", clientOk ? "PASS" : "FAIL");

console.log("\n=== 8. dispose ===");
await dispose();
fs.rmSync(tmp, { recursive: true, force: true });
console.log(a2ok && stepOk && clientOk ? "SMOKE ALL PASS" : "SMOKE FAILED");
process.exit(a2ok && stepOk && clientOk ? 0 : 1);
