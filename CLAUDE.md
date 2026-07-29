# IwIw 项目行为指令

本文件用于 Claude Code 项目级约束。跨工具通用约束应与 `AGENTS.md` 保持一致；如果本文件、`AGENTS.md`、README 或设计文档互相冲突，先修正文档契约，再继续实现。

## 项目定位

IwIw 是一个记忆驱动的本地个人智能体。“陪我想想 / 非工作状态”是一种对话策略分支；工作模式会逐步承载问题处理、规划、执行、工具能力、错误恢复和审计。

后续开发围绕成熟 agent harness 推进：运行追踪、上下文组装、模式识别、工具注册、权限边界、错误恢复和审计。

## 智能体模块

`selfecho_agent` 当前实现只能视为早期 tracer bullet。不要把单轮线性 orchestrator、关键词 planner、旧 context 拼装或依赖 `memory_agent.llm` 的模型调用当成目标架构继续扩展。

重构优先级：

1. 保持 `AgentRunner` 外部入口稳定。
2. 拆出独立 `ModelGateway` 和 provider adapter。
3. 重写 `ContextBuilder`，只编排 context source，并通过 `MemoryEngine.build_context(...)` 获取记忆上下文。
4. 建立工具 registry、权限 profile、确认 gate、trace 和 cancellation。
5. 将 `AgentOrchestrator` 重写为 plan / act / observe / repair / verify loop。

详细设计见 `docs/agent-harness-architecture.md`。

## 当前架构

- `memory_agent/`：记忆层，负责全量材料入库、轻量召回、倾向编译、版本和审计。
- `selfecho_session/`：会话层，保存 GUI 会话、原始消息、滚动摘要、会话搜索和回放。
- `selfecho_agent/`：agent harness 层，负责模式识别、上下文组装、运行追踪和问题处理流程。
- `selfecho_api/` + `web/`：本地 API 与 GUI。
- `memory/`：人工可读导出缓存，不是写入真源。

## 记忆系统不变量

1. 全量记忆库保存原始 `MemoryEnvelope`，LLM 不裁决原始材料是否入库。
2. 倾向上下文保存 agent 全局 / 工作目录 / 当前会话 overlay profile，影响 agent 回应方式、决策默认值和风险阈值。
3. 全量记忆库不对材料做预先价值判断；是否进入上下文由检索得分、作用域和预算决定。
4. 倾向 profile 不承担事实归档、事实检索或召回排序。
5. 每轮默认做轻量候选检索，只有通过 relevance gate 的片段才注入。
6. 倾向整理由 observe / compile / rebuild 三层触发，固定周期只是兜底。
7. GUI 会话原始消息必须保留，摘要和 profile 不能替代原始消息。
8. 写入、编辑、合并、删除、回滚必须保存变更前版本并写入审计。
9. 内容变更后必须刷新或清理 FTS、向量 chunk 和导出缓存。
10. 语义检索必须进入真实 agent 上下文链路，不能只接到 prompt preview。

## 开发工作流护栏

1. 不允许只更新文档状态来宣布完成；必须附带命令、测试、API 调用或真实链路 smoke test。
2. 每个跨模块改动先做 tracer bullet：从真实入口到真实输出的最小闭环。
3. 删除模块前必须列清替代能力、调用方、行为差异和验证方式。
4. 数据破坏性操作必须经过统一 mutation 入口，返回 `version_id`、`audit_id`、`changed_rows`；`changed_rows == 0` 不能写成功状态。
5. 引入依赖必须同步 `requirements.txt` / `package.json` / lock 文件，并说明 clean environment 验证方式。
6. 工具执行、记忆删除、文件写入等能力默认按高风险处理，不能因为本地运行就跳过权限与来源校验。

## 设计边界

- 当前记忆系统架构见 `docs/memory-system-architecture.md`。
- 当前智能体 harness 架构见 `docs/agent-harness-architecture.md`。
- 系统总览见 `docs/iwiw-system-design.md`。
- API 层是 adapter，不承载记忆检索、倾向编译或 agent 决策策略。

## 对话策略

- 非工作状态下，先判断用户是否在表达、探索、发散或需要被接住。
- 工作模式下，允许更主动地澄清、规划、拆解步骤和推进任务。
- 当用户情绪很重但同时有明确问题时，优先接住情绪，再进入问题处理。
- “陪我想想”是非工作状态的候选表达，避免直接把用户标记为“倾诉模式”。
