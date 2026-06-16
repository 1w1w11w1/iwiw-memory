# DeepSeek、DualPath 与 oMLX 对 SelfEcho 智能体架构的启发

本文用于回答一个具体问题：如果 SelfEcho 后续要面向普通用户提供“开盖即食”的个人智能体能力，并且希望优先优化 DeepSeek 模型，DualPath 论文与 `jundot/omlx` 项目能给我们什么架构启发。

结论先行：

- `DualPath` 不是一个前端或 agent 编排框架，而是一篇 DeepSeek-AI 参与的 agentic LLM 推理系统论文，核心关注多轮 Agent 推理中的 KV Cache 存储带宽瓶颈。
- `oMLX` 是 Apple Silicon 上的本地 LLM 推理服务，核心价值是把“本地模型可用性”做完整：OpenAI/Anthropic API、连续批处理、热/冷 KV 缓存、多模型管理、工具调用解析、管理后台。
- SelfEcho 现阶段不应直接实现 DualPath 那种数据中心级 RDMA / PD disaggregation，也不应复制 oMLX 的推理服务代码。
- 真正应该吸收的是三类思想：缓存友好的长上下文设计、模型角色路由与调度、以及可观察的 agent harness。
- 对 DeepSeek 的优化应优先做在 Provider 模板、模型角色绑定、工具调用解析、结构化输出校验、Prompt 策略、上下文构造稳定性上。

## 资料来源

### DualPath

- 标题：`DualPath: Breaking the Storage Bandwidth Bottleneck in Agentic LLM Inference`
- arXiv：<https://arxiv.org/abs/2602.21548>
- 作者机构包含 Peking University、Tsinghua University、DeepSeek-AI。
- 论文研究对象是 agentic LLM inference，即长会话、多轮、工具调用环境下的 LLM 推理系统。

论文的关键判断：

- Agentic workload 从“用户问、模型答”的少轮交互，变成“用户、模型、外部环境”之间几十轮甚至上百轮的交互。
- 每一轮新增 token 可能很短，但完整上下文会不断累积。
- 这种 `multi-turn, short-append` 模式会产生很高的 KV Cache 命中率，论文中引用的典型命中率为 `>= 95%`。
- 性能瓶颈不再只是计算，而是如何高效加载、复用、迁移 KV Cache。
- 传统 PD disaggregation 中，prefill engine 负责从外部存储加载 KV Cache，decode engine 的存储网络带宽相对闲置，造成带宽不平衡。
- DualPath 增加了 storage-to-decode path：KV Cache 可以先加载到 decode engine，再通过 RDMA 传给 prefill engine。
- 论文报告在真实 agentic workload 上，离线吞吐最高提升 `1.87x`，在线 serving 平均提升 `1.96x`，且不违反 SLO。

### oMLX

- 仓库：<https://github.com/jundot/omlx>
- 项目定位：Apple Silicon 上的 LLM inference server，提供连续批处理与 SSD KV cache，并用 macOS 菜单栏和 Web Admin 管理。
- License：Apache-2.0。

oMLX 的关键能力：

- 提供 OpenAI / Anthropic 兼容 API，包括 `/v1/chat/completions`、`/v1/messages`、`/v1/embeddings`、`/v1/rerank`。
- 基于 `mlx-lm` BatchGenerator 做 continuous batching。
- 分层 KV Cache：热缓存放 RAM，冷缓存放 SSD；支持 prefix sharing 与 Copy-on-Write。
- 多模型服务：LRU 自动驱逐、手动加载/卸载、模型 pin、每模型 TTL、进程内存限制。
- 管理后台：模型下载、模型管理、聊天、benchmark、实时状态。
- 支持 LLM、VLM、OCR、embedding、reranker。
- 支持工具调用和结构化输出解析；DeepSeek 被归入 JSON `<tool_call>` 一类。
- 针对 Claude Code 做过上下文缩放、SSE keep-alive 等适配。
- 支持 MCP 配置，让本地模型能接入工具生态。

## 对 SelfEcho 的直接启发

### 1. Agent 时代的性能问题，本质上是上下文生命周期问题

DualPath 的工程语境是数据中心推理服务，但它揭示的问题对 SelfEcho 同样成立：真正成熟的个人智能体不是单轮聊天，而是长时间、多会话、多工具、多记忆的连续运行。

SelfEcho 当前已有：

- `memory/`：Markdown 长期事实记忆。
- `selfecho_session/`：原始会话、摘要、检索、回放。
- `selfecho_api/server.py::_reply()`：临时拼接 treehole prompt、L0/L1 记忆、最近上下文，然后调用 `complete_text()`。
- `selfecho_api/model_config.py`：已有 DeepSeek 模板。

接下来不能继续把上下文当成一段字符串随手拼接。应新增 `selfecho_agent/ContextBuilder`，把上下文分成稳定前缀与动态尾部：

- 稳定前缀：系统身份、模型行为边界、工作模式/陪我想想策略、L0/L1 记忆摘要。
- 半稳定段：当前会话 rolling summary、当前目标、用户偏好策略。
- 动态尾部：最近消息、刚检索出的 L2/L3 记忆、工具结果、用户本轮输入。

这样做的意义不只是代码干净，也会天然更适配 KV / prefix cache。无论后端是 DeepSeek 云 API、本地 oMLX、还是未来其他推理服务，稳定前缀越稳定，缓存命中和响应一致性越好。

### 2. SelfEcho 也需要“双路径”，但不是照搬 DualPath 的网络路径

DualPath 的“双路径”是 KV Cache 加载路径：storage-to-prefill 与 storage-to-decode。

SelfEcho 应吸收成 agent 运行路径：

- `Light Path`：轻量对话路径。用于日常聊天、陪我想想、简单问题、低风险回复。目标是低延迟、少工具、少打扰。
- `Deliberate Path`：审慎工作路径。用于工程模式、复杂问题、计划制定、外部工具、记忆改写、健康/风险/重大决策。目标是可解释、可审计、可回滚。
- `Escalation Path`：升级路径。当系统检测到高情绪、高不确定性、长任务、多工具、记忆冲突时，从 Light Path 转入 Deliberate Path，但应向用户解释并允许用户覆盖。

这比简单的“工作模式开关”更成熟。前端可以保留一个温和的显式开关：`工作模式`。但 harness 内部还应记录：

- 用户显式模式。
- 系统判断的 intent。
- 最终采用的 path。
- 采用该 path 的理由。
- 本轮使用了哪些记忆和工具。

### 3. oMLX 提醒我们：模型管理不是填 JSON，而是运行时治理

SelfEcho 当前模型页已从原始 JSON 转向 provider 模板，这是对的。但如果要成为智能体，模型管理还要升级成“角色绑定”。

建议模型角色：

- `chat_light`：日常对话、陪我想想、低延迟回复。
- `chat_work`：工作模式下的问题拆解与结构化回答。
- `planner`：复杂任务规划。
- `memory_consolidator`：会话摘要与长期记忆候选变更。
- `memory_editor`：合并、追加、删除、归档的 diff 生成。
- `tool_checker`：工具调用前后的参数检查、结果解释、失败恢复。
- `safety_reflector`：高情绪、高风险、健康相关语境下的策略复核。

DeepSeek 优化可以体现在默认模板：

```json
{
  "provider": "deepseek",
  "roles": {
    "chat_light": "deepseek-v4-flash",
    "chat_work": "deepseek-v4-flash",
    "planner": "deepseek-v4-pro",
    "memory_consolidator": "deepseek-v4-flash",
    "memory_editor": "deepseek-v4-pro",
    "tool_checker": "deepseek-v4-flash"
  }
}
```

具体模型名应以 DeepSeek 实际 API 可用列表为准。SelfEcho 的 UI 不应让用户先理解 provider JSON，而应给出：

- DeepSeek 推荐配置。
- 低成本配置。
- 稳定工作配置。
- 本地模型配置。
- 自定义模型入口。

## DeepSeek 专项优化建议

### ProviderProfile

在 `selfecho_api/model_config.py` 基础上扩展 provider schema：

- `api_style`：`anthropic` / `openai` / `openai-compatible`。
- `model_family`：`deepseek` / `qwen` / `openai` / `anthropic` / `custom`。
- `tool_call_format`：DeepSeek 默认可设为 `json_tool_call_tag`，对应 oMLX README 中的 JSON `<tool_call>` 风格。
- `supports_streaming`。
- `supports_json_schema`。
- `supports_tools`。
- `default_roles`。
- `sampling_presets`。

这样 GUI 可以隐藏复杂字段，但后端仍知道怎么调用和解析。

### DeepSeekToolCallParser

SelfEcho 未来进入工具系统后，需要单独处理 DeepSeek 的工具调用输出，不要只依赖“模型会乖乖输出 JSON”。

建议新建：

```text
selfecho_agent/
  model_io/
    provider.py
    deepseek.py
    tool_call_parser.py
```

解析策略：

- 优先解析 API 原生 tool_calls。
- 如果是文本中出现 `<tool_call>{...}</tool_call>`，抽取并 JSON parse。
- 校验 tool name 是否注册。
- 校验 arguments 是否符合 schema。
- 不合法时让 `tool_checker` 生成一次修复请求，而不是直接执行。
- 用户可见文本与工具控制标记分离，避免把 `<tool_call>` 展示到聊天气泡里。

### 结构化输出重试

记忆整理、模式识别、计划生成都应该要求结构化输出。DeepSeek 适配时要做“校验-修复-降级”：

1. 要求模型输出 JSON。
2. 解析失败时，把错误和原输出交给同模型做一次 repair。
3. repair 仍失败，降级为纯文本摘要，不写长期记忆、不执行工具。
4. 所有失败记录进入 run trace。

这对开源项目很重要，因为普通用户最怕“看起来运行了，但暗中写坏记忆”。

### SamplingPreset

不同角色应有不同采样参数，不要全局一个 temperature。

建议默认值：

| 场景 | temperature | max_tokens | 说明 |
| --- | --- | --- | --- |
| 陪我想想 | 0.7 - 0.9 | 1200 - 1800 | 保持温柔、自然、有余地 |
| 工作模式回答 | 0.3 - 0.6 | 1600 - 3000 | 更稳定、更结构化 |
| planner | 0.2 - 0.4 | 2000 - 4000 | 避免计划漂移 |
| memory_consolidator | 0.0 - 0.2 | 1200 - 2500 | 保守、可审计 |
| memory_editor | 0.0 - 0.2 | 2000 - 4000 | 生成 diff 时要稳定 |
| tool_checker | 0.0 - 0.2 | 800 - 1500 | 参数校验不需要创意 |

## 应新增的 agent harness 模块

建议下一阶段新增 `selfecho_agent/`，先做骨架，不急着做大量工具。

```text
selfecho_agent/
  __init__.py
  run.py                 # AgentRun, AgentEvent, RunTrace
  modes.py               # DialogueMode, AgentPath, Intent
  router.py              # IntentRouter / PathRouter
  context.py             # ContextBuilder
  model_roles.py         # ModelRoleRouter
  model_io/
    client.py            # 统一 LLM 调用接口
    provider_profile.py
    deepseek.py
    tool_call_parser.py
  prompts.py             # prompt 组装与 preview
  tools/
    registry.py
    schemas.py
  safety.py              # 情绪/风险/副作用边界
  evaluator.py           # 固定案例回放测试
```

第一步迁移点：

- 把 `selfecho_api/server.py::_reply()` 中的 prompt 拼接迁移到 `ContextBuilder`。
- `_reply()` 不直接调用 `complete_text()`，而是调用 `AgentRunner.respond()`。
- 每次用户消息生成 `AgentRun` 和 `AgentEvent`，即使暂时不展示，也先落库或写入会话 metadata。
- GUI 后续可以折叠展示“本轮使用的策略与上下文”。

## 与现有记忆系统的耦合方式

SelfEcho 记忆系统不能被模型调度吞掉。正确关系是：

```text
用户输入
  -> IntentRouter 判断模式与风险
  -> ContextBuilder 检索长期记忆与会话摘要
  -> ModelRoleRouter 选择 DeepSeek 角色模型
  -> AgentRunner 生成回复或计划
  -> SessionMemory 保存原始消息与 run trace
  -> Consolidator 在会话周期结束后生成候选记忆变更
  -> MemoryEditor 生成 diff
  -> 用户确认或自动策略写入 Markdown 记忆
```

关键点：

- `memory/*.md` 仍是真源。
- L0/L1 继续默认注入，但应先压缩成“对本轮有用的策略提示”，而不是无限堆原文。
- L2/L3 只在话题命中、用户要求回顾、或 Router 判断需要时检索。
- 会话摘要服务于上下文，不替代原始消息。
- 长期记忆写入必须进入候选变更、diff、审计、回滚流程。

## GUI 对应变化

模型页：

- DeepSeek 推荐模板放在第一位。
- 增加“角色绑定”区域，而不是只列 provider。
- 每个角色显示当前模型、延迟、最近错误、连接状态。
- 自定义模型入口保留，但不作为默认主路径。

对话页：

- 默认打开聊天。
- 保留 `工作模式` 开关。
- 工作模式关闭时，不明确叫“倾诉模式”，可显示为 `陪我想想` 或轻量状态。
- 本轮如果自动升级到审慎路径，给出温和提示，例如：“这个问题可能需要拆开一点，我会先帮你理清条件。”
- 高级信息折叠：本轮模式、使用记忆、上下文摘要、模型角色。

记忆页：

- 长期记忆和会话记忆继续统一入口。
- 长期记忆按 L0/L1/L2/L3 做明显分级。
- 会话记忆展示摘要、原始回放、整理状态、候选长期记忆。
- 记忆变更必须可 diff、确认、回滚。

运行状态页或折叠面板：

- 当前 provider。
- 各模型角色健康状态。
- 最近 agent run。
- 整理队列。
- 失败的结构化输出与 repair 次数。

## 与 oMLX 的集成边界

oMLX 对 SelfEcho 的短期意义不是“嵌入一个推理服务器”，而是作为一种可选本地后端。

短期：

- 在模型模板中增加 `oMLX / Local OpenAI-compatible`。
- 用户如果在 macOS + Apple Silicon 上安装 oMLX，可以把 SelfEcho 指向 `http://localhost:8000/v1`。
- SelfEcho 只依赖标准 OpenAI/Anthropic API，不依赖 oMLX 内部模块。

中期：

- 读取 `/v1/models`，自动列出本地模型。
- GUI 提供连接测试。
- 对本地 DeepSeek / Qwen / embedding / reranker 模型做角色绑定。
- 如果后端支持缓存 stats，可在健康页展示缓存命中率、热/冷缓存状态、模型加载状态。

长期：

- SelfEcho 可定义 `InferenceBackend` 接口，适配 DeepSeek 云 API、OpenAI-compatible、本地 oMLX、其他本地服务。
- 不把 oMLX 当核心依赖，只当一个高质量 backend profile。

## 需要避免的误区

- 不要把 DualPath 当成 SelfEcho 现在要实现的推理系统。SelfEcho 不是数据中心 serving 项目。
- 不要把 oMLX 的 GUI 或代码风格搬进来。它是推理服务器管理台，SelfEcho 是个人智能体。
- 不要用“模型很强”替代 harness。DeepSeek 优化不只是换模型名，而是 role、parser、context、trace、repair 的系统工程。
- 不要让长期记忆自动黑箱写入。模型越会整理，越需要 diff 与审计。
- 不要把“陪我想想”做成弱能力模式。它只是回复策略更温和，不代表系统不聪明。

## 推荐实施路线

### Phase 1：DeepSeek ProviderProfile

- 扩展 provider schema，加入 model family、tool_call_format、role bindings、sampling presets。
- GUI 模型页展示 DeepSeek 推荐配置、OpenAI-compatible、自定义模型。
- 保持当前 `providers.local.json` 可迁移。

### Phase 2：ContextBuilder 与 AgentRunner 骨架

- 新建 `selfecho_agent/`。
- 把 `_reply()` 的拼接迁移到 `ContextBuilder`。
- 每次回复生成 run trace。
- 先只支持两条 path：`light` 与 `deliberate`。

### Phase 3：DeepSeek 结构化输出与工具调用解析

- 增加 DeepSeek parser。
- 增加 JSON schema 校验和 repair。
- 先用于模式识别、摘要、记忆候选变更，不急着开放外部副作用工具。

### Phase 4：会话周期整理升级

- 会话结束、固定轮数、token 阈值、手动整理都进入统一 consolidation queue。
- 整理结果先生成候选变更，不直接不可见地改 Markdown。
- GUI 展示候选记忆、diff、原因、来源会话。

### Phase 5：oMLX 作为本地后端模板

- 增加 `Local oMLX` 模板。
- 支持读取模型列表和连接测试。
- 如果 API 可用，展示本地模型健康与延迟。

## 对当前项目的最小下一步

最小、收益最高的下一步不是改前端，而是把 `selfecho_api/server.py::_reply()` 变薄：

```text
_reply()
  -> AgentRunner.respond(session_id, message)
      -> IntentRouter
      -> ContextBuilder
      -> ModelRoleRouter
      -> LLMClient.complete()
      -> RunTrace
```

这样 SelfEcho 会从“一个带记忆的聊天 API”转向“一个真正可演进的智能体运行系统”。DeepSeek 专项优化、oMLX 本地后端、DualPath 启发的上下文缓存策略，都可以自然挂到这个骨架上，而不会把项目再次变成外部项目拼装。
