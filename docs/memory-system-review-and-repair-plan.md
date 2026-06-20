# 记忆系统近期更改审查与修复草案

> 日期：2026-06-20
> 范围：`origin/main..HEAD` 已提交更改 + 当前未提交工作区更改
> 重点：长期记忆存储、版本历史/审计、语义检索、淘汰确认、WS 统一通道设计

## 结论摘要

近期重构方向是对的：从 Markdown 真源转向 SQLite 真源、加入本地向量检索、保留会话原始消息、为 GUI 增加淘汰确认和未来 WS 推送，这些都符合 IwIw 作为本地个人智能体继续成熟化的方向。

审查时实现处在一个危险的中间态：项目契约、文档和代码对“记忆真源、备份方式、运行链路”的定义不一致。最需要先修的不是 WS，而是数据安全和实际对话链路：

1. `AGENTS.md` 当时仍声明 `memory/` 和 `.history/` 是长期记忆真源/备份要求，但新架构已经改为 SQLite 真源并删除 `.history`。
2. 多个记忆修改路径当时没有保存版本快照，archive/delete/approve pending 可能破坏可回滚性。
3. `merge` pending action 当时会被标记为 executed，但没有实际执行合并。
4. 语义检索当时只进入了 prompt preview，没有进入真实 agent 回复上下文。
5. 新写入或编辑的记忆当时不会自动刷新向量，语义检索会漏掉新记忆或命中过期 chunk。
6. `migrate_legacy` 当时因缩进错误不再是 `SessionMemoryService` 方法，API 会运行时报错。
7. WS 设计方向可取，但模块位置和安全策略需要调整；它不应该放在 `memory_agent`，也不能因为本地服务就完全无认证。

## 本轮落地状态

已完成第一批修复，重点收敛数据安全和真实链路：

- `AGENTS.md` / 项目 `CLAUDE.md` 已统一为 SQLite 真源，`memory/` 降级为遗留/导出缓存。
- `memory_versions` 已改为不随 `memories` 删除级联清理，并增加旧 schema 迁移。
- GUI 编辑、归档、删除、合并、pending approve、rollback 等路径已保存修改前快照并写审计。
- 删除后的记忆可从 `version://<id>` 结构化历史恢复，不再把带 frontmatter 的展示文本写回正文。
- pending `merge` 不再被标记为 executed；自动维护当前只生成保守 archive 候选。
- L2/L3 语义检索已进入 `AgentOrchestrator` → `ContextBuilder` 的真实回复链路。
- 新写入/编辑/合并/恢复记忆后会刷新向量；嵌入不可用时清理 stale chunk，回退 FTS。
- `migrate_legacy` 已恢复为 `SessionMemoryService` 方法。
- Phase 6 WS 文档已改为 `selfecho_api`/transport 层，并增加 Origin + 本地 token 约束。

## 审查范围

已审查的主要文件：

- `AGENTS.md`
- `CLAUDE.md`
- `docs/memory-system-refactoring-plan.md`
- `docs/memory-vector-embedding-design.md`
- `docs/phase6-websocket-design.md`
- `memory_agent/db.py`
- `memory_agent/embedding.py`
- `memory_agent/extractor.py`
- `memory_agent/mcp_server.py`
- `memory_agent/retrieval.py`
- `selfecho_agent/context.py`
- `selfecho_agent/core/orchestrator.py`
- `selfecho_api/prompt_service.py`
- `selfecho_api/server.py`
- `selfecho_session/service.py`
- `requirements.txt`

验证结果（2026-06-20 本轮修复后）：

- `npm run build`：通过。
- `python -m compileall -q memory_agent selfecho_agent selfecho_api selfecho_session tests`：通过。
- `python tests/harness_eval.py`：通过，8 项 harness 检查均 passed。
- 临时 SQLite rollback smoke：通过，删除主记忆后可从 `memory_versions` 恢复。
- `python -c "from selfecho_api.server import app; print(app.title)"`：通过。
- `git diff --check`：通过，仅有工作区 LF/CRLF 提示。
- `python -m pytest -q`：未运行，当前环境没有安装 `pytest`。

## Claude Code + DeepSeek 工作流复盘

这些失误不应简单归因于 DeepSeek 或 Claude Code。更准确的判断是：当前工作流让模型长时间处在“连续生成 + 文档自证 + 大批量提交”的状态，但缺少强制性的工程验收门。模型可以很快推进多个 phase，却没有被迫证明每个 phase 的关键路径真的工作。

审查时，`CLAUDE.md` 已经记录了新的架构方向：SQLite 是长期记忆真源，GUI 会话原始消息必须保留，写入应可审计、可回滚。但它只说明了“当前架构和原则”，没有规定“每次修改后必须验证哪些不变量”。同时 `AGENTS.md` 还保留旧 Markdown 真源规则，导致不同 agent 或不同入口可能遵循不同契约。

暴露出的工作流问题主要有七类。

### 1. 指令源分裂

同一个项目里同时存在：

- `CLAUDE.md`：声明 SQLite 是当前真源。
- `AGENTS.md`：审查时仍声明 `memory/` 是 Markdown 真源，`.history/` 是备份要求。
- `docs/memory-system-refactoring-plan.md`：声明 Phase 0～5 + Phase 7 已完成。

这会让 Claude Code、Codex、人工维护者读取到不同版本的系统事实。对 agent 来说，这不是“文档小瑕疵”，而是运行契约冲突。

修正方式：

- 只保留一个权威项目契约入口，建议以 `AGENTS.md` 为跨工具入口，`CLAUDE.md` 只做 Claude Code 兼容摘要并引用 `AGENTS.md`。
- 每次修改架构真源、权限、备份策略时，同步更新两个文件，或删除重复内容。
- 增加一个轻量检查脚本，扫描 `AGENTS.md` / `CLAUDE.md` 是否同时出现互斥说法，例如 `memory/ 是真源` 与 `SQLite 是真源`。

### 2. “Phase 完成”被文档状态替代

重构文档里大量任务标记为已完成，但一些完成项只满足“代码存在”：

- 语义检索模块存在，但真实 agent context 没接入。
- pending actions 表支持 merge，但 approve 不执行 merge。
- `memory_versions` 表存在，但 delete 会级联清掉版本，且多个修改路径不写版本。

这说明工作流把 checklist 当成验收，而不是把可运行行为当成验收。

修正方式：

- 每个 phase 必须有 2-5 条可执行验收命令或 API 测试。
- 文档中的“已完成”只能在验收通过后标记。
- phase 文档里增加“未验证/仅实现骨架/已接入真实链路”三种状态，避免把骨架当完成。

### 3. 没有 tracer bullet，改动面过大

一次性推进 SQLite、向量、检索、触发、淘汰、GUI、WS 设计，导致变更横跨：

- 存储 schema。
- 写入路径。
- 检索路径。
- agent 上下文组装。
- GUI API。
- 维护/淘汰机制。
- 文档和工具入口。

这种工作流很适合模型“写很多代码”，但不适合保持系统不变量。每个模块看起来都合理，跨模块串起来就断。

修正方式：

- 每个大 phase 先做一条 tracer bullet：从 GUI/API 入口一路打到 DB，再从 DB 读回到真实 agent context。
- 例如语义检索 phase 的最小 tracer bullet 应是：“保存一条 normal 记忆 -> 生成向量 -> 用户消息触发真实回复上下文召回该记忆”。
- tracer bullet 通过后，再扩展功能面。

### 4. 缺少 clean environment 验证

当前本机环境有 `numpy` 和 `sentence_transformers`，但 `requirements.txt` 没有声明。模型在本机能跑通 import，不代表新环境可安装运行。

修正方式：

- 每次引入依赖必须更新依赖文件。
- 增加 clean install 检查，至少在虚拟环境或 CI 中执行：
  - `pip install -r requirements.txt`
  - `python -m compileall ...`
  - 最小 smoke tests
- 健康检查 `/api/health` 应报告 embedding backend 是否可用，而不是隐式失败。

### 5. 删除旧模块早于替代链路验收

`store.py` / `search.py` 被删除，兼容函数补到 `db.py`，但替代链路没有完整验收，导致回滚、历史、搜索和上下文注入出现断点。

修正方式：

- 删除旧模块前必须满足“替代矩阵”：
  - 旧函数名。
  - 新函数名。
  - 调用方。
  - 行为差异。
  - 测试覆盖。
- 先保留旧模块作为薄 adapter，等调用方全部切换并测试通过后再删除。

### 6. 缺少数据安全门

记忆系统不是普通 CRUD。它处理长期个人事实，删除、归档、合并、回滚都必须有更高的工程门槛。

这次的问题集中在：

- 修改前快照不统一。
- delete 与版本表 FK cascade 冲突。
- pending approve 可以把未实现动作标记为 executed。
- rollback 写回展示格式而不是结构化内容。

修正方式：

- 任何 destructive memory mutation 都只能通过一个受控函数执行。
- 这个函数必须先保存版本，再写审计，再执行 mutation。
- mutation 返回值必须包含 `version_id`、`audit_id`、`changed_rows`。
- 如果 `changed_rows == 0`，不能写成功状态。

### 7. 模型适配策略不足

DeepSeek 接入 Claude Code 可以提供很强的代码生成能力，但在这类项目里，弱点会集中暴露在：

- 长上下文下对旧契约的遗忘或混用。
- 对“看起来完成”的代码缺少怀疑。
- 对跨模块运行路径验证不足。
- 对数据迁移、删除、回滚这类负面路径不敏感。

这不是说不能用 DeepSeek，而是使用方式要改变：不要让模型连续推进多个 phase 后再统一 review；应让模型在每个小步后运行固定验收，并用失败结果约束下一步。

建议采用的 Claude Code 工作节奏：

1. 先让模型写“本次修改要保持的不变量”。
2. 再让模型只改一个 vertical slice。
3. 修改后强制运行对应 smoke test。
4. smoke test 不存在时，先补最小测试。
5. 最后才更新文档状态。
6. 每完成 3-5 个小提交，切到 review 模式，让另一个 agent 或同一 agent 只审 diff，不继续写功能。

## 建议补充到 `CLAUDE.md` 的工作流护栏

`CLAUDE.md` 当前可以作为架构摘要，但建议补一个“开发工作流”段落，明确 Claude Code 每次开发必须遵守：

```markdown
## 开发工作流护栏

1. 修改长期记忆系统前，先确认真源、备份、审计、回滚四个不变量。
2. 不允许只更新文档状态来宣布 phase 完成；必须附带可运行验收。
3. 删除旧模块前，必须列出替代矩阵和调用方迁移结果。
4. 数据破坏性操作必须经过统一 mutation 入口，写入 version 和 audit。
5. 引入依赖必须更新 requirements，并在 clean environment 下验证。
6. agent 上下文、GUI API、DB 写入属于跨模块链路，必须至少有一个端到端 smoke test。
7. WS、工具执行、记忆删除等能力默认按高风险处理，不能因为本地运行就跳过权限与来源校验。
```

短期不建议马上把这段写入 `CLAUDE.md`，因为 `AGENTS.md` 和 `CLAUDE.md` 现在还存在契约冲突。应先统一权威入口，再把护栏写入最终入口文件。

## 关键问题

### P1：项目契约与实现冲突

`AGENTS.md` 仍规定：

- `memory/` 是 Markdown 长期事实记忆真源。
- `memory/.history/` 是历史备份。
- 长期记忆写入、编辑、合并、归档、删除必须保留 `.history/` 备份和审计记录。

但新文档和实现已经改成：

- SQLite `selfecho_data/sessions.db` 是真源。
- `memory/` 是只读缓存或历史遗留。
- `.history/` 已删除。
- 历史版本进入 `memory_versions` 表。

这会导致后续维护者不知道应该相信哪个规则，也会让 agent 在启动和写入记忆时走错路径。

修复方向：

- 更新 `AGENTS.md`，正式声明 SQLite 是长期记忆真源。
- 明确定义 `memory/` 的角色：只读导出缓存、人工检查缓存，或完全废弃。
- 把“必须保留 `.history/` 备份”改为“必须在 `memory_versions` 保存修改前快照，并在 `memory_audit` 写审计事件”。
- 如果仍希望保留可读 Markdown，需要实现从 SQLite 导出 `memory/*.md` 和 `MEMORY.md` 的缓存重建，而不是把 Markdown 当写入入口。

### P1：破坏性记忆操作没有统一版本保护

当前只有部分路径调用 `_save_version()`：

- `replace_memory()`
- `delete_memory_compat()`
- `merge_memories()` 的 target

但这些路径缺快照或不完整：

- `archive_memory_by_slug()` 直接改 priority。
- `approve_pending_action()` 对 archive/delete 直接执行。
- `save_memory_candidate()` 的 archive/merge/update 也没有统一快照。
- `merge_memories()` 归档 source 前没有保存 source 快照。

修复方向：

- 在 `memory_agent/db.py` 内建立唯一写入接口，例如：
  - `mutate_memory(slug, action, reason, fn)`
  - 或更简单的 `_save_version_before(memory_id, reason)`，所有 mutation 必须调用。
- archive/delete/merge/update/pending approve 都通过同一套函数执行。
- `memory_audit.backup_path` 始终指向 `version://<id>`。
- 对 delete 需要先保存版本，再删除主表；删除后的版本要能按 slug 找回，不能因 FK cascade 一起消失。

注意：当前 `memory_versions.memory_id REFERENCES memories(id) ON DELETE CASCADE` 会让删除主记忆时历史版本一起消失。这和“删除可回滚”冲突。需要改成不级联，或另建 tombstone/backup 表。

### P1：pending merge 是空操作

维护逻辑会创建 `merge` pending action，schema 也允许 `merge`，但 `approve_pending_action()` 只处理 `archive` 和 `delete`，最后仍把 pending action 标记为 `executed`。

这会让 GUI 显示“已执行”，但实际没有合并任何记忆。

修复方向二选一：

1. 暂时禁止自动创建 `merge` pending，只允许 `archive/delete/downgrade` 进入 pending；merge 由 GUI 手动完成。
2. 完整实现 merge pending：
   - `memory_pending_actions.details` 保存 target slug、source slug 列表、合并后正文、description、priority、mem_type。
   - approve 时调用 `merge_memories()`。
   - 如果缺少合并正文，不允许 approve，返回错误，不能标记 executed。

短期建议选 1，避免 LLM 自动合并私人记忆造成不可逆污染。

### P1：语义检索没有进入真实 agent 上下文

`selfecho_api/prompt_service.py` 里的 `preview_prompt()` 会调用 `hybrid_search()`，但真实回复链路是：

`AgentOrchestrator.run_stream()` -> `ContextBuilder.build()`

当前 `ContextBuilder.build()` 只加载 L0/L1，没有按用户消息检索 L2/L3。

修复方向：

- 调整 `ContextBuilder.build()` 接口，增加 `user_message` 参数。
- 在 `AgentOrchestrator.run_stream()` 调用时传入 `request.message`。
- 在 `ContextBuilder` 中：
  - 先加载 L0/L1，并记录 `exclude_slugs`。
  - 对 `request.message` + 最近上下文调用 `hybrid_search()`。
  - 将 `format_memory_context()` 结果作为单独 section 注入。
- `prompt_service.preview_prompt()` 应复用同一个 ContextBuilder 或同一个 memory context helper，避免预览链路和真实链路分叉。

### P1：记忆写入后向量不会刷新

审查时，向量表 `memory_chunks` 只有 `ensure_all_vectors()` 这个手动全量入口。新记忆写入、编辑、合并后不会自动更新向量。

后果：

- 新保存的记忆无法被语义检索命中。
- 编辑后的记忆可能继续使用旧 chunk。
- 删除或归档后的记忆可能在向量结果里表现异常，取决于 join 和清理路径。

修复方向：

- 在所有内容变更后标记向量 dirty：
  - 最小方案：删除该 memory 的 chunks，并让 FTS5 暂时兜底。
  - 更好方案：增加 `embedding_status` / `embedding_updated_at`，后台队列刷新。
- 抽出 `refresh_memory_vectors(memory_id)`：
  - 分块。
  - 调用 `embed_batch()`。
  - 原子替换 `memory_chunks`。
  - 更新 `embedding_model`。
- 对 GUI 手动编辑、自动提取、merge、rollback 都调用同一刷新逻辑。
- 如果模型加载失败，不应阻塞写入，但必须留下 dirty 状态，便于后续补建。

### P1：旧历史导入 API 已损坏

审查时，`migrate_legacy()` 被缩进到了 `_trigger_extract()` 函数内部，不再是 `SessionMemoryService` 的方法。API 仍调用 `session_service.migrate_legacy()`。

修复方向：

- 把 `migrate_legacy()` 缩进恢复到 `SessionMemoryService` 类中。
- 增加一个最小测试：
  - `assert hasattr(SessionMemoryService, "migrate_legacy")`
  - 调用不存在 legacy DB 时返回 `{ok: False, ...}`，不抛 AttributeError。

### P2：recorded_date 和 event_date 混淆

创建记忆时：

```python
recorded = event_date or now
```

随后 `event_date` 和 `recorded_date` 都写入 `recorded`。这会让历史事件的“发生日期”和“记录日期”混为一谈。

修复方向：

- `event_date` 写候选提供的事件日期。
- `recorded_date` 永远写当前时间。
- 迁移脚本或后续修正需要识别已污染记录。

### P2：SQLite 版本回滚会污染正文

`read_history()` 返回带 frontmatter 的 Markdown 字符串。`rollback_audit()` 对 `version://` 分支直接把这个字符串作为 `body` 传给 `replace_memory()`。

修复方向：

- `read_history()` 增加结构化返回函数，例如 `read_history_record(slug, version)`。
- rollback 使用历史记录的 `content` 字段，而不是 Markdown 展示文本。
- 保留现有 `read_history()` 作为 API 展示用也可以，但不要用于写回。

### P2：依赖声明不完整

`requirements.txt` 没有 `numpy` 和 `sentence-transformers`，但向量模块直接依赖。

修复方向：

- 如果向量检索是默认能力，加入：
  - `numpy`
  - `sentence-transformers`
- 如果向量检索是可选能力，则拆成 extras 或在启动健康检查里明确报告“语义检索不可用”，并确保 FTS fallback 不导入 `numpy` 崩溃。

## WS 设计评价

### 方向正确

前端确实需要服务器推送：

- agent 运行进度。
- 记忆整理完成。
- pending action 新增。
- 后台任务状态。

JSON-RPC 2.0 over WebSocket 可以同时支持请求-响应和通知，适合 IwIw 的 GUI 控制通道。

### 模块位置需要调整

审查时的设计把后端 `WSManager` 放在 `memory_agent/ws_manager.py`。这会让记忆模块承载 chat、agent、memory、system 的统一控制通道。

按模块深度来看，WS 是传输层 seam，不是记忆模块的内部实现。更合适的位置：

- `selfecho_api/ws.py`
- 或 `selfecho_transport/jsonrpc_ws.py`

建议模块拆分：

- `JsonRpcRouter`：注册 method -> handler。
- `ConnectionManager`：管理连接、session 订阅、通知发送。
- `memory_rpc.py`：把 `memory.*` 方法适配到 `memory_agent.db`。
- `agent_rpc.py`：把 `agent.*` 方法适配到 `AgentRunner`。
- `chat_rpc.py`：处理 chat stream/cancel。

这样 `memory_agent` 只提供记忆能力，不知道 WebSocket 存在；WS 层只负责协议和连接，不写业务逻辑。

### 安全策略必须前置

“本地服务无需认证”不成立。浏览器中的任意网页都可能尝试连接 `ws://127.0.0.1:8765/ws`。

最低要求：

- 后端校验 `Origin`，只允许：
  - `http://127.0.0.1:5173`
  - `http://localhost:5173`
  - 已打包前端所在 origin
- 启动时生成随机 token，前端从同源页面或启动注入配置获取。
- WS upgrade 时要求 token。
- 对写入/删除/归档/执行工具等高风险方法继续走权限确认，不因 WS 存在而绕过 policy。

### 迁移路径建议

不要一开始把 CRUD、chat stream、agent run 全部迁移到 WS。先让 WS 成为事件通道，降低风险。

建议顺序：

1. Step 1：WS 只做通知。
   - `pending.notify`
   - `agent.run.progress`
   - `system.consolidate.done`
2. Step 2：增加只读 RPC。
   - `memory.list`
   - `memory.get`
   - `memory.search`
   - `system.stats`
3. Step 3：增加低风险控制。
   - `agent.run.status`
   - `chat.stream.cancel`
4. Step 4：迁移写操作。
   - `pending.approve/reject`
   - `memory.archive/delete/edit`
   - 这些必须复用 REST 当前的 service 函数，不要复制业务逻辑。
5. Step 5：评估是否迁移 chat stream。
   - SSE 对文本流是足够好的，不一定必须删除。
   - 如果迁移，WS 应支持 stream id、cancel、done/error 事件和 backpressure。

## 修复路线

### Phase A：先统一契约

目标：让文档、项目指令和实现对真源/备份/审计达成一致。

任务：

1. 更新 `AGENTS.md`：SQLite 是长期记忆真源。
2. 更新 `docs/memory-system-refactoring-plan.md`：删除重复 Phase 6 标题，明确 `memory/` 是缓存还是废弃。
3. 更新 `CLAUDE.md`：补充 `memory_versions` 替代 `.history` 的前提和限制。
4. 决定是否保留 Markdown 导出：
   - 保留：实现 `export_markdown_cache()`。
   - 不保留：删除“Markdown 只读缓存”的说法，避免假缓存。

验收：

- 新读者只看 `AGENTS.md` 也不会再误以为 `memory/` 是写入真源。

### Phase B：恢复数据安全

目标：所有修改长期记忆的路径都可审计、可回滚。

任务：

1. 调整 `memory_versions`，避免删除主记忆时级联删除历史。
2. `_save_version()` 返回 `version://<id>`。
3. archive/delete/merge/update/pending approve 全部先保存 version。
4. 修复 rollback 使用结构化历史内容。
5. 删除路径增加 tombstone 或按 slug 可恢复。

验收：

- 手动编辑后可回滚。
- 归档后可回滚。
- 删除后能从版本恢复。
- merge 后 target 和 source 都有修改前快照。

### Phase C：修运行时硬错误

目标：修掉会直接导致 API 错误或错误状态的缺陷。

任务：

1. 修复 `SessionMemoryService.migrate_legacy()` 缩进。
2. pending `merge` 暂停自动执行，或实现完整 merge approve。
3. 修复 `recorded_date` 写入逻辑。
4. 为这些路径补最小测试。

验收：

- `/api/session-memory/migrate-legacy` 不再 AttributeError。
- approve unsupported merge 不会标记 executed。
- 新建历史事件时 `event_date != recorded_date` 可成立。

### Phase D：打通真实检索链路

目标：让 L2/L3 语义检索真正进入 agent 回复，而不是只在 preview 中出现。

任务：

1. `ContextBuilder.build()` 接收 `user_message`。
2. `AgentOrchestrator.run_stream()` 传入 `request.message`。
3. 抽出 memory context helper，preview 和真实链路共用。
4. 内容变更后刷新或标记 dirty vectors。
5. 依赖补齐或降级策略补齐。

验收：

- 新写入一条 normal 记忆后，相关用户消息能在真实 agent context 中召回。
- embedding 不可用时，FTS fallback 不崩溃，并有明确健康状态。

### Phase E：修订 WS 设计

目标：在实现前把 transport seam 和安全策略定稳。

任务：

1. 把设计文档中的 `memory_agent/ws_manager.py` 改为 `selfecho_api/ws.py` 或独立 transport 模块。
2. 增加 Origin allowlist + token 方案。
3. 明确 REST 与 WS 的分工：
   - REST：资源 CRUD 可以继续保留。
   - WS：运行控制和事件推送优先。
4. 增加 method 权限分级：
   - read-only
   - local state mutation
   - destructive memory mutation
   - tool execution
5. 先实现事件推送，不急着迁移 chat stream。

验收：

- WS 设计不让 `memory_agent` 依赖 `FastAPI WebSocket`。
- 任意外部网页无法无 token 连接并执行本地操作。

## 建议提交拆分

1. `docs: align memory source-of-truth contract`
2. `fix(session): restore legacy migration method`
3. `fix(memory): preserve versions for all mutations`
4. `fix(memory): make pending merge safe`
5. `fix(memory): correct event and recorded dates`
6. `fix(memory): refresh vectors after writes`
7. `feat(agent): inject semantic memory in real context`
8. `docs(ws): revise websocket transport seam and security`

## 最小测试清单

后端：

- `SessionMemoryService` 有 `migrate_legacy` 方法。
- `replace_memory()` 写入 version，并 audit 指向 `version://...`。
- `archive_memory_by_slug()` 写入 version。
- `delete_memory_compat()` 删除后版本仍可读。
- `rollback_audit()` 不把 frontmatter 写进正文。
- pending merge 在缺少合并正文时不能 executed。
- 新建记忆的 `recorded_date` 是当前时间，`event_date` 保留事件时间。
- 写入/编辑后 memory chunks 被刷新或标记 dirty。

前端/API：

- `npm run build`。
- `/api/memories/search?q=...` 在 embedding 可用和不可用时都能返回合理结果。
- pending action approve/reject 后 GUI 刷新状态。

WS 后续实现：

- 非 allowlist Origin 被拒绝。
- 无 token 连接被拒绝。
- `pending.notify` 能按 session 推送。
- agent progress 不需要前端轮询。
