import { homedir } from "node:os";
import { join } from "node:path";
import { Context } from "@deepseek-ai/cordis";
import { defineTool } from "@deepseek-ai/dsh-tools";
import Schema from "@deepseek-ai/schemastery";
import { MemoryBackend } from "./backend.js";
import { MemoryTools, toTextBlocks } from "./tools.js";
import { iwiwSections, stripRetrievalFields } from "./prompts.js";
import { registerCaptureCommands } from "./capture.js";
import { runStartup, type StartupOutcome } from "./startup.js";

/** dsh-iwiw-memory：IwIw 记忆内核的 DSH 插件 —— 跨会话记忆（模型自主工具化写入）。 */
export const name = "dsh-iwiw-memory";
/** 必须显式声明 host 端用到的 cordis 服务，否则 ctx 访问器会抛 "cannot get property ... without inject"。 */
export const inject: string[] = ["tools", "systemPrompt", "settings", "commands"];

interface PluginConfig {
  /** Python 解释器路径（含 mcp/jieba 依赖的 venv 或系统 Python）。 */
  python?: string;
  /** memory_agent 工作目录。 */
  cwd?: string;
  /** core 注入段总字符预算（与内核 MEMORY_RECALL_MAX_CHARS 对齐）。 */
  coreMaxChars?: number;
  /** 每消息命中注入的条数上限（默认 3）。 */
  hitTopK?: number;
  /** 常驻层集合（模式配置，类型轴保持纯净）：这些 mem_type 全量注入；
   *  rules 层单独包装为「准则」段。chat 模式默认 ["profile","rules"]，dev 模式建议 ["rules"]。 */
  standingLayers?: string[];
  /** reflect steering：连续 N 个模型步未写入记忆后注入一次性回顾提示；0 关闭（默认 7）。 */
  reflectTurns?: number;
  /** 已弃用：旧的空闲巩固阈值。仅用于兼容读取（0 → 关闭启动补账），不再生效。 */
  consolidateIdleMinutes?: number;
  /** 启动时自动补账（替代旧的空闲轮询）。默认 true。 */
  startupConsolidate?: boolean;
  /** 覆盖 MCP 子进程环境变量（如 MEMORY_AGENT_DB_PATH 指向隔离库）。 */
  env?: Record<string, string>;
  /** 当前 DSH profile 目录（/memo 解析 DSH 内部导出包用）。
   *  缺省时用 DSH_HOME/profiles/web——web profile 是本插件的挂载点。 */
  profileDir?: string;
  /** 项目标识口径：默认取会话 cwd 的末段目录名（见 projectOf）。
   *  同一份记忆库被多个工作区共用时，project 型记忆按它隔离；
   *  想手工固定口径（如统一写 "dsh"）就在这里覆盖。 */
  projectTag?: string;
}

const VALID_MEM_TYPES = new Set(["profile", "fact", "lesson", "rules", "project"]);
const DEFAULT_CORE_MAX_CHARS = 2500;

/** 注入 header 的记录时间后缀：直接来自后端落库字段（recorded_date），模型不生成、不复述。
 *  只呈现"何时记下"这一确定事实，缺席时不加后缀。 */
function recordedSuffix(recordedDate: unknown): string {
  const stamp = typeof recordedDate === "string" ? recordedDate.trim() : "";
  if (!stamp) return "";
  return ` · 记录于 ${stamp.slice(0, 16).replace("T", " ")}`;
}

/**
 * 当前会话的项目标识 —— project 型记忆的隔离键。
 *
 * 为什么是 cwd 末段目录名：与用户心智一致（"我在哪个仓库里"），且不依赖
 * 任何额外注册表。同一目录改名/移动会改变标识，这是可接受的：标识只用于
 * 过滤"这条 project 记忆跟当前仓库有关吗"，不参与任何持久引用。
 * projectTag 配置可整体覆盖（同一库被多工作区共用、或想让某个仓库的记忆
 * 对多个目录都可见时用）。
 */
function projectOf(cwd: unknown, override?: string): string | null {
  const tag = (override ?? "").trim();
  if (tag) return tag;
  const p = typeof cwd === "string" ? cwd.trim() : "";
  if (!p) return null;
  const parts = p.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? null;
}

function makeDefinition(tools: MemoryTools, onRemember?: () => void, projectTag?: string) {
  return {
    memory_remember: defineTool({
      name: "memory_remember",
      description:
        "把值得长期保存的稳定信息写入记忆，按内容类型选 level。写入即全文替换；新建前先 memory_search 查重。" +
        "不要写入凭据类内容（API key/token/密码/私钥/连接串口令），需要记录配置位置时写占位符而非真值；" +
        "公网 IP 只记用途，本机与内网地址可保留。写入前内核会确定性脱敏，命中项在返回里告知。",
      parameters: {
        description: { type: "string", required: true, description: "一句话描述" },
        body: { type: "string", required: true, description: "完整正文（整体替换旧内容，不是追加）" },
        level: { type: "string", description: "记忆类型：profile=用户身份画像/健康/偏好；fact=一般事实（默认）；lesson=教训与经验；rules=准则；project=项目脉络与决策" },
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
            if (r.redacted) {
              lines.push(`🔒 已脱敏（未落库）：${r.redacted}；如需保留配置线索请改记占位符`);
            }
            return toTextBlocks(lines.join("\n"));
          }
          return toTextBlocks(value);
        },
      },
      async execute(args, exec) {
        const a = args as { description: string; body: string; level?: string; slug?: string; priority?: string };
        // exec.agent 在 PTC 嵌套派发里也会被传播（dsh-tools 的 run_code binding 显式转发），
        // 所以写入路径能和 pre-step 注入路径拿到同一个项目标识。
        const project = projectOf((exec as any)?.agent?.session?.header?.cwd, projectTag);
        const out = { result: await tools.remember({ ...a, project: project ?? undefined }) };
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
      async execute(args, exec) {
        const a = args as { query: string; top_k?: number };
        const project = projectOf((exec as any)?.agent?.session?.header?.cwd, projectTag);
        return { results: await tools.search({ query: a.query, top_k: a.top_k ?? 5, project: project ?? undefined }) };
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
  // 启动补账：替代旧的空闲轮询（与聊天活跃度无关）
  startupConsolidate: Schema.boolean().default(true).description("启动时自动补账"),
  standingLayers: Schema.string().default("profile,rules").description("常驻记忆类型，逗号分隔"),
  // 内核 LLM 模型 id（空 = 用 .env / 环境变量里的值）。
  // 改动不重启 DSH：applySettings 重设后端子进程 env 并断开连接，下次调用用新环境重新 spawn。
  llmModel: Schema.string().default("").description("内核 LLM 模型 id（空=跟随 .env）"),
  // 已弃用：保留数值 schema 只为接收旧配置，不给默认值、不在设置页展示
  consolidateIdleMinutes: Schema.number().description("已弃用，仅兼容旧关闭设置"),
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
    startupConsolidate: config.startupConsolidate ?? config.consolidateIdleMinutes !== 0,
    hitTopK: undefined as number | undefined,
    llmModel: "",
  };
  overrides.hitTopK = config.hitTopK ?? 3;
  // 旧配置迁移：consolidateIdleMinutes=0 曾表示「明确关闭巩固」。
  // 必须看 user/base 层是否**显式**设置过——schema 默认值填好的数字会掩盖旧 0。
  let legacyConsolidateDisabled = config.consolidateIdleMinutes === 0;
  let legacyNotice = legacyConsolidateDisabled
    ? "旧的 consolidateIdleMinutes=0 已映射为 startupConsolidate=false（启动补账关闭）。"
    : "";
  const standingLayers = (): string[] => (overrides.standingLayers as string[]).filter((t) => VALID_MEM_TYPES.has(t));
  /** settings watch 触发时的模型切换钩子；backend 构造后才赋值（声明顺序）。 */
  let onModelSettingChanged: (() => void) | undefined;
  const reflectTurns = (): number => overrides.reflectTurns as number;
  const hitTopK = (): number => overrides.hitTopK as number;

  try {
      const scope = (ctx as any).settings.register(
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
      // 显式新字段优先于旧字段；user 层优先于 base/config 层。
      const descriptor: any = (ctx as any).settings.describe()
        .find((entry: any) => entry?.ns === SETTINGS_NS);
      const user = descriptor?.user ?? {};
      const base = descriptor?.base ?? config;
      const hasNewSetting = Object.prototype.hasOwnProperty.call(user, "startupConsolidate")
        || Object.prototype.hasOwnProperty.call(base, "startupConsolidate");
      const legacy = Object.prototype.hasOwnProperty.call(user, "consolidateIdleMinutes")
        ? user.consolidateIdleMinutes
        : base.consolidateIdleMinutes;
      if (!hasNewSetting && typeof legacy === "number") {
        legacyConsolidateDisabled = legacy === 0;
        legacyNotice = legacyConsolidateDisabled
          ? "旧的 consolidateIdleMinutes=0 已映射为 startupConsolidate=false（启动补账关闭）。"
          : "旧的 consolidateIdleMinutes 阈值已停用：巩固改为每次启动补账一次。";
        overrides.startupConsolidate = !legacyConsolidateDisabled;
      }
      if (legacyNotice) ctx.logger.info(`[dsh-iwiw-memory] ${legacyNotice}`);
      scope.watch(() => {
        applySettings(scope.get());
        ctx.logger.info(`[dsh-iwiw-memory] settings updated: ${JSON.stringify(overrides)}`);
        // 模型变更需要重设子进程 env + 重连，由下面赋值进去的钩子执行
        onModelSettingChanged?.();
      });
      ctx.logger.info("[dsh-iwiw-memory] settings section installed");
  } catch (e) {
    ctx.logger.warn("[dsh-iwiw-memory] settings registration failed (patch config in effect)", e);
  }
  // 子进程 env：部署 env + 当前模型选择（空串=不覆盖，让 .env 生效）
  const backendEnv = (): Record<string, string> => {
    const env = { ...(config.env ?? {}) };
    const chosen = String(overrides.llmModel ?? "").trim();
    if (chosen) env.MEMORY_AGENT_LLM_MODEL = chosen;
    return env;
  };

  const backend = new MemoryBackend(python, ["-m", "memory_agent.mcp_server"], cwd, backendEnv());
  const tools = new MemoryTools(backend);

  // 模型变更：重设 env 并断开连接（不重启 DSH）。断开会杀掉子进程，
  // 在飞的工具调用会失败一次，所以放在 watch 里异步做，不阻塞设置写入。
  let lastModel = String(overrides.llmModel ?? "").trim();
  const applyModelChange = async (): Promise<void> => {
    const next = String(overrides.llmModel ?? "").trim();
    if (next === lastModel) return;
    lastModel = next;
    backend.setEnv(backendEnv());
    await backend.close().catch(() => {});
    ctx.logger.info(`[dsh-iwiw-memory] kernel llm model → ${next || "(follow .env)"}; backend will respawn`);
    // 常驻段正文不随模型变，但让用户立刻看到生效状态：刷新一次 core 缓存。
    void refreshCore().catch(() => {});
  };
  onModelSettingChanged = () => { void applyModelChange(); };

  // 1) 启动时预热后端（避免首次工具调用才连接）。
  try { await backend.callTool("list_memories", { limit: 1 }); } catch (e) { ctx.logger.warn("memory backend warmup failed", e); }

  // 2) 注册 4 个 memory_* 工具（与 CLI chat 同源）；remember 成功后刷新 core 段缓存（A2）+ reflect 计数归零。
  let stepSinceWrite = 0;
  const defs = makeDefinition(tools, () => { stepSinceWrite = 0; refreshCore().catch(() => {}); }, config.projectTag);
  const disposers = [
    ctx.tools.register(defs.memory_remember),
    ctx.tools.register(defs.memory_search),
    ctx.tools.register(defs.memory_read),
    ctx.tools.register(defs.memory_list),
  ];

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
          // 注入视图剥离检索字段（关键词行）：常驻层不经 FTS，那行纯空转。
          // 必须在预算裁剪之前剥，否则剥离省下的额度会被浪费。
          let body = stripRetrievalFields(mem?.content ?? "");
          if (body.length > budget) body = body.slice(0, budget) + "...";
          budget -= body.length;
          lines.push(`- **${it.slug}** — ${it.description}`);
          if (body) lines.push(`  ${body}`);
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
  let refreshInFlight: Promise<void> | null = null;
  let pending = false;
  let disposing = false;
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
        // 同 fetchCoreText：准则段同样全量注入，剥离检索字段
        const body = stripRetrievalFields(mem?.content ?? "");
        if (body) lines.push(`- ${body}`);
      }
      return lines.length > 0 ? lines.join("\n") : "";
    } catch (e) {
      ctx.logger.warn("fetch rules failed", e);
      return "";
    }
  };
  // 返回可等待的在途 Promise：调用方 await 它才是「刷新已生效」，
  // 只把请求排进队列就说刷新完成会让系统段读到旧值。
  const refreshCore = (): Promise<void> => {
    if (disposing) return refreshInFlight ?? Promise.resolve();
    if (refreshInFlight) { pending = true; return refreshInFlight; }
    refreshInFlight = (async () => {
      try {
        do {
          pending = false;
          cachedCore = await fetchCoreText();
          cachedRules = await fetchRulesText();
        } while (pending);
      } finally { refreshInFlight = null; }
    })();
    return refreshInFlight;
  };
  refreshCore().catch(() => {});
  // 3) system prompt 段（core / rules / 工具提示）——唯一构造源在 prompts.ts，
  //    /iwiw-prompt 回显同一份，注入内容与回显不会漂移。
  const sectionText = { core: () => cachedCore, rules: () => cachedRules };
  for (const section of iwiwSections(sectionText)) {
    disposers.push(ctx.systemPrompt.section(section));
  }

  // 4.5) 跨项目「想法带回」管道（/memo + /recall）——纯开发者工具，可整块剥离：
  //     删除本段两行 + src/capture.ts + package.json 的 peerDependency 即可，
  //     不影响上面的记忆工具与注入链路。
  try {
    // 与内核 @deepseek-ai/dsh-home-paths resolveDshHome 同语义：非空 $DSH_HOME 优先，
    // 否则回退 ~/.dsh。桌面端插件进程里 DSH_HOME 常缺省，直接读 env 会得到 profiles\web（盘符相对路径）。
    const dshHome = (process.env.DSH_HOME ?? "").trim() || join(homedir(), ".dsh");
    const profileDir = config.profileDir ?? join(dshHome, "profiles", "web");
    disposers.push(...registerCaptureCommands(ctx, { python, profileDir, promptText: sectionText }));
    ctx.logger.info("[dsh-iwiw-memory] capture commands registered (/memo, /recall, /iwiw-prompt)");
  } catch (e) {
    ctx.logger.warn("[dsh-iwiw-memory] capture commands registration failed", e);
  }

  // 5) 每消息命中注入（agent/pre-step）：最后一条 user 消息触发检索，
  //    命中且未注入过的记忆以快照消息插入其前。
  //    会话内去重：注入过的 slug 不再重复注入（compress 后由 reinject 补回）。
  //    检索带 session_id + context（内核 per-session SessionState，回指联想）
  //    并排除常驻层（已在 system 注入，命中注入防重复）。
  const injectedBySession = new Map<string, Set<string>>();
  // 压缩后补回：compaction 释放去重并快照本会话已注入 slugs，compaction/end 后 pre-step 补回
  const reinjectArmed = new Map<string, string[]>();
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
          // project 型记忆按当前项目隔离（内核侧只过滤 project 类型，见 project_visible）
          project: projectOf(agent?.session?.header?.cwd, config.projectTag) ?? undefined,
        });
        const hits = (Array.isArray(raw) ? raw : ((raw as any)?.hits ?? [])) as Array<{
          slug: string; description: string; priority: string; content: string;
          recorded_date?: string;
        }>;
        const seen = injectedBySession.get(sid) ?? new Set<string>();
        const fresh = hits.filter((h) => h.slug && !seen.has(h.slug));
        if (fresh.length === 0) return decision;
        for (const h of fresh) seen.add(h.slug);
        injectedBySession.set(sid, seen);
        const lines = ["## 相关记忆（命中）", ""];
        for (const h of fresh) {
          lines.push(`- **${h.slug}** (${h.priority}) — ${h.description}${recordedSuffix(h.recorded_date)}`);
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

  // 6) 启动补账（A）：每次插件启动跑一次后台补账，替代旧的空闲轮询。
  //    与聊天活跃度无关，也没有峰时抑制。结果暂存 lastConsolidateReport，
  //    下一次对话 pre-step 一次性注入。
  //    常驻、永不重启的进程不会自动周期巩固；需要时手动调 MCP 的 run_consolidate。
  // 巩固/补账报告：完成后暂存，下一次对话 pre-step 一次性注入（与旧定时器同款消费点）。
  const lastConsolidateReport = { value: "" };
  const startupState: { running: boolean; finished: boolean } = { running: false, finished: false };
  const startupTasks = new Set<Promise<void>>();
  let activeStartupController: AbortController | null = null;
  if (overrides.startupConsolidate === false) {
    ctx.logger.info("[dsh-iwiw-memory] startup reconcile disabled by settings");
  } else {
    ctx.inject(["storageDomain", "sessionQuery", "sessionPersistence"], (sctx: any) => {
      // 回调可能因服务重绑再次执行：同次 apply 内防重复；失败/服务暂失回到可重试态。
      if (startupState.running || startupState.finished) return;
      startupState.running = true;
      const controller = new AbortController();
      activeStartupController = controller;
      const task = runStartup(sctx, {
        getDatabaseId: () => tools.databaseId(),
        consolidate: (sinceMs, untilMs) => tools.consolidateWindow(sinceMs, untilMs, controller.signal),
        report: (text) => { lastConsolidateReport.value = text; },
        log: {
          info: (msg) => ctx.logger.info(msg),
          warn: (msg, err) => ctx.logger.warn(msg, err),
        },
        signal: controller.signal,
      })
        .then((outcome: StartupOutcome) => {
          startupState.finished = outcome.status !== "failed";
          ctx.logger.info(`[dsh-iwiw-memory] startup reconcile: ${outcome.status}`);
        })
        .catch((error) => ctx.logger.warn("[dsh-iwiw-memory] startup reconcile crashed", error))
        .finally(() => {
          startupState.running = false;
          startupTasks.delete(task);
          if (activeStartupController === controller) activeStartupController = null;
        });
      startupTasks.add(task);
      return async () => {
        controller.abort();
        await task;
      };
    });
  }

  ctx.logger.info("[dsh-iwiw-memory] applied: 4 tools + 3 prompt sections + pre-step hook (reflect/hit) + startup reconcile");

  return async () => {
    disposing = true;
    activeStartupController?.abort();
    for (const d of disposers) { try { d(); } catch {} }
    const tasks = [...startupTasks];
    if (refreshInFlight) tasks.push(refreshInFlight);
    await Promise.allSettled(tasks);
    await backend.close();
    ctx.logger.info("[dsh-iwiw-memory] disposed");
  };
};
