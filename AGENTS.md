# IwIw 项目行为指令

## 项目定位

IwIw 当前是一个记忆驱动的本地个人智能体。“陪我想想 / 非工作状态”是一种对话策略分支，`工作模式` 会逐步承载问题处理、规划、执行与工具能力。

后续开发应围绕成熟 agent harness 推进：运行追踪、上下文组装、模式识别、工具注册、权限边界、错误恢复和审计。

## 记忆系统

本项目采用两套互补记忆：

1. SQLite 长期事实记忆
   - 真源数据库：`selfecho_data/sessions.db`
   - 维护模块：`memory_agent/db.py`
   - 主要表：`memories`、`memory_chunks`、`memories_fts`、`memory_pending_actions`、`memory_versions`
   - 分级：`core / important / normal / archive`
   - `memory/` 仅可视为历史遗留或可读导出缓存，不再是写入真源。

2. IwIw 会话记忆层
   - 数据库：`selfecho_data/sessions.db`
   - 旧历史导入库：`selfecho_data/legacy/claude_code_history.db`
   - 负责 GUI 会话、原始消息、滚动摘要、周期摘要、会话搜索和回放。

旧 Claude Code 历史只是可导入背景资料，不再定义 IwIw 的产品边界或运行生命周期。

## 启动时

从 SQLite 长期记忆表加载 L0（core）和 L1（important）记忆完整内容。

如果 SQLite 记忆库缺失、损坏或不可读，应继续运行，但标记记忆检索不可用。禁止编造记忆事实。`memory/MEMORY.md` 如存在，只能作为人工可读索引缓存。

## 检索策略

1. 启动时仅加载 L0（core）和 L1（important）记忆。
2. 当前话题涉及个人事实、长期计划、健康、关系、偏好或历史决策时，优先检索 SQLite 长期记忆。
3. 当前话题涉及过去对话、时间上下文、某段 GUI 会话或导入历史时，使用 IwIw 会话记忆层检索。
4. L2（normal）和 L3（archive）按话题触发，不应默认全文注入。
5. 长期事实优先由 `memory_agent` 维护；会话背景优先由 `selfecho_session` 提供。
6. 语义检索必须进入真实 agent 上下文链路，不能只接到 prompt preview。

## 记忆写入与维护

- GUI 会话保存完整原始消息。
- 会话整理应在会话周期结束、固定轮数、阈值触发或用户手动触发时进行。
- 长期记忆写入、编辑、合并、归档、删除和回滚都必须保存修改前版本到 `memory_versions`，并写入 `memory_audit` 审计记录。
- 删除操作不能导致历史版本一起丢失；当前 schema 若不能保证，应先修 schema 或 tombstone 方案。
- 内容变更后必须刷新或标记 dirty：FTS、向量 chunk、Markdown 缓存如存在也要同步。
- 不确定是否属于稳定事实时，优先生成候选或保持观察，不要急着写入。
- 用户明确要求删除、纠正或更新记忆时，优先采用用户最近一次明确更正。

## 开发工作流护栏

1. 不允许只更新文档状态来宣布 phase 完成；必须附带命令、测试、API 调用或真实链路 smoke test。
2. 每个跨模块改动先做 tracer bullet：从真实入口到真实输出的最小闭环。
3. 删除旧模块前必须列替代矩阵：旧能力、新能力、调用方、行为差异、验证方式。
4. 数据破坏性操作必须经过统一 mutation 入口，返回 `version_id`、`audit_id`、`changed_rows`；`changed_rows == 0` 不能写成功状态。
5. 引入依赖必须同步 `requirements.txt` / `package.json` / lock 文件，并说明 clean environment 验证方式。
6. WS、工具执行、记忆删除等能力默认按高风险处理，不能因为本地运行就跳过权限与来源校验。

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
