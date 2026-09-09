# 记忆系统项目行为指令

## 项目定位

本项目是一个**独立的本地长期记忆系统**：核心为 SQLite 分级事实记忆（core/normal/archive），自带 CLI 对话工作台（`memory_agent/chat.py`）作为使用与验证界面，并以 MCP（`memory_agent/mcp_server.py`）对外提供完整工具面。

第二阶段再以插件/MCP 形式接入 DSH；在此之前不依赖任何外部 harness。自研 agent harness、会话层、Web GUI 已移除，不得复活。

## 记忆系统不变量

1. 长期事实记忆以 `data/memory.db` 的 SQLite 为唯一真源；任何 Markdown/文件导出都不是写入真源。
2. 记忆分级为三值：core（必须载入，对话启动注入）/ normal（按需载入，话题触发检索）/ archive（归档状态，不参与常规检索与维护候选）。
3. 写入、编辑、合并、归档、删除、回滚必须走统一 mutation 入口（`*_result`），保存变更前版本到 `memory_versions`，事件写入 `memory_audit`。
4. 破坏性操作必须返回 `version_id`、`audit_id`、`changed_rows`；`changed_rows == 0` 不能写成功状态。
5. 删除不能导致历史版本一起丢失；删除可通过版本回滚恢复。
6. 内容变更后必须刷新 FTS 索引（由 `db.py` 的 FTS 触发器保证）。
7. 更新采用**全文替换**语义（`upsert_memory` 及模型工具写入均整体替换正文，不做追加），避免正文无限膨胀。
8. 语义检索必须进入真实对话上下文链路（chat 每轮注入 + core 启动注入），不能只接在展示层。
9. 不确定是否属于稳定事实时，优先生成候选或保持观察，不要急着写入。
10. 用户明确要求删除、纠正或更新记忆时，以用户最近一次明确更正为准。

## 检索策略

1. 启动时仅加载 core（必须载入）完整内容。
2. 当前话题涉及个人事实、长期计划、健康、关系、偏好或历史决策时，优先确定性检索（`search_memories`）。
3. 检索为确定性通道：FTS 词面（含同义词扩展与 CJK 兜底）+ 会话状态回指联想（SessionState）；FTS 命中是独立强信号。
4. 记忆写入由模型在对话中自主调用记忆工具（memory_remember 等）完成；写入准则由工具描述与 system prompt 承载，不使用独立提取管线或触发词表。

## 开发工作流护栏

1. 不允许只更新文档状态来宣布完成；必须附带命令、测试或真实链路 smoke test。
2. 每个跨模块改动先做 tracer bullet：从真实入口到真实输出的最小闭环。
3. 删除模块前必须列替代矩阵：旧能力、新能力、调用方、行为差异、验证方式。
4. 数据破坏性操作必须经过统一 mutation 入口。
5. 引入依赖必须同步 `requirements.txt`，并说明 clean environment 验证方式。
6. 核心机制评估：`python tests/memory_system_eval.py`（不主动新增测试文件，除非明确要求）。
