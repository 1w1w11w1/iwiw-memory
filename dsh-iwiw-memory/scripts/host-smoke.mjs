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
import { MemoryBackend } from "../lib/backend.js";

// B 管道捕获：截获 pre-step 发往 MCP 的 search_memories 调用参数
const searchCalls = [];
let memoryStatsCalls = 0;
const origCallTool = MemoryBackend.prototype.callTool;
MemoryBackend.prototype.callTool = async function (name, args, options) {
  if (name === "search_memories" && args?.session_id !== undefined) {
    searchCalls.push({ args: JSON.parse(JSON.stringify(args)) });
  }
  if (name === "memory_stats") memoryStatsCalls += 1;
  return origCallTool.call(this, name, args, options);
};

// 隔离库：显式经 config.env 传给 MCP 子进程（MCP SDK 不继承完整父进程 env）
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "iwiw-smoke-"));
const dbPath = path.join(tmp, "memory.db");

const python = process.env.SMOKE_PYTHON ?? "E:/desktop/111/.venv/Scripts/python.exe";
const cwd = process.env.SMOKE_CWD ?? "E:/desktop/111";

const registered = [];
const sections = [];
const handlers = {};
// settings 服务 mock：与 dsh-settings 的 register 契约同形（schema 归一化 + get/watch）
const makeSettingsService = (userByNs = {}) => ({
  registered: {},
  register(ns, schema, options) {
    if (this.registered[ns]) throw new Error(`settings namespace "${ns}" is already registered`);
    const base = options?.base ?? {};
    const user = userByNs[ns] ?? {};
    const resolved = schema({ ...base, ...user });
    const entry = { ns, schema, base, user, resolved, watchers: new Set() };
    this.registered[ns] = entry;
    return {
      get: () => entry.resolved,
      watch: (cb) => { entry.watchers.add(cb); return () => entry.watchers.delete(cb); },
    };
  },
  describe() {
    return Object.values(this.registered).map((entry) => ({
      ns: entry.ns, value: entry.resolved, base: entry.base, user: entry.user,
    }));
  },
});
const makeCtx = (handlersMap = handlers, userByNs = {}) => {
  const settingsService = makeSettingsService(userByNs);
  return {
    settingsService,
    settings: settingsService,
    logger: {
      info: (...a) => console.log("[info]", ...a),
      warn: (...a) => console.log("[warn]", ...a),
      error: (...a) => console.log("[error]", ...a),
    },
    tools: { register: (def) => { registered.push(def); return () => {}; } },
    systemPrompt: { section: (s) => { sections.push(s); return () => {}; } },
    on: (event, handler) => { (handlersMap[event] ??= []).push(handler); return () => {}; },
    inject: (services, cb) => {
      if (services.includes("settings")) cb({ settings: settingsService });
      return () => {};
    },
  };
};
const ctx = makeCtx();

console.log("=== 1. apply ===");
const dispose = await apply(ctx, { python, cwd, env: { MEMORY_AGENT_DB_PATH: dbPath } });

console.log("\n=== 1.5 settings section 注册 ===");
const settingsEntry = ctx.settingsService.registered["dsh-iwiw-memory"];
const settingsOk = !!settingsEntry
  && settingsEntry.resolved.hitTopK === 3
  && settingsEntry.resolved.reflectTurns === 7
  && settingsEntry.resolved.startupConsolidate === true
  && settingsEntry.resolved.standingLayers === "profile,rules";
console.log("settings 注册断言:", settingsOk ? "PASS" : "FAIL", JSON.stringify(settingsEntry?.resolved));

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
  level: "profile",
});
const slug = r1?.result?.slug;
console.log("memory_remember →", JSON.stringify(r1).slice(0, 200));

const r2 = await byName.memory_search.execute({ query: "花生 过敏" });
console.log("memory_search →", JSON.stringify(r2).slice(0, 200));

const r3 = await byName.memory_read.execute({ slug });
console.log("memory_read →", JSON.stringify(r3).slice(0, 160));

const r4 = await byName.memory_list.execute({ priority: "active", limit: 10 });
console.log("memory_list →", JSON.stringify(r4).slice(0, 200));

console.log("\n=== 4. core 注入段刷新验证（A2）===");
await new Promise((res) => setTimeout(res, 600));
const refreshed = coreText();
console.log("core 刷新后:", refreshed.slice(0, 300));
const a2ok = refreshed.includes(slug);
console.log("A2 写入可见:", a2ok ? "PASS" : "FAIL");

console.log("\n=== 4.5 rules 准则段（双轴分类）===");
const rRules = await byName.memory_remember.execute({
  description: "体验优先、简洁有效的规则",
  body: "体验感是第一目标；拒收因子堆叠，排序公式保持两因子。",
  level: "rules",
});
console.log("rules 写入:", JSON.stringify(rRules).slice(0, 160));
await new Promise((res) => setTimeout(res, 600));
const rulesSection = sections.find((sec) => sec.name === "iwiw-memory:rules");
const rulesText = rulesSection ? (typeof rulesSection.text === "function" ? rulesSection.text() : rulesSection.text) : "";
const rulesOk = rulesText.includes("准则") && rulesText.includes("因子堆叠");
console.log("rules 段文本:", JSON.stringify(rulesText.slice(0, 160)));
console.log("rules 注入断言:", rulesOk ? "PASS" : "FAIL");
// 判别调试：MCP 数据 vs 缓存链路
const recheck = await byName.memory_list.execute({ priority: "active" });
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
  priority: "active",
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
currentDecision = mkDecision("今天天气怎么样");
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
// 补回断言：本会话注入过的记忆（哮喘）应补回；花生（profile）从不进 hit 注入，不含属预期（A 生效）
const reinj = snap4.find((m) => m.source?.memory?.kind === "reinjection");
const reinjOk = !!reinj && JSON.stringify(reinj).includes("哮喘") && !JSON.stringify(reinj).includes("花生");
console.log("补回快照:", snap4.length, "条, kind=reinjection 且含已注入内容:", reinjOk);
// 补回后去重重新武装：同主题再次提问应命中（释放后的重新注入）
currentDecision = mkDecision("哮喘的诱因有哪些");
const out5 = await preStep({ agent: agentMock, messages: currentDecision.messages, signal }, next);
const snap5 = out5.messages.filter((m) => m.source?.kind === "plugin" && m.source?.memory?.kind === "hit");
console.log("补回后重新命中:", snap5.length, "条 hit 快照");
const reinjStepOk = reinjOk;
console.log("reinject 断言:", reinjStepOk ? "PASS" : "FAIL");

// A 断言：常驻层（profile 花生过敏）不应通过 hit 注入重复（system core 段已有）；
// 双消息 decision 同时验证 context 携带（B）
const multiDecision = {
  kind: "enter",
  messages: [
    { id: "u-prev", role: "user", source: { kind: "user" }, content: [{ type: "text", text: "之前我们聊过哮喘的事情" }] },
    { id: "u-cur", role: "user", source: { kind: "user" }, content: [{ type: "text", text: "花生过敏能吃什么药" }] },
  ],
};
currentDecision = multiDecision;
const outA = await preStep({ agent: agentMock, messages: currentDecision.messages, signal }, next);
const snapA = outA.messages.filter((m) => m.source?.kind === "plugin");
const excludeOk = snapA.every((m) => !JSON.stringify(m).includes("花生"));
console.log("A 常驻层排除:", snapA.length, "条快照, 不含 profile 重复:", excludeOk);

// B 断言：search 调用携带 session_id / exclude_mem_types；多轮 decision 时携带非空 context
const searchArgsOk = searchCalls.length > 0
  && searchCalls.every((c) =>
    typeof c.args.session_id === "string"
    && Array.isArray(c.args.exclude_mem_types)
    && c.args.exclude_mem_types.includes("profile"))
  && searchCalls.some((c) => Array.isArray(c.args.context) && c.args.context.length > 0);
console.log(`B 回指管道: 捕获 ${searchCalls.length} 次 search, 参数断言:`, searchArgsOk ? "PASS" : "FAIL",
  searchCalls[0] ? JSON.stringify({ session_id: searchCalls[0].args.session_id, exclude: searchCalls[0].args.exclude_mem_types, context_len: searchCalls[0].args.context?.length }) : "");

console.log("\n=== 5.7 reflect steering（独立 apply 实例，reflectTurns=2）===");
let reflectOk = false;
{
  const registered2 = [];
  const handlers2 = {};
  const ctx2 = makeCtx(handlers2);
  ctx2.logger = { info: () => {}, warn: (...a) => console.log("[warn2]", ...a), error: (...a) => console.log("[error2]", ...a) };
  ctx2.tools = { register: (def) => { registered2.push(def); return () => {}; } };
  ctx2.systemPrompt = { section: () => () => {} };
  const dispose2 = await apply(ctx2, { python, cwd, env: { MEMORY_AGENT_DB_PATH: dbPath }, reflectTurns: 2, startupConsolidate: false });
  const preStep2 = (handlers2["agent/pre-step"] ?? [])[0];
  const agent2 = { session: { header: { id: "reflect-sess", origin: "main" } } };
  const run2 = async (text) => {
    currentDecision = mkDecision(text);
    return preStep2({ agent: agent2, messages: currentDecision.messages, signal }, next);
  };
  const s1 = await run2("问题一：今天中午吃什么好");
  const s2 = await run2("问题二：外面天气怎么样");
  const s3 = await run2("问题三：帮我汇总一下情况");
  const reflectSnaps = s3.messages.filter((m) => m.source?.memory?.kind === "reflect");
  const noEarly = !s1.messages.some((m) => m.source?.memory?.kind === "reflect")
    && !s2.messages.some((m) => m.source?.memory?.kind === "reflect");
  reflectOk = reflectSnaps.length === 1 && noEarly && JSON.stringify(reflectSnaps[0]).includes("memory_remember");
  console.log("reflect 断言（前两步不触发，第三步触发）:", reflectOk ? "PASS" : "FAIL");
  await dispose2();
}

console.log("\n=== 5.8 启动补账（真实 domain + 会话读取 stub）===");
let startupOk = false;
{
  const { runStartup, StateSchema, INITIAL_STATE, scopeKeyOf, completedInWindow } = await import("../lib/startup.js");

  // 纯函数：只有 turn/end + completed 且落在 (from, to] 才算完成轮次
  const ev = (type, kind, time) => ({ type, data: kind ? { reason: { kind } } : {}, time });
  const pureOk = completedInWindow(ev("turn/end", "completed", 200), 100, 300) === true
    && completedInWindow(ev("turn/end", "aborted", 200), 100, 300) === false
    && completedInWindow(ev("turn/end", "interrupted", 200), 100, 300) === false
    && completedInWindow(ev("turn/end", "completed", 100), 100, 300) === false   // 左开
    && completedInWindow(ev("turn/end", "completed", 300), 100, 300) === true    // 右闭
    && completedInWindow(ev("step/end", null, 200), 100, 300) === false;
  const startupChecks = [pureOk];
  console.log("completedInWindow 边界断言:", pureOk ? "PASS" : "FAIL");

  // 真实 storage-domain 风格的 mock：状态持久在闭包里，验证水位真的被推进
  const makeDomain = () => {
    let value = structuredClone(INITIAL_STATE);
    const writes = [];
    return {
      writes,
      get value() { return value; },
      domain: {
        global: {
          get: () => value,
          set: async (next) => { value = StateSchema.parse(next); writes.push(structuredClone(next)); },
        },
        close: async () => {},
      },
    };
  };
  const storageDomain = { open: async () => null };
  const sessionQuery = { listSessions: async () => [], readSession: async () => ({ events: [], inheritedEventCount: 0 }) };
  const sessionPersistence = { resolveCurrentLog: async () => undefined };

  const logs = [];
  const log = { info: (m) => logs.push(m), warn: (m) => logs.push("WARN " + m) };
  const signal = new AbortController().signal;

  // 场景 1：首次启动 → 只写基线水位，不列会话、不调 LLM
  {
    const store = makeDomain();
    storageDomain.open = async () => store.domain;
    let listed = 0;
    const query = { listSessions: async () => { listed += 1; return []; }, readSession: async () => ({ events: [], inheritedEventCount: 0 }) };
    let consolidateCalls = 0;
    const out = await runStartup(
      { storageDomain, sessionQuery: query, sessionPersistence },
      {
        getDatabaseId: async () => "db-abc",
        consolidate: async () => { consolidateCalls += 1; return { ok: true, reviewed: 0 }; },
        report: () => {}, log, signal, now: () => 1000,
      },
    );
    const firstOk = out.status === "first-run" && listed === 0 && consolidateCalls === 0
      && store.value.lastRun === 1000 && store.value.lastConsolidatedAt === 1000;
    startupChecks.push(firstOk);
    console.log("场景1 首次启动（零会话/零LLM）:", firstOk ? "PASS" : "FAIL", JSON.stringify(out));
  }

  // 场景 2：无新完成轮次 → 只推进 lastRun，lastConsolidatedAt 不动
  {
    const store = makeDomain();
    await store.domain.global.set({ lastRun: 1000, lastConsolidatedAt: 1000 });
    storageDomain.open = async () => store.domain;
    const query = {
      listSessions: async () => [{ header: { id: "s1", origin: "main" }, live: false }],
      readSession: async () => ({ events: [], inheritedEventCount: 0 }),
    };
    let consolidateCalls = 0;
    const out = await runStartup(
      { storageDomain, sessionQuery: query, sessionPersistence },
      {
        getDatabaseId: async () => "db-abc",
        consolidate: async () => { consolidateCalls += 1; return { ok: true }; },
        report: () => {}, log, signal, now: () => 2000,
      },
    );
    // resolveCurrentLog 返回 undefined → 不做 mtime 粗筛，仍由 readSession 完整读取。
    const noopOk = out.status === "noop" && store.value.lastRun === 2000
      && store.value.lastConsolidatedAt === 1000 && consolidateCalls === 0;
    startupChecks.push(noopOk);
    console.log("场景2 无完成轮次（仅推 lastRun）:", noopOk ? "PASS" : "FAIL", JSON.stringify(out));
  }

  // 场景 3：live 会话有 completed → 触发一次窗口整理并推进两个水位
  {
    const store = makeDomain();
    await store.domain.global.set({ lastRun: 1000, lastConsolidatedAt: 1000 });
    storageDomain.open = async () => store.domain;
    const query = {
      listSessions: async () => [{ header: { id: "s2", origin: "main" }, live: true }],
      readSession: async () => ({
        inheritedEventCount: 0,
        events: [
          { type: "turn/start", time: 1500, data: {} },
          { type: "turn/end", time: 1600, data: { reason: { kind: "completed" } } },
          { type: "turn/end", time: 1700, data: { reason: { kind: "aborted" } } }, // 不算
        ],
      }),
    };
    const windows = [];
    const out = await runStartup(
      { storageDomain, sessionQuery: query, sessionPersistence },
      {
        getDatabaseId: async () => "db-abc",
        consolidate: async (since, until) => { windows.push([since, until]); return { ok: true, reviewed: 3 }; },
        report: (t) => logs.push(t), log, signal, now: () => 2000,
      },
    );
    const ok3 = out.status === "consolidated" && out.completedTurns === 1
      && windows.length === 1 && windows[0][0] === 1000 && windows[0][1] === 2000
      && store.value.lastRun === 2000 && store.value.lastConsolidatedAt === 2000
      && out.reviewed === 3;
    startupChecks.push(ok3);
    console.log("场景3 有完成轮次（固定窗口+双水位）:", ok3 ? "PASS" : "FAIL", JSON.stringify(out), JSON.stringify(windows));
  }

  // 场景 4：整理失败 → 不推进水位（下次按旧水位重试）
  {
    const store = makeDomain();
    await store.domain.global.set({ lastRun: 1000, lastConsolidatedAt: 1000 });
    storageDomain.open = async () => store.domain;
    const query = {
      listSessions: async () => [{ header: { id: "s3", origin: "main" }, live: true }],
      readSession: async () => ({
        inheritedEventCount: 0,
        events: [{ type: "turn/end", time: 1600, data: { reason: { kind: "completed" } } }],
      }),
    };
    const out = await runStartup(
      { storageDomain, sessionQuery: query, sessionPersistence },
      {
        getDatabaseId: async () => "db-abc",
        consolidate: async () => ({ ok: false, error: "llm exploded" }),
        report: () => {}, log, signal, now: () => 2000,
      },
    );
    const ok4 = out.status === "failed" && store.value.lastRun === 1000 && store.value.lastConsolidatedAt === 1000;
    startupChecks.push(ok4);
    console.log("场景4 整理失败不推水位:", ok4 ? "PASS" : "FAIL", JSON.stringify(out));
  }

  // 场景 5：时钟回拨 → clock-regressed，不降水位
  {
    const store = makeDomain();
    await store.domain.global.set({ lastRun: 5000, lastConsolidatedAt: 5000 });
    storageDomain.open = async () => store.domain;
    let consolidateCalls = 0;
    const out = await runStartup(
      { storageDomain, sessionQuery, sessionPersistence },
      {
        getDatabaseId: async () => "db-abc",
        consolidate: async () => { consolidateCalls += 1; return { ok: true }; },
        report: () => {}, log, signal, now: () => 1000,
      },
    );
    const ok5 = out.status === "clock-regressed" && store.value.lastRun === 5000 && consolidateCalls === 0;
    startupChecks.push(ok5);
    console.log("场景5 时钟回拨不降水位:", ok5 ? "PASS" : "FAIL", JSON.stringify(out));
  }

  // 场景 6：服务缺失 → disabled，不写假水位
  {
    const out = await runStartup(
      { sessionQuery: null, sessionPersistence: null, storageDomain: null },
      {
        getDatabaseId: async () => "db-abc",
        consolidate: async () => ({ ok: true }),
        report: () => {}, log, signal, now: () => 1000,
      },
    );
    const ok6 = out.status === "disabled" && !!out.error;
    startupChecks.push(ok6);
    console.log("场景6 服务缺失降级:", ok6 ? "PASS" : "FAIL", JSON.stringify(out));
  }

  // 场景 7：后端没有 database_id → 停用（不冒险用错误锁 key）
  {
    const out = await runStartup(
      { storageDomain, sessionQuery, sessionPersistence },
      {
        getDatabaseId: async () => null,
        consolidate: async () => ({ ok: true }),
        report: () => {}, log, signal, now: () => 1000,
      },
    );
    const ok7 = out.status === "disabled" && out.error === "no database_id";
    startupChecks.push(ok7);
    console.log("场景7 无 database_id 停用:", ok7 ? "PASS" : "FAIL", JSON.stringify(out));
  }

  // 场景 8：跨进程占位锁真实互斥
  {
    const { acquireStartupLock, releaseStartupLock } = await import("../lib/startup.js");
    const key = scopeKeyOf("lock-probe-" + Date.now());
    const a = await acquireStartupLock(key);
    const b = await acquireStartupLock(key);
    await releaseStartupLock(a);
    const c = await acquireStartupLock(key);
    await releaseStartupLock(c);
    const ok8 = a !== null && b === null && c !== null;
    startupChecks.push(ok8);
    console.log("场景8 占位锁互斥:", ok8 ? "PASS" : "FAIL", JSON.stringify({ a: !!a, b: !!b, c: !!c }));
  }

  // 场景 9：任一候选会话读取失败 → 整次失败，双水位保持原值
  {
    const store = makeDomain();
    await store.domain.global.set({ lastRun: 1000, lastConsolidatedAt: 1000 });
    storageDomain.open = async () => store.domain;
    const query = {
      listSessions: async () => [{ header: { id: "broken", origin: "main" }, live: false }],
      readSession: async () => { throw new Error("read failed"); },
    };
    const out = await runStartup(
      { storageDomain, sessionQuery: query, sessionPersistence },
      {
        getDatabaseId: async () => "db-abc",
        consolidate: async () => ({ ok: true }),
        report: () => {}, log, signal, now: () => 2000,
      },
    );
    const ok9 = out.status === "failed"
      && store.value.lastRun === 1000
      && store.value.lastConsolidatedAt === 1000;
    startupChecks.push(ok9);
    console.log("场景9 会话读取失败不推水位:", ok9 ? "PASS" : "FAIL", JSON.stringify(out));
  }

  // 场景 10：真实 apply 关闭启动补账 → 不打开 storage domain
  {
    let opened = 0;
    let childDispose;
    const statsBefore = memoryStatsCalls;
    const ctxDisabled = makeCtx({}, {
      "dsh-iwiw-memory": { startupConsolidate: false },
    });
    ctxDisabled.tools = { register: () => () => {} };
    ctxDisabled.systemPrompt = { section: () => () => {} };
    ctxDisabled.inject = (services, cb) => {
      if (services.includes("storageDomain")) {
        childDispose = cb({
          storageDomain: { open: async () => { opened += 1; throw new Error("must not open"); } },
          sessionQuery: { listSessions: async () => [], readSession: async () => ({ events: [], inheritedEventCount: 0 }) },
          sessionPersistence: { resolveCurrentLog: async () => undefined },
        });
      }
      return () => {};
    };
    const disposeDisabled = await apply(ctxDisabled, {
      python, cwd, env: { MEMORY_AGENT_DB_PATH: dbPath }, consolidateIdleMinutes: 180,
    });
    await disposeDisabled();
    const ok10 = opened === 0 && memoryStatsCalls === statsBefore && childDispose === undefined;
    startupChecks.push(ok10);
    console.log("场景10 新关闭设置优先于旧正数:", ok10 ? "PASS" : "FAIL", JSON.stringify({ opened, statsCalls: memoryStatsCalls - statsBefore }));
  }

  // 场景 11：旧 user 配置 0 仍映射为关闭
  {
    let opened = 0;
    const statsBefore = memoryStatsCalls;
    const ctxLegacyDisabled = makeCtx({}, {
      "dsh-iwiw-memory": { consolidateIdleMinutes: 0 },
    });
    ctxLegacyDisabled.tools = { register: () => () => {} };
    ctxLegacyDisabled.systemPrompt = { section: () => () => {} };
    ctxLegacyDisabled.inject = (services, cb) => {
      if (services.includes("storageDomain")) {
        cb({
          storageDomain: { open: async () => { opened += 1; throw new Error("must not open"); } },
          sessionQuery: { listSessions: async () => [], readSession: async () => ({ events: [], inheritedEventCount: 0 }) },
          sessionPersistence: { resolveCurrentLog: async () => undefined },
        });
      }
      return () => {};
    };
    const disposeLegacyDisabled = await apply(ctxLegacyDisabled, {
      python, cwd, env: { MEMORY_AGENT_DB_PATH: dbPath },
    });
    await disposeLegacyDisabled();
    const ok11 = opened === 0 && memoryStatsCalls === statsBefore;
    startupChecks.push(ok11);
    console.log("场景11 旧关闭设置仍生效:", ok11 ? "PASS" : "FAIL", JSON.stringify({ opened, statsCalls: memoryStatsCalls - statsBefore }));
  }

  // 场景 12：首次启动已取消 → 不打开 domain、不写基线
  {
    let opened = 0;
    const controller = new AbortController();
    controller.abort();
    const out = await runStartup(
      {
        storageDomain: { open: async () => { opened += 1; return makeDomain().domain; } },
        sessionQuery,
        sessionPersistence,
      },
      {
        getDatabaseId: async () => "db-abc",
        consolidate: async () => ({ ok: true }),
        report: () => {}, log, signal: controller.signal, now: () => 1000,
      },
    );
    const ok12 = out.status === "failed" && opened === 0;
    startupChecks.push(ok12);
    console.log("场景12 预取消不写基线:", ok12 ? "PASS" : "FAIL", JSON.stringify({ out, opened }));
  }

  // 场景 13：最后一次读取后取消 → noop 也不得推进水位
  {
    const store = makeDomain();
    await store.domain.global.set({ lastRun: 1000, lastConsolidatedAt: 1000 });
    storageDomain.open = async () => store.domain;
    const controller = new AbortController();
    const query = {
      listSessions: async () => [{ header: { id: "cancel-after-read", origin: "main" }, live: true }],
      readSession: async () => {
        setImmediate(() => controller.abort());
        return { events: [], inheritedEventCount: 0 };
      },
    };
    const out = await runStartup(
      { storageDomain, sessionQuery: query, sessionPersistence },
      {
        getDatabaseId: async () => "db-abc",
        consolidate: async () => ({ ok: true }),
        report: () => {}, log, signal: controller.signal, now: () => 2000,
      },
    );
    const ok13 = out.status === "failed"
      && store.value.lastRun === 1000
      && store.value.lastConsolidatedAt === 1000;
    startupChecks.push(ok13);
    console.log("场景13 读取后取消不推水位:", ok13 ? "PASS" : "FAIL", JSON.stringify(out));
  }

  // 场景 14：显式新开启优先于旧 0，且可选服务绑定返回可等待 disposer
  {
    let opened = 0;
    let childDispose;
    const statsBefore = memoryStatsCalls;
    const ctxEnabled = makeCtx({}, {
      "dsh-iwiw-memory": { startupConsolidate: true },
    });
    ctxEnabled.tools = { register: () => () => {} };
    ctxEnabled.systemPrompt = { section: () => () => {} };
    ctxEnabled.inject = (services, cb) => {
      if (services.includes("storageDomain")) {
        const store = makeDomain();
        childDispose = cb({
          storageDomain: { open: async () => { opened += 1; return store.domain; } },
          sessionQuery: { listSessions: async () => [], readSession: async () => ({ events: [], inheritedEventCount: 0 }) },
          sessionPersistence: { resolveCurrentLog: async () => undefined },
        });
      }
      return () => {};
    };
    const disposeEnabled = await apply(ctxEnabled, {
      python, cwd, env: { MEMORY_AGENT_DB_PATH: dbPath }, consolidateIdleMinutes: 0,
    });
    for (let i = 0; i < 50 && opened === 0; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    if (typeof childDispose === "function") await childDispose();
    await disposeEnabled();
    const ok14 = opened === 1
      && memoryStatsCalls === statsBefore + 1
      && typeof childDispose === "function";
    startupChecks.push(ok14);
    console.log("场景14 新开启设置优先且绑定可取消:", ok14 ? "PASS" : "FAIL", JSON.stringify({ opened, statsCalls: memoryStatsCalls - statsBefore, disposer: typeof childDispose }));
  }

  startupOk = startupChecks.every(Boolean);
}
console.log("启动补账断言:", startupOk ? "PASS" : "FAIL");

console.log("\n=== 6. client bundle 冒烟（minimal DOM stub）===");
let clientOk = false;
try {
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
  globalThis.MutationObserver = universal;
  // 模拟 DSH 渲染端 __ModuleLoader__：求值时注册，factory 立即执行并捕获 cjs 导出；
  // factory 内 require("react") 由宿主提供，这里用 universal stub 顶替。
  let clientExports = null;
  let registeredId = null;
  const loader = {
    load: (entry) => {
      registeredId = entry.id;
      clientExports = entry.factory((spec) => {
        if (spec === "react") return universal;
        throw new Error("unexpected client require: " + spec);
      });
    },
  };
  // 真实 window 只保证 __ModuleLoader__，其余浏览器 API 用 universal 兜底
  globalThis.window = new Proxy({ __ModuleLoader__: loader }, {
    get: (t, prop) => (prop in t ? t[prop] : universal),
  });
  await import("../lib/client.js");
  if (registeredId !== "@iwiw/dsh-iwiw-memory") {
    throw new Error("client bundle 未通过 __ModuleLoader__ 自注册，id=" + registeredId);
  }
  const client = clientExports;
  if (typeof client?.apply !== "function") throw new Error("client factory 导出缺少 apply");
  const slots = {
    inject: (name, factory) => { factory(); return () => {}; },
    register: () => ({}),
  };
  const clientCtx = { slots };
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
const allOk = settingsOk && a2ok && stepOk && reinjStepOk && excludeOk && searchArgsOk && reflectOk && startupOk && clientOk;
console.log(allOk ? "SMOKE ALL PASS" : "SMOKE FAILED");
process.exit(allOk ? 0 : 1);
