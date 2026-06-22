# 记忆系统当前架构

本文件记录当前成型方向，并替代旧的阶段计划、修复复盘和 Markdown 向量方案文档。后续实现以这里和 `AGENTS.md` 的不变量为准。

## 目标形态

IwIw 采用两套互补记忆：

- 长期事实记忆：由 `memory_agent` 维护，真源是 `selfecho_data/sessions.db` 中的 SQLite 表。
- 会话记忆：由 `selfecho_session` 维护，同库保存 GUI 会话、原始消息、摘要、搜索和回放。

启动时只完整加载 L0 `core` 与 L1 `important`。L2 `normal` 和 L3 `archive` 必须按话题触发检索，不能默认全文注入。涉及个人事实、健康、关系、长期计划、偏好和历史决策时优先检索长期事实记忆；涉及过去对话、某段 GUI 会话或导入历史时优先检索会话记忆层。

## 数据真源

| 数据 | 真源 | 维护模块 | 说明 |
|---|---|---|---|
| 长期事实 | `selfecho_data/sessions.db` / `memories` | `memory_agent/db.py` | L0-L3 分级事实记忆 |
| 检索 chunk | `memory_chunks` | `memory_agent/embedding.py` + `memory_agent/retrieval.py` | 语义检索与混合检索输入 |
| 关键词索引 | `memories_fts` | SQLite FTS trigger | 关键词检索回退 |
| 待确认动作 | `memory_pending_actions` | `memory_agent/db.py` | 保守维护候选 |
| 版本 | `memory_versions` | mutation 入口 | 变更前快照 |
| 审计 | `memory_audit` | mutation 入口 | 操作、来源、原因和结果 |
| GUI 会话 | session tables | `selfecho_session/service.py` | 原始消息和摘要 |
| Markdown | `memory/` | 导出缓存 | 只读参考，不是写入真源 |

## Mutation 契约

破坏性或可见内容变更必须返回：

- `version_id`
- `audit_id`
- `changed_rows`

`changed_rows == 0` 表示没有真实变更，调用方不能展示成功状态。删除、归档、合并和回滚都必须保留可恢复版本，并写入审计事件。

## 替代矩阵

| 旧能力/设计 | 新能力/设计 | 调用方 | 行为差异 | 验证方式 |
|---|---|---|---|---|
| `memory/*.md` 作为长期记忆真源 | SQLite `memories` 表 | `memory_agent`、API、agent context builder | Markdown 不再是写入路径 | `python tests\memory_system_eval.py` |
| `memory/.history/` 文件备份 | `memory_versions` + `memory_audit` | mutation 结果函数、回滚 API | 以版本和审计 id 追踪，而不是文件路径 | mutation eval 检查 `version_id` / `audit_id` |
| 旧 `vectorizer.py` / `vector_store.py` 方案 | `embedding.py` + `memory_chunks` + `retrieval.py` | `refresh_memory_vectors`、`hybrid_search` | 向量 chunk 存在 SQLite，不再独立维护文件型向量库 | 检索 smoke / eval |
| `memory/*.md` 一次性迁移入口 | 显式导入脚本或人工迁移流程 | 当前无调用方 | 删除运行时代码，避免误触发旧真源导入 | `rg migrate_from_markdown`、`compileall` |
| `rebuild_memory_index` 作为真源索引 | SQLite 到 Markdown 的可读缓存导出 | Web、API、MCP | 保留按钮/工具，但只导出缓存，不参与写入 | API import smoke、前端构建 |

## 保留边界

- `rebuild_index()` 暂时保留，因为 Web、API 和 MCP 仍有真实调用方；它的语义是“导出 SQLite 记忆索引缓存”。
- 旧 Claude Code 历史导入仍保留在 `selfecho_session`，它是可导入背景资料，不定义 IwIw 产品边界。
- WebSocket 仍是未来 transport 设计，不能放进 `memory_agent` 内部。
