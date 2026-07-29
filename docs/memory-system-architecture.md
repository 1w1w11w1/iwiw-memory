# 记忆系统架构

IwIw 的记忆系统目标很简单：让 AI 像“手边有一个搜索引擎的人”。

原始材料不交给 LLM 判断价值，先确定性写入全量记忆库；每轮对话按相关度、作用域和时间三层权重检索，只有过门槛的片段进入上下文。LLM 的职责是另一条线：从高信号材料中总结稳定的对话倾向和行为策略倾向，让 AI 更像熟悉用户的协作者。

## 唯一入口

外部调用方只依赖 `memory_agent.engine.MemoryEngine`：

```python
MemoryEngine.capture_material(...)      # 全量存储原始材料
MemoryEngine.build_context(...)         # 倾向 profile + 搜索召回片段
MemoryEngine.maintain_tendencies(...)   # observe / compile 周期维护
MemoryEngine.compile_tendency(...)      # 手动 compile / rebuild
MemoryEngine.search(...)                # agent / MCP 带 scope 的召回入口
MemoryEngine.search_records(...)        # 受信 GUI 的全量管理搜索
MemoryEngine.list_records/get_record    # 带 scope 的读取入口
MemoryEngine.update/delete/rollback     # 动作绑定权限、版本和审计一体的变更入口
```

`selfecho_session`、`selfecho_agent`、API 和 MCP 都只能通过这个 interface 使用记忆能力。项目初期不保留旧兼容壳；替换旧路径时迁移真实调用方并删除旧模块。

## 数据层

真源数据库是 `selfecho_data/sessions.db`。

全量记忆库：

```text
memory_records
- id
- source_type: message | tool | manual | file | system_event
- scope_type: global | project | session
- project_id
- session_id
- turn_idx
- role
- content
- content_hash
- privacy: public | internal | sensitive
- tags
- status: active | deleted
- vector_status: dirty | ready
- created_at / updated_at
- metadata
```

```text
memory_chunks
- record_id
- chunk_index
- chunk_text
- vector
- model_version
```

倾向上下文：

```text
tendency_observations
- scope_kind: agent_global | workspace | session
- scope_key
- workspace_id
- project_id
- content
- source_session_id
- source_turn_range
- suggested_scope_kind: agent_global | workspace | session
- status: active | merged | deleted
```

```text
tendency_profiles
- scope_kind: agent_global | workspace | session
- scope_key
- content
- source_observation_ids
- version
```

修改记录：

```text
memory_versions
memory_audit
tendency_profile_versions
memory_ingest_outbox
```

## 写入

`capture_material` 是确定性写入路径：

1. GUI 消息与 `memory_ingest_outbox` 同事务写入；`capture_material` 成功后删除 outbox，失败则保留并可重试。
2. 不调用 LLM 判断是否值得保存。
3. 用 `session_id + turn_idx + role + content_hash` 保证同一消息幂等。
4. 非私密项目或会话消息默认 `privacy=internal`；普通聊天使用 `session` scope，不会升级为 global。
5. FTS 随事务立即更新；内容变化把向量标记为 `dirty`。默认 light 检索不做同步向量维护，明确历史回顾的 deep 检索才按小批次刷新；失败不会阻塞消息写入或丢失待处理状态。
6. 编辑、删除和回滚保存完整 record snapshot；版本、业务变更和审计任一步失败都会回滚。

## 检索

每轮默认构造轻量 query，但不是每轮默认注入。召回排序只在可见候选集中进行：

- `sensitive` 永不进入召回。
- `project` 记录只在同 `project_id` 下可见。
- `session` 记录只在同 `session_id` 下可见。
- `global/public` 可跨上下文召回。

三层加权：

```text
final_score =
  0.62 * match_score   # 向量或关键词命中
+ 0.23 * scope_score   # session > project > global
+ 0.15 * time_score    # 近期材料略优先
```

注入门槛：

```text
score < 0.45          不注入
0.45 <= score < 0.68  light，最多 3 条
score >= 0.68         focused，最多 5 条
```

出现“之前 / 上次 / 记得 / 这个项目 / 我的偏好 / 删除记忆”等强触发词时，门槛降到 `0.35`。
强触发只降低总分门槛，`match_score` 仍必须至少为 `0.20`，scope 和时间不能单独让记录进入上下文。

## 倾向

倾向 profile 不保存事实，只保存行为 prior。

继承链：

```text
agent_global -> workspace -> session overlay
```

维护闭环：

1. 用户明确纠正、表达长期偏好、提出项目原则，或周期整理触发 `maintain_tendencies`。
2. LLM 只提取 `tendency_observations`，不改写全量事实库；所有新观察先落入 session overlay，候选目标记录在 `suggested_scope_kind`。
3. active observations 达到阈值、未整理消息达到 turn/token 预算、会话归档关闭或用户手动整理时，只自动编译当前 session profile；固定周期只是兜底。
4. 每个 scope 只能有一个 profile；profile 更新、版本、审计和 observation merge 在同一事务中完成。
5. workspace / agent_global 只有在受信 GUI 明确确认并具备 `full_access` 权限后，才从当前 session overlay 晋升；用户要求重审、profile 冲突或大规模删除后走 rebuild。

## 上下文顺序

`MemoryEngine.build_context` 输出两段：

1. `长期倾向`：每轮稳定注入，影响回应方式和风险判断。
2. `相关记忆`：只有检索过门槛才注入，作为可引用的历史片段。

`selfecho_agent.ContextBuilder` 只编排 context source，不直接读记忆表、不扫描工作目录、不实现检索策略。
SQLite 不可读时 `build_context` 返回 `memory_available=false`，agent 继续运行且不注入伪造记忆。

## 权限

- 记忆管理 API 只接受 IwIw 本机页面和开发端口的浏览器 Origin / Referer；缺失可信来源时拒绝。
- 编辑、删除、回滚、workspace/global 晋升和 rebuild 必须携带与具体 action/target 绑定的 `MemoryMutationContext`；其中可信来源、显式确认和 GUI 当前 `full_access` permission profile 必须同时成立。
- API 只把经来源校验的 GUI permission profile 转成 mutation context，不替调用方写死 `full_access`，最终授权由 `MemoryEngine` 判断。
- MCP 当前只暴露 scoped list/read/search/stats。scope 由宿主进程通过 `IWIW_MCP_SESSION_ID`、`IWIW_MCP_PROJECT_ID`、`IWIW_MCP_WORKSPACE_ROOT` 绑定，不接受模型在 tool call 中自报。
- MCP 的 list/read 默认经过 scope、privacy 与 session tombstone 过滤，不能按记录 ID 绕过限制；记忆写入和倾向维护在接入统一 `ToolRegistry`、permission profile、确认 gate 与 trace 前不暴露。

## 替代矩阵

| 删除模块 | 替代能力 | 已迁移调用方 | 行为差异 | 验证方式 |
|---|---|---|---|---|
| `memory_agent/service.py` | `MemoryEngine.build_context/search/list_records` | `ContextBuilder`、prompt preview | 一个入口同时处理倾向、scope、召回门槛和降级 | `python tests/memory_system_eval.py` |
| `memory_agent/retrieval.py` | `MemoryEngine.search` + `db.search_fts/search_vectors` | agent context、MCP | 不再按 L0-L3 预判价值；按 match/scope/time 和预算召回 | retrieval eval |
| `memory_agent/triggers.py` | `MemoryEngine.maintain_tendencies` | `SessionMemoryService` | trigger 只决定是否观察；原始材料始终确定性入库 | tendency eval |
| `memory_agent/llm.py` | `selfecho_model.ModelGateway` + `ProviderAdapter` | agent orchestrator、extractor、compiler | provider、重试和流式不再依赖记忆模块 | compileall + gateway eval |
| `selfecho_agent/model.py` | `selfecho_model.ModelGateway` | `AgentOrchestrator` | agent 与记忆复用同一独立模型运行时 | harness eval |

## 落地闭环

当前必须成立的 tracer bullet：

1. GUI 用户消息写入 `messages`，同时 `capture_material` 全量入库。
2. agent 回复前调用 `build_context`，拿到倾向 profile 和相关记忆片段。
3. 周期整理调用 `maintain_tendencies`，从会话片段提取 observation 并编译 profile。
4. GUI 的记忆页搜索调用 `search_records`，展示全量记忆库结果；agent / MCP 继续调用 scoped `search`。
5. 编辑、删除、回滚返回 `changed_rows`、`audit_id`、`version_id`，并刷新索引。
6. 删除 GUI 会话在同一 SQLite 事务中保存 `session_version`、写入 session tombstone、更新会话状态并记录审计。原始消息与 `memory_records` 保持不变，但所有正常 list/read/search/vector/tendency 路径统一隐藏；恢复在同一事务中移除 tombstone，并返回恢复前状态的新版本。
