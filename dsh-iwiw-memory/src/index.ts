import { Context } from "@deepseek-ai/cordis";
import { defineTool } from "@deepseek-ai/dsh-tools";
import Schema from "@deepseek-ai/schemastery";
import { MemoryBackend } from "./backend.js";
import { MemoryTools, toTextBlocks } from "./tools.js";
import { coreSectionText, toolGuideSection, MEMORY_SECTION_NAME } from "./prompts.js";

/** dsh-iwiw-memory：IwIw 记忆内核的 DSH 插件 —— 跨会话记忆（模型自主工具化写入）。 */
export const name = "dsh-iwiw-memory";
/** 必须显式声明 host 端用到的 cordis 服务，否则 ctx 访问器会抛 "cannot get property ... without inject"。 */
export const inject: string[] = ["tools", "systemPrompt", "settings"];

interface PluginConfig {
  /** Python 解释器路径（含 mcp/jieba 依赖的 venv 或系统 Python）。 */
  python?: string;
  /** memory_agent 工作目录。 */
  cwd?: string;
  /** 常驻层注入段总字符预算（与内核 MEMORY_RECALL_MAX_CHARS 对齐）。 */
  coreMaxChars?: number;
  /** 每消息命中注入的条数上限（默认 3）。 */
  hitTopK?: number;
  /** 常驻层集合（模式配置，类型轴保持纯净）：这些 mem_type 全量注入；
   *  rules 层单独包装为「准则」段。chat 模式默认 ["profile","rules"]，dev 模式建议 ["rules"]。 */
  standingLayers?: string[];
  /** reflect steering：连续 N 个模型步未写入记忆后注入一次性回顾提示；0 关闭（默认 7）。 */
  reflectTurns?: number;
  /** 记忆巩固（consolidate）：空闲 N 分钟后触发（峰时抑制见 isPeakTime）；0 关闭（默认 180）。 */
  consolidateIdleMinutes?: number;
  /** 覆盖 MCP 子进程环境变量（如 MEMORY_AGENT_DB_PATH 指向隔离库）。 */
  env?: Record<string, string>;
}

const VALID_MEM_TYPES = new Set(["profile", "fact", "lesson", "rules", "project"]);
const DEFAULT_CORE_MAX_CHARS = 2500;

/** 峰时抑制：9-12 / 14-18 及各自前 15 分钟不触发巩固（避免打扰活跃时段）。 */
export function isPeakTime(d: Date): boolean {
  const h = d.getHours() + d.getMinutes() / 60;
  return (h >= 8.75 && h < 12) || (h >= 13.75 && h < 18);
}

function makeDefinition(tools: MemoryTools, onRemember?: () => void) {
  return {
    memory_remember: defineTool({
      name: "memory_remember",
      description: "把值得长期保存的稳定信息写入记忆，按内容类型选 level。写入即全文替换；新建前先 memory_search 查重。",
      parameters: {
        description: { type: "string", required: true, description: "一句话描述" },
        body: { type: "string", required: true, description: "完整正文（整体替换旧内容，不是追加）" },
        level: { type: "string", description: "记忆类型：profile=用户身份画像/健康/偏好；fact=一般事实（默认）；lesson=教训与经验；rules=用户要求持续遵守的准则；project=项目脉络与决策" },
        slug: { type: "string", description: "可选。更新已有记忆时填其 slug；新建建议用简短英文连字符命名，不填则自动生成" },
        priority: { type: "string", description: "active|archived，默认 active（在役）；archived=归档退役，一般不手动用" },
      },
      output: {
        schema: { type: "object", additionalProperties: false, properties: { result: { type: "json" } } },
        // 写入反馈人性化：✓ 已记住（类型）slug — 描述；带近似条目时提醒查重合并。
        render: (args, value) => {
          const r = (value as any)?.result ?? value;
          const a = args as any;
          if (r?.ok) {
            const lines = [`✓ 已记住（${r.level ?? a?.level ?? "fact"}）${r.slug}` + (a?.description ? ` — ${a.description}` : "")];
            if (Array.isArray(r.related) && r.related.length > 0) {
              lines.push(`⚠ 近似条目（如重复请带其 slug 更新合并）：` + r.related.map((x: any) => x?.slug ?? x).join("、"));
            }
            return toTextBlocks(lines.join("\n"));
          }
          return toTextBlocks(value);
        },
      },
      async execute(args) {
        const a = args as { description: string; body: string; level?: string; slug?: string; priority?: string };
        const out = { result: await tools.remember(a) };
        onRemember?.();
        return out;
      },
    }),
    memory_search: defineTool({
      name: "memory_search",
      description: "按关键词检索长期记忆（FTS 词面 + 同义词 + 会话联想）。涉及个人事实、计划、健康、关系、偏好时主动调用。",
      parameters: {
        query: { type: "string", required: true, description: "检索关键词" },
        top_k: { type: "integer", description: "返回条数（默认 5）" },
      },
      output: {
        schema: { type: "object", additionalProperties: false, properties: { results: { type: "json" } } },
        render: (_args, value) => toTextBlocks(value),
      },
      async execute(args) {
        const a = args as { query: string; top_k?: number };
        return { results: await tools.search({ query: a.query, top_k: a.top_k ?? 5 }) };
      },
    }),
    memory_read: defineTool({
      name: "memory_read",
      description: "读取一条记忆的完整正文。",
      parameters: {
        slug: { type: "string", required: true, description: "记忆 slug" },
      },
      output: {
        schema: { type: "object", additionalProperties: false, properties: { memory: { type: "json" } } },
        render: (_args, value) => toTextBlocks(value),
      },
      async execute(args) {
        const a = args as { slug: string };
        return { memory: await tools.read(a) };
      },
    }),
    memory_list: defineTool({
      name: "memory_list",
      description: "列出记忆条目（可按 active/archived 与类型过滤）。",
      parameters: {
        priority: { type: "string", description: "active|archived" },
        mem_type: { type: "string", description: "profile|fact|lesson|rules|project" },
        limit: { type: "integer", description: "条数（默认 20）" },
      },
      output: {
        schema: { type: "object", additionalProperties: false, properties: { items: { type: "json" } } },
        render: (_args, value) => toTextBlocks(value),
      },
      async execute(args) {
        const a = args as { priority?: string; mem_type?: string; limit?: number };
        return { items: await tools.list({ priority: a.priority, mem_type: a.mem_type, limit: a.limit ?? 20 }) };
      },
    }),
  } as const;
}

/** settings schema（schemastery 对象）：设置通道要求 schema 可 JSON 序列化——
 *  host describe 时序列化信封，渲染端 rehydrate+validate（纯函数会静默产出空镜像）。 */
export const SETTINGS_SCHEMA = Schema.object({
  hitTopK: Schema.number().default(3).description("每条消息命中注入条数上限"),
  coreMaxChars: Schema.number().default(2500).description("常驻记忆段字符预算"),
  reflectTurns: Schema.number().default(7).description("回顾提示触发步数，0=关闭"),
  consolidateIdleMinutes: Schema.number().default(180).description("空闲巩固阈值（分钟），0=关闭"),
  standingLayers: Schema.string().default("profile,rules").description("常驻记忆类型，逗号分隔"),
});

const SETTINGS_NS = "dsh-iwiw-memory";

export const apply = async (ctx: Context, config: PluginConfig = {}) => {
  // 部署级配置（不进设置页）：插件以 `python -m memory_agent.mcp_server` 拉起记忆内核，
  // cwd 必须指向 iwiw-memory 仓库根（含 memory_agent/）；缺失即失败并给出修复指引。
  const python = config.python ?? "python";
  if (!config.cwd) {
    throw new Error(
      "[dsh-iwiw-memory] 缺少部署配置 cwd：请在本机 profile 的 cordis.patch.yml 中为插件设置 "
      + "config.cwd（iwiw-memory 仓库根目录）与 config.python（Python 解释器路径，缺省取 PATH 上的 python）。",
    );
  }
  const cwd = config.cwd;
  // settings.yaml user 层可调字段：patch config 为初始值，settings 注册后被覆盖；
  // 消费点读 overrides（大部分字段热生效：下一步/下一轮巩固间隔即生效）。
  const overrides: Record<string, any> = {
    coreMaxChars: config.coreMaxChars ?? DEFAULT_CORE_MAX_CHARS,
    standingLayers: config.standingLayers ?? ["profile", "rules"],
    reflectTurns: config.reflectTurns ?? 7,
    consolidateIdleMinutes: config.consolidateIdleMinutes ?? 180,
    hitTopK: undefined as number | undefined,
  };
  overrides.hitTopK = config.hitTopK ?? 3;
  const standingLayers = (): string[] => (overrides.standingLayers as string[]).filter((t) => VALID_MEM_TYPES.has(t));
  const reflectTurns = (): number => overrides.reflectTurns as number;
  const hitTopK = (): number => overrides.hitTopK as number;
  ctx.inject(["settings"], (sctx: any) => {
    try {
      const scope = sctx.settings.register(
        SETTINGS_NS,
        SETTINGS_SCHEMA,
        { base: config },
      );
      const applySettings = (resolved: Record<string, unknown>): void => {
        for (const [k, v] of Object.entries(resolved)) {
          // standingLayers 存储为逗号分隔字符串，运行时消费需要数组
          overrides[k] = k === "standingLayers"
            ? String(v).split(",").map((s) => s.trim()).filter((t) => VALID_MEM_TYPES.has(t))
            : v;
        }
      };
      applySettings(scope.get());
      scope.watch(() => {
        applySettings(scope.get());
        ctx.logger.info(`[dsh-iwiw-memory] settings updated: ${JSON.stringify(overrides)}`);
      });
      ctx.logger.info("[dsh-iwiw-memory] settings section installed");
    } catch (e) {
      ctx.logger.warn("[dsh-iwiw-memory] settings registration failed (patch config in effect)", e);
    }
  });
  const backend = new MemoryBackend(python, ["-m", "memory_agent.mcp_server"], cwd, config.env);
  const tools = new MemoryTools(backend);

  // 1) 启动时预热后端（避免首次工具调用才连接）。
  try { await backend.callTool("list_memories", { limit: 1 }); } catch (e) { ctx.logger.warn("memory backend warmup failed", e); }

  // 2) 注册 4 个 memory_* 工具（与 CLI chat 同源）；remember 成功后刷新 core 段缓存（A2）+ reflect 计数归零。
  let stepSinceWrite = 0;
  const defs = makeDefinition(tools, () => { stepSinceWrite = 0; refreshCore().catch(() => {}); });
  const disposers = [
    ctx.tools.register(defs.memory_remember),
    ctx.tools.register(defs.memory_search),
    ctx.tools.register(defs.memory_read),
    ctx.tools.register(defs.memory_list),
  ];

  // 3) 静态工具使用提示。
  disposers.push(ctx.systemPrompt.section(toolGuideSection));

  // 4) 动态 core 段：standingLayers 各层（rules 除外）list + 逐条 read，字符预算内拼装。
  const fetchCoreText = async (): Promise<string> => {
    try {
      const layers = standingLayers().filter((t) => t !== "rules");
      let budget = overrides.coreMaxChars as number;
      const lines: string[] = [];
      for (const layer of layers) {
        // MCP list_memories 返回裸数组；execute 层的 {items} 包装不经过这里
        const raw = (await tools.list({ priority: "active", mem_type: layer, limit: 50 })) as
          | Array<{ slug: string; description: string }>
          | { items?: Array<{ slug: string; description: string }> };
        const items = Array.isArray(raw) ? raw : (raw?.items ?? []);
        for (const it of items) {
          if (budget <= 0) break;
          const mem = (await tools.read({ slug: it.slug })) as { content?: string } | null;
          let body = mem?.content ?? "";
          if (body.length > budget) body = body.slice(0, budget) + "...";
          budget -= body.length;
          lines.push(`- **${it.slug}** — ${it.description}`);
          lines.push(`  ${body}`);
        }
      }
      return lines.length > 0 ? lines.join("\n") : "（暂无必读长期记忆）";
    } catch (e) {
      ctx.logger.warn("fetch core memory failed", e);
      return `（core 记忆拉取失败：${String(e)}）`;
    }
  };
  // A2 一致性：core/rules 段缓存 + 写入后异步刷新（pending 重跑，刷新期间的请求不丢失）。
  let cachedCore = "";
  let cachedRules = "";
  let refreshing = false;
  let pending = false;
  const fetchRulesText = async (): Promise<string> => {
    // rules 类准则全量注入（archived 退役不注入）；未配置 rules 层则无准则段
    if (!standingLayers().includes("rules")) return "";
    try {
      const raw = (await tools.list({ priority: "active", mem_type: "rules", limit: 50 })) as
        | Array<{ slug: string; description: string }> | { items?: Array<{ slug: string; description: string }> };
      const items = Array.isArray(raw) ? raw : (raw?.items ?? []);
      if (items.length === 0) return "";
      const lines: string[] = [];
      for (const it of items) {
        const mem = (await tools.read({ slug: it.slug })) as { content?: string } | null;
        if (mem?.content) lines.push(`- ${mem.content}`);
      }
      return lines.length > 0 ? lines.join("\n") : "";
    } catch (e) {
      ctx.logger.warn("fetch rules failed", e);
      return "";
    }
  };
  const refreshCore = async () => {
    if (refreshing) { pending = true; return; }
    refreshing = true;
    try {
      do {
        pending = false;
        cachedCore = await fetchCoreText();
        cachedRules = await fetchRulesText();
      } while (pending);
    } finally { refreshing = false; }
  };
  refreshCore().catch(() => {});
  disposers.push(ctx.systemPrompt.section({
    name: MEMORY_SECTION_NAME,
    order: -50,
    text: () => cachedCore || "（core 记忆加载中…）",
  }));
  // rules 准则段：用户要求持续遵守的准则每轮生效
  disposers.push(ctx.systemPrompt.section({
    name: "iwiw-memory:rules",
    order: -45,
    text: () => {
      if (!cachedRules) return "";
      return [
        "## 准则（用户要求持续遵守）",
        "",
        "以下准则来自记忆库 rules 层，请在本会话中严格遵守：",
        "",
        cachedRules,
      ].join("\n");
    },
  }));

  // 5) 每消息命中注入（agent/pre-step）：最后一条 user 消息触发检索，
  //    命中且未注入过的记忆以快照消息插入其前。
  //    会话内去重：注入过的 slug 不再重复注入（compress 后由 reinject 补回）。
  //    检索带 session_id + context（内核 per-session SessionState，回指联想）
  //    并排除常驻层（已在 system 注入，命中注入防重复）。
  const injectedBySession = new Map<string, Set<string>>();
  // 压缩后补回：compaction 释放去重并快照本会话已注入 slugs，compaction/end 后 pre-step 补回
  const reinjectArmed = new Map<string, string[]>();
  // 活动时间戳：任何 pre-step 活动都刷新（供巩固空闲判定）
  const lastActivityAt = { value: Date.now() };
  let consolidateRunning = false;
  disposers.push(
    ctx.on("session/event", (session: any, event: any) => {
      const t = event?.type;
      const sid = typeof session?.id === "string" ? session.id : null;
      if (!sid) return;
      if (t === "compaction/start" || t === "compaction/summary") {
        const seen = injectedBySession.get(sid);
        if (seen?.size) {
          reinjectArmed.set(sid, [...seen]);
          injectedBySession.delete(sid);
          ctx.logger.info(`[dsh-iwiw-memory] compaction signal: released seen for session, ${seen.size} slugs armed`);
        }
        return;
      }
      if (t === "compaction/end") {
        const err = event?.data?.error;
        if ((err === undefined || err === null || err === "") && reinjectArmed.has(sid)) {
          ctx.logger.info("[dsh-iwiw-memory] compaction finished, re-injection armed");
        } else if (err) {
          reinjectArmed.delete(sid);
        }
      }
    }),
  );
  const snapshotMessage = (text: string, meta: { kind: string; ids: string[] }) => ({
    id: crypto.randomUUID(),
    role: "user",
    content: [{ type: "text", text }] as Array<{ type: string; text: string }>,
    source: {
      kind: "plugin",
      plugin: "dsh-iwiw-memory",
      form: "snapshot",
      memory: meta,
      sections: [{ name: "相关记忆", text }],
    },
  });
  disposers.push(
    ctx.on("agent/pre-step", async ({ agent, signal }: any, next: any) => {
      const decision = await next();
      if (decision === undefined || decision?.kind !== "enter" || signal?.aborted) return decision;
      if (!Array.isArray(decision.messages) || decision.messages.length === 0) return decision;
      if (agent?.session?.header?.origin === "subagent") return decision;
      lastActivityAt.value = Date.now();
      const lastUser = [...decision.messages].reverse().find((m: any) => m.source?.kind === "user");
      if (!lastUser) return decision;
      const text = (lastUser.content ?? [])
        .filter((b: any) => b?.type === "text" && typeof b.text === "string")
        .map((b: any) => b.text)
        .join(" ")
        .trim();
      if (text.length < 4) return decision;
      const sid = typeof agent?.session?.header?.id === "string" ? agent.session.header.id : "default";
      // ── 巩固报告：空闲巩固完成后下一次对话一次性告知 ──
      if (lastConsolidateReport.value) {
        const report = lastConsolidateReport.value;
        lastConsolidateReport.value = "";
        const rewritten = [...decision.messages];
        rewritten.splice(rewritten.indexOf(lastUser), 0, snapshotMessage(report, { kind: "consolidate-report", ids: [] }));
        ctx.logger.info("[dsh-iwiw-memory] consolidate report injected");
        return { ...decision, messages: rewritten };
      }
      // ── reflect steering：连续 N 步未写入 → 注入一次性回顾提示（优先于命中注入）──
      if (reflectTurns() > 0 && stepSinceWrite >= reflectTurns()) {
        stepSinceWrite = 0;
        const reflectText = [
          "## 会话回顾（reflect）",
          "",
          "最近多轮对话没有写入记忆。若此前对话出现值得长期保存的稳定事实、决策或偏好，请在本次回复前调用 memory_remember（新建前先 memory_search 查重）；确认没有则忽略本提示。",
        ].join("\n");
        const rewritten = [...decision.messages];
        rewritten.splice(rewritten.indexOf(lastUser), 0, snapshotMessage(reflectText, { kind: "reflect", ids: [] }));
        ctx.logger.info("[dsh-iwiw-memory] reflect steering injected");
        return { ...decision, messages: rewritten };
      }
      stepSinceWrite += 1;
      // ── 压缩后补回：本会话已注入过的记忆重新进入上下文 ──
      const pendingSlugs = reinjectArmed.get(sid);
      if (pendingSlugs?.length) {
        reinjectArmed.delete(sid);
        const lines = ["## 相关记忆（压缩后补回）", ""];
        for (const slug of pendingSlugs) {
          try {
            const mem = (await tools.read({ slug })) as { content?: string } | null;
            if (mem?.content) {
              lines.push(`- **${slug}**`);
              lines.push(`  ${mem.content}`);
            }
          } catch {}
        }
        if (lines.length > 2) {
          const rewritten = [...decision.messages];
          rewritten.splice(rewritten.indexOf(lastUser), 0, snapshotMessage(lines.join("\n"), { kind: "reinjection", ids: pendingSlugs }));
          ctx.logger.info(`[dsh-iwiw-memory] post-compaction re-injected: ${pendingSlugs.join(", ")}`);
          return { ...decision, messages: rewritten };
        }
      }
      // ── 命中注入：回指联想（session_id + context）+ 常驻层排除 ──
      try {
        const userTexts: string[] = [];
        for (const m of decision.messages) {
          if (m.source?.kind !== "user") continue;
          const t = (m.content ?? [])
            .filter((b: any) => b?.type === "text" && typeof b.text === "string")
            .map((b: any) => b.text)
            .join(" ")
            .trim();
          if (t) userTexts.push(t);
        }
        const context = userTexts.slice(-4, -1);
        const raw = await tools.search({
          query: text, top_k: hitTopK(), session_id: sid,
          context, exclude_mem_types: standingLayers(),
        });
        const hits = (Array.isArray(raw) ? raw : ((raw as any)?.hits ?? [])) as Array<{
          slug: string; description: string; priority: string; content: string;
        }>;
        const seen = injectedBySession.get(sid) ?? new Set<string>();
        const fresh = hits.filter((h) => h.slug && !seen.has(h.slug));
        if (fresh.length === 0) return decision;
        for (const h of fresh) seen.add(h.slug);
        injectedBySession.set(sid, seen);
        const lines = ["## 相关记忆（命中）", ""];
        for (const h of fresh) {
          lines.push(`- **${h.slug}** (${h.priority}) — ${h.description}`);
          lines.push(`  ${h.content}`);
        }
        const rewritten = [...decision.messages];
        rewritten.splice(rewritten.indexOf(lastUser), 0, snapshotMessage(lines.join("\n"), { kind: "hit", ids: fresh.map((h) => h.slug) }));
        // 使用强化：注入命中自增（fire-and-forget，不阻塞注入）
        void tools.touch(fresh.map((h) => h.slug)).catch(() => {});
        ctx.logger.info(`[dsh-iwiw-memory] hit injected: ${fresh.map((h) => h.slug).join(", ")}`);
        return { ...decision, messages: rewritten };
      } catch (e) {
        ctx.logger.warn("[dsh-iwiw-memory] hit injection failed", e);
        return decision;
      }
    }),
  );

  // 6) 记忆巩固（consolidate）：空闲 consolidateIdleMinutes 且非峰时 → 调内核 run_consolidate（低风险修正留痕自愈，归档进待审批）。
  //    结果暂存 lastConsolidateReport，下一次对话 pre-step 一次性注入。
  //    常驻定时器（10min 检查一次），每次读 overrides.consolidateIdleMinutes——settings 改空闲阈值热生效；设 0 即停。
  const lastConsolidateReport = { value: "" };
  const consolidateIdleMinutes = (): number => overrides.consolidateIdleMinutes as number;
  const consolidateTimer = consolidateIdleMinutes() > 0
    ? setInterval(() => {
        const idle = consolidateIdleMinutes();
        if (idle <= 0 || consolidateRunning) return;
        if (Date.now() - lastActivityAt.value < idle * 60_000) return;
        if (isPeakTime(new Date())) return;
        consolidateRunning = true;
        ctx.logger.info("[dsh-iwiw-memory] consolidate: idle threshold reached");
        tools.consolidate()
          .then((r: any) => {
            const d = (r && typeof r === "object") ? r as Record<string, any> : {};
            ctx.logger.info(
              `[dsh-iwiw-memory] consolidate done: reviewed=${d.reviewed ?? 0} auto_fixed=${d.auto_fixed?.length ?? 0}`
              + ` pending=${d.pending?.length ?? 0} suggestions=${d.suggestions?.length ?? 0}`
              + (d.error ? ` error=${d.error}` : ""),
            );
            const parts: string[] = [`审查了 ${d.reviewed ?? 0} 条近期记忆`];
            const fixed: string[] = d.auto_fixed ?? [];
            if (fixed.length) parts.push(`自动修正 ${fixed.length} 条（${fixed.map((f: any) => f?.slug).join("、")}）`);
            const pend: string[] = d.pending ?? [];
            if (pend.length) parts.push(`待审批归档 ${pend.length} 条`);
            const sugg: string[] = d.suggestions ?? [];
            if (sugg.length) parts.push(`合并建议 ${sugg.length} 条`);
            if (d.error) parts.push(`错误：${d.error}`);
            if (!fixed.length && !pend.length && !sugg.length && !d.error) parts.push("无需巩固");
            lastConsolidateReport.value = ["## 记忆巩固报告", "", "空闲期巩固完成：" + parts.join("；") + "。"].join("\n");
          })
          .catch((e) => ctx.logger.warn("[dsh-iwiw-memory] consolidate failed", e))
          .finally(() => {
            consolidateRunning = false;
            // 重置活动时间戳：下一轮巩固需重新积累完整空闲期
            lastActivityAt.value = Date.now();
          });
      }, 10 * 60_000)
    : null;

  ctx.logger.info("[dsh-iwiw-memory] applied: 4 tools + 2 prompt sections + pre-step hook (reflect/hit) + consolidation scheduler");

  return async () => {
    if (consolidateTimer) clearInterval(consolidateTimer);
    for (const d of disposers) { try { d(); } catch {} }
    await backend.close();
    ctx.logger.info("[dsh-iwiw-memory] disposed");
  };
};
