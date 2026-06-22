# IwIw 项目行为指令

本文件是 Claude Code 在 IwIw 项目中的项目级约束。跨工具通用约束应与 `AGENTS.md` 保持一致；如果本文件、`AGENTS.md`、README 或设计文档互相冲突，先修正文档契约，再继续实现。

## 项目定位

IwIw 当前是一个记忆驱动的本地个人智能体。“陪我想想 / 非工作状态”是一种对话策略分支；工作模式会逐步承载问题处理、规划、执行、工具能力、错误恢复和审计。

后续开发围绕成熟 agent harness 推进：运行追踪、上下文组装、模式识别、工具注册、权限边界、错误恢复和审计。

## 当前架构

- 长期事实记忆真源：`memory_agent/db.py` 使用 `selfecho_data/sessions.db` 中的 SQLite 表，包含 `memories`、`memory_chunks`、`memories_fts`、`memory_pending_actions`、`memory_versions`、`memory_audit`。
- Markdown 记忆目录：`memory/` 只能视为历史遗留或可读导出缓存，不能作为写入真源。
- 会话记忆层：`selfecho_session/` 使用同一 `sessions.db` 保存 GUI 会话、原始消息、滚动摘要、周期摘要、会话搜索和回放。
- 长期记忆模块：`memory_agent/` 负责长期记忆 CRUD、BGE 本地向量嵌入、FTS5 回退、混合检索、高信号触发提取和维护候选。
- 本地 API：`selfecho_api/` 提供 REST CRUD、SSE 流和 pending-actions 审批。
- GUI：`web/` 是主要用户入口；旧 Claude Code 历史只作为 legacy 导入资料。

## 记忆系统不变量

1. 长期事实记忆以 SQLite 为真源；Markdown 只能是导出缓存。
2. GUI 会话原始消息必须保留，摘要不能替代原始消息。
3. L0/L1 始终加载；L2/L3 只在话题相关或用户要求回顾时检索。
4. 写入、编辑、合并、归档、删除、回滚必须保存变更前版本并写入审计。
5. 删除不能导致历史版本一起丢失；如果 schema 做不到，先修 schema 或 tombstone 方案。
6. 内容变更后必须刷新或清理 FTS、向量 chunk、Markdown 缓存。
7. 语义检索必须进入真实 agent 上下文链路，不能只接到 prompt preview。

## 开发工作流护栏

1. 不允许只更新文档状态来宣布 phase 完成；必须附带命令、测试、API 调用或真实链路 smoke test。
2. 每个跨模块改动先做 tracer bullet：从真实入口到真实输出的最小闭环。
3. 删除旧模块前必须列替代矩阵：旧能力、新能力、调用方、行为差异、验证方式。
4. 数据破坏性操作必须经过统一 mutation 入口，返回 `version_id`、`audit_id`、`changed_rows`；`changed_rows == 0` 不能写成功状态。
5. 引入依赖必须同步 `requirements.txt` / `package.json` / lock 文件，并说明 clean environment 验证方式。
6. WebSocket、工具执行、记忆删除等能力默认按高风险处理，不能因为本地运行就跳过权限与来源校验。

## 设计边界

- 当前记忆系统架构见 `docs/memory-system-architecture.md`。
- WebSocket 是传输层，不属于 `memory_agent` 内部实现；后端实现应放在 `selfecho_api` 或独立 transport 模块。
- 旧历史库只作可导入背景资料，不定义 IwIw 的产品边界或运行生命周期。

## 对话策略

- 非工作状态下，先判断用户是否在表达、探索、发散或需要被接住。
- 工作模式下，允许更主动地澄清、规划、拆解步骤和推进任务。
- 当用户情绪很重但同时有明确问题时，优先接住情绪，再进入问题处理。
- “陪我想想”是非工作状态的候选表达，避免直接把用户标记为“倾诉模式”。

## 记忆分级

| 级别 | 标签 | 说明 |
|------|------|------|
| L0 | `core` | 核心身份、稳定认知模式、长期交流偏好，始终加载 |
| L1 | `important` | 健康、关系、重大决策、长期压力源，始终加载 |
| L2 | `normal` | 日常偏好、阶段计划、一般事实，按话题触发 |
| L3 | `archive` | 过时事实、历史事件、低频参考，深度检索按需 |
