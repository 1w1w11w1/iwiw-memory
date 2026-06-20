# IwIw 项目行为指令

本文件是 Claude Code 在 IwIw 项目中的项目级约束。跨工具通用约束应与 `AGENTS.md` 保持一致；如果本文件、`AGENTS.md`、README 或设计文档互相冲突，必须先指出冲突并修正文档契约，不得混用旧规则继续实现。

IwIw 是一个记忆驱动的本地个人智能体。“陪我想想 / 非工作状态”是对话策略分支；工作模式逐步承载问题处理、规划、执行、工具和审计能力。项目长期目标是成熟 agent harness，而不是纯聊天应用。

## 当前架构

- **长期记忆真源**：`memory_agent/db.py` 使用 `selfecho_data/sessions.db` 中的 SQLite 表，包含 `memories`、`memory_chunks`、`memories_fts`、`memory_pending_actions`、`memory_versions` 等。
- **Markdown 记忆目录**：`memory/` 目前只能视为历史遗留/可读缓存。不要把它当写入真源，不要绕过 SQLite 直接改长期记忆。
- **会话记忆层**：`selfecho_session/` 使用同一个 `sessions.db` 保存 GUI 会话、原始消息、滚动摘要、周期摘要、会话搜索和回放。
- **长期记忆模块**：`memory_agent/` 负责长期记忆 CRUD、BGE 本地向量嵌入、FTS5 回退、混合检索、高信号触发提取和维护候选。
- **本地 API**：`selfecho_api/` 提供 REST CRUD、SSE 流和 pending-actions 审批。
- **GUI**：`web/` 是主要用户入口；旧 Claude Code 历史只作为 legacy 导入资料，不再定义产品边界。

## 记忆系统不变量

修改长期记忆系统前，先确认以下不变量；不能确认时先修设计，不要先写代码。

1. **真源**：长期事实记忆以 SQLite 为真源；Markdown 只能是导出/缓存。
2. **原始记录**：GUI 会话原始消息必须保留，摘要不能替代原始消息。
3. **分级加载**：L0/L1 始终加载；L2/L3 只在话题相关或用户要求回顾时检索。
4. **版本与审计**：写入、编辑、合并、归档、删除、回滚必须保存修改前版本，并写入审计事件。
5. **删除可恢复**：删除不能导致历史版本一起丢失；如果当前 schema 做不到，先修 schema 或 tombstone 方案。
6. **索引同步**：内容变更后必须刷新或标记 dirty：FTS、向量 chunk、Markdown 缓存如存在也要同步。
7. **真实链路**：记忆检索必须进入真实 agent 上下文链路，不能只接到 prompt preview。

## 开发工作流护栏

1. 不允许只更新文档状态来宣布 phase 完成；必须附带命令、测试、API 调用或真实链路 smoke test。
2. 每个跨模块改动先做 tracer bullet：从真实入口到真实输出的最小闭环。
3. 删除旧模块前必须列替代矩阵：旧能力、新能力、调用方、行为差异、验证方式。
4. 数据破坏性操作必须经过统一 mutation 入口，返回 `version_id`、`audit_id`、`changed_rows`；`changed_rows == 0` 不能写成功状态。
5. 引入依赖必须同步 `requirements.txt` / `package.json` / lock 文件，并说明 clean environment 验证方式。
6. 不要把 helper 或 preview 的成功当成系统成功；要验证 `AgentOrchestrator`、API、DB、GUI 真实路径。
7. 修复完成后再更新 phase 文档；未验证的项目只能标为“未验证”或“骨架完成”。

## 审查修复状态

近期 review 已记录在 `docs/memory-system-review-and-repair-plan.md`。继续开发前用这里作为快速验收清单：

- 已修：`AGENTS.md` 与本文件统一为 SQLite 真源，`.history` 不再作为备份机制。
- 已修：破坏性记忆操作改走 `memory_versions` 快照和 `memory_audit` 审计。
- 已修：pending `merge` 不再被假执行；自动维护当前只生成保守 archive 候选。
- 已修：L2/L3 语义检索进入真实 `AgentOrchestrator` → `ContextBuilder` 回复链路。
- 已修：新写入、编辑、归档、合并、回滚相关路径刷新或清理向量 chunk。
- 已修：legacy migration 重新绑定为 `SessionMemoryService.migrate_legacy()` 方法。
- 已修：`requirements.txt` 已声明 `numpy` 与 `sentence-transformers`。
- 待做：WebSocket 统一通道仍停留在设计阶段；后端实现应放在 `selfecho_api` 或独立 transport 模块。

## WS 设计边界

WebSocket 是传输层 seam，不是 `memory_agent` 的内部实现。

- 后端 WS 管理器应放在 `selfecho_api` 或独立 transport 模块，不要放进 `memory_agent`。
- 先做事件推送，再迁移控制命令；不要一开始替换全部 REST/SSE。
- 本地服务也要做 Origin allowlist 和启动 token；不能因为跑在 `127.0.0.1` 就无认证。
- `memory.*`、`agent.*`、`chat.*`、`system.*` 只能作为 handler adapter 注册到 WS 路由，业务逻辑仍归属原模块。

## 对话策略

- 非工作状态优先接住表达、整理感受，不急着拆任务。
- 工作模式优先澄清、规划、拆解和推进。
- 当用户情绪很重但同时有明确问题时，先承接情绪，再进入问题处理。
- “陪我想想”是非工作状态的候选表达，不要直接把用户标记为“倾诉模式”。

## 记忆分级

| 级别 | 标签 | 说明 |
|------|------|------|
| L0 | `core` | 核心身份、稳定认知模式、长期交流偏好 |
| L1 | `important` | 健康、关系、重大决策、长期压力源 |
| L2 | `normal` | 日常偏好、阶段计划、一般事实 |
| L3 | `archive` | 过时事实、历史事件、低频参考 |
