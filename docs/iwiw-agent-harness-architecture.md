# IwIw Agent Harness Architecture

本文定义 IwIw 下一阶段的 agent harness。目标是避免在 `runner.py` 上继续堆功能，而是形成一套逻辑简洁、模块边界清晰、功能完整且可以逐步落地的工作流。

## 参考资料

### OpenCode

参考来源：

- `anomalyco/opencode` README：OpenCode 是 open source AI coding agent。
- OpenCode agents 文档：内置 `build` 与 `plan` agent，`build` 是全开发能力，`plan` 是只读分析；用户可创建全局或项目级 agent，并选择 permissions。
- OpenCode 源码目录：
  - `packages/opencode/src/agent/agent.ts`
  - `packages/opencode/src/session/processor.ts`
  - `packages/opencode/src/session/revert.ts`
  - `packages/opencode/src/tool/*`
  - `packages/opencode/src/permission/*`

可吸收思想：

- agent 是配置化的角色，不是硬编码 prompt。
- plan/build/review/debug/docs 是权限和任务路径的组合。
- session processor 把 LLM stream、reasoning、tool input、tool result、step finish、overflow compaction 都记录成事件。
- revert 基于 snapshot、patch、message timeline 回退。

不照搬：

- 不引入 Effect 体系。
- 不把 IwIw 拆成 TypeScript monorepo。
- 不直接复制 OpenCode 的 agent 文件格式。

### Codex

参考来源：

- `openai/codex` README：Codex CLI 是本地运行的 coding agent。
- `openai/codex` 源码目录：
  - `codex-rs/core/src/tools/registry.rs`
  - `codex-rs/core/src/tools/orchestrator.rs`
  - `codex-rs/core/src/config/permissions.rs`
  - `codex-rs/core/src/exec_policy.rs`
  - `codex-rs/core/src/turn_metadata.rs`
  - `codex-rs/core/src/turn_timing.rs`
  - `codex-rs/core/src/turn_diff_tracker.rs`
  - `codex-rs/app-server-protocol/schema/json/*Approval*.json`

可吸收思想：

- tool registry、tool orchestrator、runtime、sandbox、approval、telemetry 分层明确。
- 权限 profile 分为 read-only、workspace-write、danger-full-access，并可扩展。
- 工具执行顺序是 approval -> sandbox selection -> attempt -> retry/escalation。
- turn metadata、timing、diff tracker 独立记录，不和模型回复逻辑混在一起。

不照搬：

- 不在 Python 阶段实现完整 OS sandbox。
- 不把 Codex 的 approval/sandbox 名称作为产品概念。
- 不直接复制 Rust 协议层。

### Claude Code

Claude Code 本体不是开源 harness，因此只作为产品行为参考：

- 项目级上下文和对话级 thread 分开。
- 项目规则由 durable instruction 承载，thread 记录一次任务过程。
- 权限、工具、hook、MCP、记忆和上下文压缩是独立机制。

IwIw 不继续把 Claude Code 当输入源或生命周期中心。

### Everything Claude Code（ECC）

参考来源：

- `mit-network/everything-claude-code` README、shortform / longform / security guides。
- 重点文件：
  - `skills/agent-harness-construction/SKILL.md`
  - `skills/continuous-learning-v2/SKILL.md`
  - `skills/iterative-retrieval/SKILL.md`
  - `skills/strategic-compact/SKILL.md`
  - `skills/context-budget/SKILL.md`
  - `skills/eval-harness/SKILL.md`
  - `hooks/hooks.json`
  - `scripts/harness-audit.js`
  - `scripts/hooks/session-start.js`
  - `scripts/hooks/session-end.js`
  - `scripts/hooks/run-with-flags.js`

可吸收思想：

- harness 是性能系统：上下文预算、工具治理、质量门、持续学习、安全边界和成本控制都属于运行时能力。
- lifecycle event 比单个 prompt hook 更稳定：SessionStart、Stop、PreCompact、PreToolUse、PostToolUse 分别承担不同职责。
- 工具 observation 必须结构化，至少包含 status、summary、next_actions、artifacts、失败原因、安全重试和停止条件。
- continuous learning 应先形成原子候选 instinct，带 trigger、action、confidence、evidence、scope，再按项目/全局边界晋升。
- 子 agent / 后台观察者必须有节流、lease、idle guard、re-entrancy guard、tail sampling 和超时，否则容易自循环或污染记忆。
- eval-first 和 quality gate 应成为 harness 回归测试，而不是上线前人工检查。

不照搬：

- 不复制 ECC 的 Claude Code plugin / slash command 生态。
- 不把 IwIw 做成面向工程师的配置包。
- 不在当前阶段启用无守护的后台 observer。
- 不让技能、MCP 或外部仓库内容直接获得长期记忆写入权。

## 当前问题

早期 `AgentRunner` 曾同时负责：

- intent routing
- policy assessment
- context building
- run trace 写入
- model retry
- timeline 写入
- final trace 组装

当前代码已把 `AgentRunner` 收敛为兼容外壳，并将主流程放入 `AgentOrchestrator`。下一步要防止的问题，是继续把 planner、tool、rollback、memory proposer、eval、learning 等能力都塞进 orchestrator，使它变成新的“万能 runner”。

当前 `SessionMemoryService` 也在增长：

- GUI 会话
- 项目与普通对话
- 摘要与整理
- 记忆审计
- agent run
- agent event
- agent timeline

它可以继续作为 persistence gateway，但不应承担 harness 业务决策。

## 设计目标

IwIw harness 应满足：

- 一个主编排器：所有 turn 都经过同一个 orchestrator。
- 模块只做一件事：router 只判断意图，policy 只判断权限，context 只组装上下文。
- 所有行动可观察：每个 step 都进入 trace/timeline。
- 工具能力可扩展：先只读，再写入，再联网，再完全访问。
- 生命周期可治理：session start、turn done、tool before/after、compact before、session end 都能触发独立处理。
- 上下文有预算：每轮记录 context sections、估算大小、裁剪原因和重复注入风险。
- 工具有 observation contract：工具结果不仅给模型看，也给 trace、GUI、恢复逻辑和 eval 使用。
- 行为可测试：router、policy、context、tool、memory candidate 都能被 deterministic eval 回放。
- 记忆双轨：个人长期记忆和工作目录记忆分开。
- 支持回溯：每次可修改操作都能关联 timeline、backup、rollback ref。
- GUI 低干扰展示：默认展示摘要，展开看完整 trace。

## 主循环

```text
UserTurn
  -> Orchestrator.start
  -> Lifecycle.emit(turn_start)
  -> Router.classify
  -> Policy.assess
  -> ContextBuilder.build
  -> ContextBudget.measure
  -> Planner.plan_or_clarify
  -> ToolPlanner.select_tools
  -> ApprovalGate.decide
  -> Lifecycle.emit(tool_before)
  -> ToolOrchestrator.execute
  -> Lifecycle.emit(tool_after)
  -> ModelGateway.complete
  -> MemoryBridge.propose
  -> Evaluator.mark_optional_checkpoints
  -> TraceRecorder.finish
  -> Lifecycle.emit(turn_done)
```

第一阶段不必全部执行工具，但接口应按完整链路设计。

## 模块边界

### `core/types.py`

定义 harness 共享数据结构：

- `AgentRequest`
- `AgentResult`
- `HarnessStep`
- `ModelCallConfig`
- `ExecutionPlan`
- `ToolCallPlan`
- `ToolObservation`
- `ApprovalRequirement`
- `RollbackRef`
- `ContextBudgetReport`

这些类型是内部契约，不直接绑定 FastAPI 或 Vue。

### `core/orchestrator.py`

唯一主编排器。

职责：

- 串联 router、policy、context、planner、tool planner、executor、model gateway、memory bridge、trace recorder。
- 决定本轮是否直接回复、先澄清、等待批准或执行工具。
- 不直接写 SQL。
- 不直接读写 Markdown 记忆。
- 不直接实现某个工具。

### `trace.py`

封装 run trace 和 timeline persistence。

职责：

- start run
- record step
- record event
- record timeline
- finish run

它调用 `SessionMemoryService`，但上层不直接碰 service 的 agent trace 方法。

### `lifecycle.py`

封装 harness 生命周期事件。

职责：

- emit `session_start`
- emit `turn_start`
- emit `tool_before`
- emit `tool_after`
- emit `compact_before`
- emit `session_end`
- later: 支持 profile 控制和按事件禁用处理器

它不直接写长期记忆，只把事件交给 session / trace / consolidation queue。

### `context_budget.py`

记录上下文预算与裁剪原因。

职责：

- 估算每个 context section 的大小。
- 标记 L0/L1、L2/L3、项目上下文、工具观察、最近消息等来源。
- 检测重复注入和过大段落。
- 给 GUI 提供“本轮上下文来源”摘要。
- later: 根据模型窗口和角色自动裁剪或建议压缩。

### `model.py`

封装模型调用。

职责：

- complete
- retry
- later: fallback candidate selection
- later: structured output repair

它不决定业务策略，只返回结果或错误。

### `planner.py`

工作模式的结构化思考层。

职责：

- 生成目标、已知信息、缺失问题、步骤、风险、验收标准。
- 信息不足时要求澄清。
- 不执行工具。

初版可以是 deterministic planner，之后再接 LLM structured output。

### `tools/registry.py`

工具注册表。

职责：

- 注册工具 spec。
- 暴露工具名、风险等级、输入 schema、是否可并行、输出 observation schema。
- 返回具体 runtime。

### `tools/orchestrator.py`

工具执行编排器。

职责：

- approval -> backup/snapshot -> execute -> record -> retry/recover。
- 每个 error path 返回 root_cause_hint、safe_retry 和 stop_condition。
- 执行前必须得到 policy gate 的结果。

### `tools/workspace.py`

第一批只读工具：

- list directory
- read text file
- search files
- git status read-only

### `memory_bridge.py`

负责双轨记忆的接口。

职责：

- 个人长期记忆候选。
- 工作目录记忆候选。
- 会话结束整理。
- 上下文不足时压缩。

不直接决定写入权限，写入权限由 policy/settings 决定。

### `evaluator.py`

负责 harness 行为回归测试。

职责：

- 回放固定 user turn。
- 断言 router、policy、context sections、tool calls 和 memory candidate 是否符合预期。
- 记录 pass/fail、失败原因和相关 run trace。
- 支持无需真实模型的 deterministic eval。

第一批 eval 应覆盖：

- 普通聊天不声称能看到项目文件。
- 工作目录问题必须调用只读 workspace observation。
- 写入/删除/联网/记忆修改必须触发确认或候选变更。
- 用户纠正记忆时，新事实优先。
- safety path 先承接情绪，再进入问题处理。

### `learning_bridge.py`

负责从会话和工具观察中生成候选 pattern。

职责：

- 读取 session / trace / tool observation。
- 生成原子候选：trigger、action、confidence、evidence、scope。
- 区分个人长期记忆、工作目录记忆、项目约定和临时观察。
- 只生成候选，不直接写入 `memory/`。
- later: 多次确认后晋升为 skill、工作记忆或长期偏好。

## 权限模型

采用四层：

- `read_only`：只读，适合计划和探索。
- `guided`：可提出修改计划，执行前确认。
- `workspace`：可在工作目录内写入，危险操作需要备份。
- `full_access`：完全访问，由用户显式开启；仍必须记录 trace 和 rollback ref。

这对应但不照搬 Codex 的 read-only/workspace/danger-full-access。

OpenCode 的 build/plan 思想映射为：

- IwIw `计划模式`：read_only。
- IwIw `工作模式`：guided 或 workspace。
- IwIw `完全访问`：full_access。
- IwIw `陪我想想`：无工具或只读上下文。

## 状态机

```text
idle
  -> running
  -> waiting_approval
  -> executing_tool
  -> recovering
  -> completed
  -> degraded
  -> cancelled
```

所有状态变化都写入 timeline。

## 回溯设计

每个可修改 step 都生成：

- `timeline_id`
- `run_id`
- `turn_idx`
- `target`
- `before_ref`
- `after_ref`
- `diff`
- `rollback_strategy`

初版支持记忆文件回滚；后续支持工作目录文件 patch rollback。

## 第一阶段落地范围

本轮重构只做：

- 新增 core 类型。
- 新增 orchestrator。
- 新增 trace recorder。
- 新增 model gateway。
- 保留 `AgentRunner.respond()` 作为兼容外壳。
- 不引入真正写文件工具。
- 不改变现有 API 返回结构。

验收：

- `selfecho_api` 仍调用 `AgentRunner.respond()`。
- `AgentRunner` 内部委托 `AgentOrchestrator.run()`。
- Python 编译通过。
- 前端 build 通过。

## 第二阶段

已开始落地：

- 新增 `selfecho_agent/planner.py`。
- 新增 `selfecho_agent/tools/registry.py`、`tools/orchestrator.py`、`tools/workspace.py`。
- `AgentOrchestrator` 已接入 planner、tool registry、只读 workspace tool observations。
- 在“工作目录下能看到什么”等场景中，优先通过 `workspace.list_directory` 读取会话绑定项目目录，而不是依靠 prompt 猜测。
- `workspace.read_text_file` 支持读取当前工作目录内的文本文件，并限制大小、文件类型和路径边界。
- planner 和 tool results 会写入 run trace / timeline，并注入本轮模型上下文。

仍需继续：

- 前端审查面板把 planner/tool observation 做成更清晰的可展开块。
- 增加 read-only `workspace.search_files` 和 `git.status`。
- 将用户选择的“工作模式 / 计划模式 / 陪我想想”显式传给后端 router。
- 增加无需真实模型的 harness 单元测试。
- 扩展 `ToolResult` 为完整 observation contract。
- 增加 context budget 记录和 GUI 来源预览。
- 把现有 Claude hook 记忆抽取收敛到 IwIw lifecycle / consolidation queue。

## 第二阶段加固：ECC 吸收项

此阶段不是增加更多工具，而是提升单 agent 运行质量。

任务：

- 新增 lifecycle event 层，先只记录事件，再逐步接入整理队列和 GUI 展示。
- 新增 context budget report，记录每轮 section 来源、估算大小、裁剪原因。
- 新增 harness eval，覆盖 router、policy、context builder、tool orchestrator、memory candidate。
- 工具返回统一 observation contract：`status`、`summary`、`next_actions`、`artifacts`、`root_cause_hint`、`safe_retry`、`stop_condition`。
- 会话整理输出先生成候选 instinct / memory candidate，带 confidence 和 evidence。
- 后台整理和观察者在没有 lease、idle guard、re-entrancy guard、timeout 前不进入自动运行。

完成标准：

- 不依赖真实模型也能验证核心 harness 行为。
- GUI 能解释“本轮为什么读了这些上下文、用了这些工具”。
- 工具失败后能安全恢复或停止，不靠模型临场猜。

## 第二阶段加固落地记录（2026-06-17）

本轮已完成：

- `ToolResult` 扩展为 observation contract，保留原有 `ok / summary / content / error`，新增 `status`、`next_actions`、`artifacts`、`root_cause_hint`、`safe_retry`、`stop_condition`。
- `ToolOrchestrator` 的未注册工具、权限不足、handler exception 都返回可恢复失败信息。
- `workspace.search_files` 与 `git.status` 已作为只读工具注册，继续沿用工作目录边界、忽略规则和大小限制。
- `DeterministicPlanner` 能为项目内搜索和 git 状态请求生成只读 tool call。
- `ContextBudget` 独立成模块，记录 section 来源、字符数、估算 token 和重复/过大告警；run trace 和 done trace 都携带预算报告。
- 新增 `HarnessEvaluator` 与 `tests/harness_eval.py`，覆盖普通对话不调用工作区工具、工作目录观察、本地搜索低风险、写入需确认、工具失败契约、工作区搜索边界、git 状态恢复契约、上下文预算来源。

阶段 review：

- 架构收益：这轮没有扩大 agent 自主性，而是把“观察、预算、失败恢复、回放测试”补齐。工具能力增加了两个，但它们都被限制在只读范围内，且结果进入统一 observation contract。
- 复杂度变化：`AgentOrchestrator` 仍然承担了较多串联职责，但预算估算、eval、工具实现都没有塞进 orchestrator 本体；目前还不需要完全重构。
- 风险点：`AgentOrchestrator._format_tool_content()` 已经开始感知具体工具名称，后续如果工具继续增加，应抽成 ToolObservationFormatter 或让工具提供自己的 display adapter。
- 不应继续做的事：暂时不引入后台 observer、不加入写文件工具、不做 ReAct 自动循环、不让整理候选直接写入长期记忆。
- 下一阶段建议：优先做 lifecycle event 薄层和显式模式输入，让 GUI 选择的“陪我想想 / 计划 / 工作”进入 router trace；之后再考虑 trace 展示面板，而不是继续堆工具。

## 第三阶段

- 写文件工具。
- diff preview。
- backup。
- rollback。
- git read/write 分级。
- memory bridge 双轨。

## 不做什么

- 不在当前阶段实现完整 sandbox。
- 不把 prompt 当 harness。
- 不让前端直接决定工具行为。
- 不让长期个人记忆和工作目录记忆混在一起。
- 不继续暴露旧项目名或旧输入源作为产品概念。
- 不照搬 ECC 的插件/命令/多 agent 数量规模。
- 不在无节流和无审计条件下启用后台 observer 或连续自动循环。
