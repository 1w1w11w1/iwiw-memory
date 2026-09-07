import { defineTool } from "@deepseek-ai/dsh-tools";
import { MemoryBackend } from "./backend.js";
import { MemoryTools, toTextBlocks } from "./tools.js";
import { toolGuideSection, MEMORY_SECTION_NAME } from "./prompts.js";
/** dsh-iwiw-memory：IwIw 记忆内核的 DSH 插件 —— 跨会话记忆 + 自动学习。 */
export const name = "dsh-iwiw-memory";
const DEFAULT_PYTHON = "E:/desktop/111/.venv/Scripts/python.exe";
const DEFAULT_CWD = "E:/desktop/111";
/** Phase B：当前仅暴露搜索/提取/列表/更新四个高频工具，stats 暂用脚本/CLI。 */
function makeDefinition(tools) {
    return {
        memory_search: defineTool({
            name: "memory_search",
            description: "按关键词检索长期记忆。涉及个人事实、计划、健康、关系、偏好时主动调用。",
            parameters: {
                query: { type: "string", required: true, description: "检索关键词" },
                top_k: { type: "integer", required: false, description: "返回条数（默认 5）" },
            },
            output: {
                schema: { type: "object", additionalProperties: false, properties: { results: { type: "json" } } },
                render: (_args, value) => toTextBlocks(value),
            },
            async execute(args) {
                const a = args;
                return await tools.search({ query: a.query, top_k: a.top_k ?? 5 });
            },
        }),
        memory_extract: defineTool({
            name: "memory_extract",
            description: "从单条消息中提取并保存长期记忆。系统也会自动提取；此工具可手动触发或重提。",
            parameters: {
                message: { type: "string", required: true, description: "待分析消息" },
                context: { type: "string", required: false, description: "对话上下文" },
            },
            output: {
                schema: { type: "object", additionalProperties: false, properties: { new_memories: { type: "array" }, count: { type: "integer" } } },
                render: (_args, value) => toTextBlocks(value),
            },
            async execute(args) {
                const a = args;
                return await tools.extract({ message: a.message, context: a.context ?? "" });
            },
        }),
        memory_list: defineTool({
            name: "memory_list",
            description: "按分级列出记忆（priority=core|normal|archive 可选过滤）。",
            parameters: {
                priority: { type: "string", required: false, description: "core|normal|archive" },
                limit: { type: "integer", required: false, description: "条数（默认 20）" },
            },
            output: {
                schema: { type: "object", additionalProperties: false, properties: { items: { type: "array" } } },
                render: (_args, value) => toTextBlocks(value),
            },
            async execute(args) {
                const a = args;
                return await tools.list({ priority: a.priority, limit: a.limit ?? 20 });
            },
        }),
        memory_update: defineTool({
            name: "memory_update",
            description: "覆盖更新一条记忆（slug 必须存在；变更前快照入版本表）。",
            parameters: {
                slug: { type: "string", required: true, description: "记忆 slug" },
                description: { type: "string", required: true, description: "一句话描述" },
                body: { type: "string", required: true, description: "更新后的完整正文" },
                priority: { type: "string", required: false, description: "core|normal|archive（默认 normal）" },
            },
            output: {
                schema: { type: "object", additionalProperties: false, properties: { ok: { type: "boolean" } } },
                render: (_args, value) => toTextBlocks(value),
            },
            async execute(args) {
                const a = args;
                return await tools.update({
                    slug: a.slug, description: a.description, body: a.body, priority: a.priority,
                });
            },
        }),
    };
}
export const apply = async (ctx, config = {}) => {
    const python = config.python ?? DEFAULT_PYTHON;
    const cwd = config.cwd ?? DEFAULT_CWD;
    const backend = new MemoryBackend(python, ["-m", "memory_agent.mcp_server"], cwd);
    const tools = new MemoryTools(backend);
    // 1) 启动时预热后端（避免首次工具调用才连接）。
    try {
        await backend.callTool("list_memories", { limit: 1 });
    }
    catch (e) {
        ctx.logger.warn("memory backend warmup failed", e);
    }
    // 2) 注册 4 个 memory_* 工具。
    const defs = makeDefinition(tools);
    const disposers = [
        ctx.tools.register(defs.memory_search),
        ctx.tools.register(defs.memory_extract),
        ctx.tools.register(defs.memory_list),
        ctx.tools.register(defs.memory_update),
    ];
    // 3) 静态工具使用提示。
    ctx.systemPrompt.section(toolGuideSection);
    disposers.push(ctx.systemPrompt.section(toolGuideSection));
    // 4) 动态 core 段：每次装配时拉取（轻量：只取 priority=core 的标题和正文）。
    const fetchCoreText = async () => {
        try {
            const list = (await tools.list({ priority: "core", limit: 50 }));
            const items = list?.items ?? [];
            if (items.length === 0)
                return "（暂无必读长期记忆）";
            return items.map((m) => `- **${m.slug}** — ${m.description}\n  ${m.content}`).join("\n");
        }
        catch (e) {
            ctx.logger.warn("fetch core memory failed", e);
            return "（core 记忆拉取失败：${String(e)}）";
        }
    };
    let cachedCore = "";
    // 启动时异步预热（不阻塞插件激活）
    fetchCoreText().then((t) => { cachedCore = t; }).catch(() => { });
    disposers.push(ctx.systemPrompt.section({
        name: MEMORY_SECTION_NAME,
        order: -50,
        text: () => cachedCore || "（core 记忆加载中…）",
    }));
    // 5) 会话事件自动提取：每个 user/message 触发高层信号检查。
    //    使用 session/event 监听（emit 模式），对已入历史的消息做异步提取。
    const sessionEvents = ctx.events ?? ctx.scope?.events;
    if (sessionEvents?.on) {
        const off = sessionEvents.on("session/event", (session, ev) => {
            if (ev?.type !== "user/message")
                return;
            const content = extractTextFromMessage(ev.message);
            if (!content || content.length < 4)
                return;
            // 简化触发：长度 > 12 且包含身份/偏好/决策关键词 → 自动提取
            if (!/我|你|我|我[觉想爱需要]|偏好|喜欢|不喜欢|决定|计划|记住|健康|有.+?病|过敏/.test(content))
                return;
            // 异步，不阻塞事件循环
            tools.extract({ message: content }).catch((e) => ctx.logger.warn("auto extract failed", e));
        });
        disposers.push(off);
    }
    ctx.logger.info("[dsh-iwiw-memory] applied: 4 tools + 2 prompt sections + auto-extract on");
    return async () => {
        for (const d of disposers) {
            try {
                d();
            }
            catch { }
        }
        await backend.close();
        ctx.logger.info("[dsh-iwiw-memory] disposed");
    };
};
/** 从 UserMessage 提取可读文本（ContentBlock[] 拼接）。 */
function extractTextFromMessage(msg) {
    if (!msg)
        return "";
    const blocks = (msg.content ?? msg.message?.content);
    if (!Array.isArray(blocks))
        return typeof msg === "string" ? msg : "";
    return blocks
        .filter((b) => b?.type === "text" && typeof b.text === "string")
        .map((b) => b.text)
        .join("\n")
        .trim();
}
