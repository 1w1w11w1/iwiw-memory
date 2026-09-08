import { Context } from "@deepseek-ai/cordis";
import { defineTool } from "@deepseek-ai/dsh-tools";
import { MemoryBackend } from "./backend.js";
import { MemoryTools, toTextBlocks } from "./tools.js";
import { coreSectionText, toolGuideSection, MEMORY_SECTION_NAME } from "./prompts.js";

/** dsh-iwiw-memory：IwIw 记忆内核的 DSH 插件 —— 跨会话记忆（模型自主工具化写入）。 */
export const name = "dsh-iwiw-memory";

interface PluginConfig {
  /** Python 解释器路径（含 mcp/jieba 依赖的 venv 或系统 Python）。 */
  python?: string;
  /** memory_agent 工作目录。 */
  cwd?: string;
  /** core 注入段总字符预算（与内核 MEMORY_RECALL_MAX_CHARS 对齐）。 */
  coreMaxChars?: number;
  /** 每消息命中注入的条数上限（默认 3）。 */
  hitTopK?: number;
  /** 覆盖 MCP 子进程环境变量（如 MEMORY_AGENT_DB_PATH 指向隔离库）。 */
  env?: Record<string, string>;
}

const DEFAULT_PYTHON = "E:/desktop/111/.venv/Scripts/python.exe";
const DEFAULT_CWD = "E:/desktop/111";
const DEFAULT_CORE_MAX_CHARS = 2500;

function makeDefinition(tools: MemoryTools, onRemember?: () => void) {
  return {
    memory_remember: defineTool({
      name: "memory_remember",
      description: "把值得长期保存的稳定事实写入记忆（身份、偏好、决策、健康、关系、计划）。写入即全文替换；新建前先 memory_search 查重。",
      parameters: {
        description: { type: "string", required: true, description: "一句话描述" },
        body: { type: "string", required: true, description: "完整正文（整体替换旧内容，不是追加）" },
        slug: { type: "string", description: "可选。更新已有记忆时填其 slug；新建建议用简短英文连字符命名，不填则自动生成" },
        priority: { type: "string", description: "core|normal|archive，默认 normal；身份/健康/重大决策用 core" },
      },
      output: {
        schema: { type: "object", additionalProperties: false, properties: { result: { type: "json" } } },
        render: (_args, value) => toTextBlocks(value),
      },
      async execute(args) {
        const a = args as { description: string; body: string; slug?: string; priority?: string };
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
      description: "列出记忆条目（可按 core/normal/archive 过滤）。",
      parameters: {
        priority: { type: "string", description: "core|normal|archive" },
        limit: { type: "integer", description: "条数（默认 20）" },
      },
      output: {
        schema: { type: "object", additionalProperties: false, properties: { items: { type: "json" } } },
        render: (_args, value) => toTextBlocks(value),
      },
      async execute(args) {
        const a = args as { priority?: string; limit?: number };
        return { items: await tools.list({ priority: a.priority, limit: a.limit ?? 20 }) };
      },
    }),
  } as const;
}

export const apply = async (ctx: Context, config: PluginConfig = {}) => {
  const python = config.python ?? DEFAULT_PYTHON;
  const cwd = config.cwd ?? DEFAULT_CWD;
  const coreMaxChars = config.coreMaxChars ?? DEFAULT_CORE_MAX_CHARS;
  const backend = new MemoryBackend(python, ["-m", "memory_agent.mcp_server"], cwd, config.env);
  const tools = new MemoryTools(backend);

  // 1) 启动时预热后端（避免首次工具调用才连接）。
  try { await backend.callTool("list_memories", { limit: 1 }); } catch (e) { ctx.logger.warn("memory backend warmup failed", e); }

  // 2) 注册 4 个 memory_* 工具（与 CLI chat 同源）；remember 成功后刷新 core 段缓存（A2）。
  const defs = makeDefinition(tools, () => { refreshCore().catch(() => {}); });
  const disposers = [
    ctx.tools.register(defs.memory_remember),
    ctx.tools.register(defs.memory_search),
    ctx.tools.register(defs.memory_read),
    ctx.tools.register(defs.memory_list),
  ];

  // 3) 静态工具使用提示。
  disposers.push(ctx.systemPrompt.section(toolGuideSection));

  // 4) 动态 core 段：list 拿 slug + 逐条 read 拿全文，字符预算内拼装。
  const fetchCoreText = async (): Promise<string> => {
    try {
      // MCP list_memories 返回裸数组；execute 层的 {items} 包装不经过这里
      const raw = (await tools.list({ priority: "core", limit: 50 })) as
        | Array<{ slug: string; description: string }>
        | { items?: Array<{ slug: string; description: string }> };
      const items = Array.isArray(raw) ? raw : (raw?.items ?? []);
      if (items.length === 0) return "（暂无必读长期记忆）";
      let budget = coreMaxChars;
      const lines: string[] = [];
      for (const it of items) {
        if (budget <= 0) break;
        const mem = (await tools.read({ slug: it.slug })) as { content?: string } | null;
        let body = mem?.content ?? "";
        if (body.length > budget) body = body.slice(0, budget) + "...";
        budget -= body.length;
        lines.push(`- **${it.slug}** — ${it.description}`);
        lines.push(`  ${body}`);
      }
      return lines.join("\n");
    } catch (e) {
      ctx.logger.warn("fetch core memory failed", e);
      return `（core 记忆拉取失败：${String(e)}）`;
    }
  };
  // A2 一致性：core 段缓存 + 写入后异步刷新（pending 重跑，刷新期间的请求不丢失）。
  let cachedCore = "";
  let refreshing = false;
  let pending = false;
  const refreshCore = async () => {
    if (refreshing) { pending = true; return; }
    refreshing = true;
    try {
      do {
        pending = false;
        cachedCore = await fetchCoreText();
      } while (pending);
    } finally { refreshing = false; }
  };
  refreshCore().catch(() => {});
  disposers.push(ctx.systemPrompt.section({
    name: MEMORY_SECTION_NAME,
    order: -50,
    text: () => cachedCore || "（core 记忆加载中…）",
  }));

  // 5) 每消息命中注入（agent/pre-step）：最后一条 user 消息触发检索，
  //    命中且未注入过的记忆以快照消息插入其前（meow 同款机制）。
  //    会话内去重：注入过的 slug 不再重复注入（compress 后由 reinject 补回，后续实现）。
  const hitTopK = config.hitTopK ?? 3;
  const injectedBySession = new Map<string, Set<string>>();
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
      try {
        const raw = await tools.search({ query: text, top_k: hitTopK });
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
        ctx.logger.info(`[dsh-iwiw-memory] hit injected: ${fresh.map((h) => h.slug).join(", ")}`);
        return { ...decision, messages: rewritten };
      } catch (e) {
        ctx.logger.warn("[dsh-iwiw-memory] hit injection failed", e);
        return decision;
      }
    }),
  );

  ctx.logger.info("[dsh-iwiw-memory] applied: 4 tools + 2 prompt sections + pre-step hook");

  return async () => {
    for (const d of disposers) { try { d(); } catch {} }
    await backend.close();
    ctx.logger.info("[dsh-iwiw-memory] disposed");
  };
};
