# IwIw 项目行为指令

## 项目定位

IwIw 是一个记忆驱动的本地个人智能体。“陪我想想 / 非工作状态”是一种对话策略分支，`工作模式` 会逐步承载问题处理、规划、执行与工具能力。

后续开发围绕成熟 agent harness 推进：运行追踪、上下文组装、模式识别、工具注册、权限边界、错误恢复和审计。

## 智能体模块

`selfecho_agent` 的目标是成熟的本地 agent harness：能理解目标、组装上下文、规划步骤、调用工具、观察结果、恢复错误，并在风险动作前等待确认。

当前 agent 代码只能视为早期 tracer bullet，不应沿现有单轮线性实现继续加功能。重构时优先替换错误底座：

- 模型运行时不能依赖 `memory_agent.llm`，应由独立 `ModelGateway` 和 provider adapter 承担。
- `ContextBuilder` 只编排 context source，不直接扫描工作目录或读取底层记忆存储。
- 工作模式需要 plan / act / observe / repair / verify loop，不以关键词 planner 作为目标形态。
- 工具执行必须经过统一 registry、权限 profile、风险分级、确认 gate、trace 和取消机制。
- API 层只是 transport adapter，不承载 agent 决策、工具执行或记忆检索策略。

详细设计见 `docs/agent-harness-architecture.md`。

## 记忆系统

项目记忆系统分成两层：

1. **全量记忆库**
   - 真源数据库：`selfecho_data/sessions.db`
   - 维护模块：`memory_agent`
   - 保存对象：会话消息、工具结果、手动记录、项目事件的 `MemoryEnvelope`
   - 检索方式：向量、关键词、时间、作用域和来源过滤
   - 规则：不经 LLM 判断是否值得保存，先确定性入库，再按需召回
   - 规则：不对材料做预先价值判断，是否进入上下文由检索得分、作用域和预算决定

2. **倾向上下文**
   - 真源数据库：`selfecho_data/sessions.db`
   - 维护模块：`memory_agent`
   - 保存对象：agent 全局倾向 profile、工作目录倾向 profile、当前会话 overlay、倾向观察和版本
   - 生成方式：LLM 从高信号对话、项目事件和手动整理中总结行为 prior
   - 规则：每轮按 agent_global -> workspace -> session overlay 的继承链稳定注入短 profile，影响回应方式、决策默认值和风险阈值
   - 规则：不承担事实归档、事实检索或召回排序
   - 规则：工作目录倾向以规范化工作目录根路径为隔离真源；GUI `project_id` 只是 UI 记录标识
   - 规则：当前会话中新出现但未稳定的倾向先进入 session overlay，不能直接污染 workspace 或 agent_global

`memory/` 只能作为人工可读导出缓存，不是写入真源。

## 检索与上下文

- 每轮默认做轻量候选检索，但只有通过 relevance gate 的 corpus snippets 才能注入。
- 轻量检索不依赖用户主动要求，也不由 LLM 决定是否发生。
- 深度检索用于用户明确回顾历史、agent 缺少决策依据、项目演进追溯或轻量检索预算不足的场景。
- 上下文组装顺序：agent 全局倾向 profile、工作目录倾向 profile、当前会话 overlay、轻量召回片段、会话近期上下文。
- 语义检索必须进入真实 agent 上下文链路，不能只接到 prompt preview。
- 如果 SQLite 记忆库缺失、损坏或不可读，应继续运行，但标记记忆能力不可用。禁止编造记忆事实。

## 倾向整理

倾向整理分三层触发：

- `observe`：用户明确纠正、表达长期偏好、项目出现设计原则时，写入倾向观察并标记 profile dirty。
- `compile`：dirty observations 达到阈值、会话轮数/token 达到阈值、idle/close、用户手动要求整理时，编译短 profile。
- `rebuild`：用户要求重新审视项目、profile 冲突、大规模删除或项目方向根本变化时，从指定范围重建 profile。

固定周期只能作为兜底触发，不是唯一整理入口。

## 写入与审计

- GUI 会话必须保存完整原始消息。
- 全量记忆库保存原始材料，不由 LLM 判断是否值得保存。
- 倾向 profile 只保存稳定行为 prior，并保留来源。
- 写入、编辑、合并、删除和回滚都必须保存修改前版本，并写入 `memory_audit`。
- 数据破坏性操作必须返回 `version_id`、`audit_id`、`changed_rows`。
- `changed_rows == 0` 不能展示为成功。
- 内容变更后必须刷新或标记 dirty：FTS、向量 chunk、导出缓存如存在也要同步。
- 用户明确要求删除、纠正或更新记忆时，优先采用用户最近一次明确更正。

## 开发工作流护栏

1. 不允许只更新文档状态来宣布完成；必须附带命令、测试、API 调用或真实链路 smoke test。
2. 每个跨模块改动先做 tracer bullet：从真实入口到真实输出的最小闭环。
3. 删除模块前必须列清替代能力、调用方、行为差异和验证方式。
4. 引入依赖必须同步 `requirements.txt` / `package.json` / lock 文件，并说明 clean environment 验证方式。
5. 工具执行、记忆删除、文件写入等能力默认按高风险处理，不能因为本地运行就跳过权限与来源校验。
6. 项目初期不为错误底座保留冗余兼容层；当新模块替代旧路径时，应迁移真实调用方并完整剪枝旧接口、旧转发壳和旧文档表述，而不是长期维护兼容性包装。

## 对话策略

- 非工作状态下，先判断用户是否在表达、探索、发散或需要被接住。
- 工作模式下，允许更主动地澄清、规划、拆解步骤和推进任务。
- 当用户情绪很重但同时有明确问题时，优先接住情绪，再进入问题处理。
- “陪我想想”是非工作状态的候选表达，避免直接把用户标记为“倾诉模式”。
