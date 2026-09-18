import type { ContentBlock } from "@deepseek-ai/dsh-llm";

/** core 记忆（must-load）段名；正文由 apply 时拉取，避免启动抢 Python 资源。 */
export const MEMORY_SECTION_NAME = "iwiw-memory:core";
/** rules 准则段名。 */
export const RULES_SECTION_NAME = "iwiw-memory:rules";

/**
 * 剥离只对**检索**有意义的字段（注入视图专用，不动库里的正文）。
 *
 * `关键词: …` 行是 FTS 同义词扩展的锚点，只在按话题召回时起作用。
 * 常驻层是全量无条件注入、永不经过 FTS 的，这行纯属空转——实测占常驻注入 17.6%。
 * 剥离只发生在拼进 prompt 之前；memory_read / memory_search 取回的仍是完整正文。
 *
 * 规则（确定性，无 LLM）：
 *   - 独立成行的 `关键词: …` 整行删除；
 *   - `关键词: …` 粘在最后一个非空行尾部时，从该处截断（实测全库 1 例）。
 * 正文中间顺带提到「关键词」的句子不受影响。
 */
export function stripRetrievalFields(text: string): string {
  const lines = text.split("\n");
  const kept: string[] = [];
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]!;
    if (/^[ \t]*关键词[ \t]*[:：]/.test(line)) continue;
    const inline = /关键词[ \t]*[:：]/.exec(line);
    if (inline && lines.slice(i + 1).every((l) => l.trim() === "")) {
      kept.push(line.slice(0, inline.index));
      continue;
    }
    kept.push(line);
  }
  return kept.join("\n").trim();
}

/**
 * iwiw 注入的全部 system prompt 段 —— 唯一构造源。
 * 注册（index.ts）与 /iwiw-prompt 回显（capture.ts）共用同一份，两者不会漂移。
 * 取值惰性：每次调用读当下的 core/rules 缓存。
 */
export function iwiwSections(get: { core: () => string; rules: () => string }): Array<{
  name: string;
  order: number;
  text: () => string;
}> {
  return [
    { name: MEMORY_SECTION_NAME, order: -50, text: () => get.core() || "（core 记忆加载中…）" },
    {
      name: RULES_SECTION_NAME,
      order: -45,
      text: () => {
        const rules = get.rules();
        if (!rules) return "";
        return [
          "## 准则",
          "",
          "以下准则来自记忆库 rules 层，请在本会话中严格遵守：",
          "",
          rules,
        ].join("\n");
      },
    },
    { name: TOOL_GUIDE_SECTION_NAME, order: 110, text: () => toolGuideSection.text },
  ];
}

/** 工具使用提示（静态）：告诉模型有哪些 memory_* 工具以及何时该用。 */
export const TOOL_GUIDE_SECTION_NAME = "iwiw-memory:tools";
export const toolGuideSection = {
  name: TOOL_GUIDE_SECTION_NAME,
  order: 110,
  text: [
    "## 记忆工具",
    "",
    "你可以使用以下记忆工具（按需调用，无需用户要求）：",
    "- memory_remember(description, body, level?, slug?)：把值得长期保存的稳定信息写入记忆；写入即全文替换；新建前先 memory_search 查重。",
    "- memory_search(query, top_k=5)：按关键词检索相关长期事实。",
    "- memory_read(slug)：读取一条记忆的完整正文。",
    "- memory_list(priority?, mem_type?, limit=20)：列出记忆条目。",
    "",
    "level 取值：profile=用户身份画像/健康/偏好；fact=一般事实；lesson=教训与经验；rules=准则；project=项目脉络与决策。",
    "什么时候调用 memory_search：用户话题涉及个人事实、长期计划、健康、关系、偏好或历史决策时优先调。",
    "什么时候调用 memory_remember：出现身份画像（profile）、一般事实（fact）、教训（lesson）、准则（rules）、项目脉络（project），或用户明确要求记住/更正时。",
    "调用原则：用户的最新明示更正优先（如「其实我不喜欢 X」应理解为覆盖而非并列）；一次性、临时话题不要写。",
    "敏感信息：不要写入凭据类内容（API key、token、密码、私钥、连接串口令）——需要记录「配置在哪」就写占位符（如 ARK_API_KEY=<见 .env>），不要抄真值；公网 IP/域名只记用途与归属，本机与内网地址（127.0.0.1、192.168.x.x）可保留。写入前内核会做一次确定性脱敏，命中项会被替换并在工具返回里告知。",
  ].join("\n"),
};

/** 抽取助手：把任意值转成可展示的 ContentBlock 列表。 */
export function toTextBlocks(value: unknown): ContentBlock[] {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return [{ type: "text", text }];
}
