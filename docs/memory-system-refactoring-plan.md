# IwIw 记忆系统重构计划

> 基于 2026-06-20 架构讨论的最终决策。
> 最后更新：2026-06-20，Phase 0～5 + Phase 7 已实现；审查修复正在收敛数据安全与真实链路验收。

---

## 架构决策总结

| 决策点 | 结论 |
|--------|------|
| 存储真源 | **SQLite 统一真源** —— 合并到 `selfecho_data/sessions.db` |
| 向量嵌入 | **BAAI/bge-small-zh-v1.5**（本地部署，中文优化，33MB，512维） |
| 检索 | **向量优先 + FTS5 回退**，相关性阈值过滤 |
| 触发机制 | **混合策略** —— 高信号消息实时提取 + 周期 LLM consolidate |
| 淘汰策略 | **配置化** —— `llm_judge` / `rules` / `hybrid` / `manual`，支持前端确认 |
| 通信 | **JSON-RPC 2.0 over WebSocket**（待实现控制通道，位于 API/transport 层）+ **REST**（CRUD）+ SSE（过渡期流式回复） |
| Markdown | 迁移后停掉同步写入，保留文件作为可读缓存 |
| 前端确认 | 淘汰/删除操作前可在前端展示确认信息，配置可开关 |

---

## 分阶段实施路线

### Phase 0：剪枝 ✅（已完成）

**目标**：清理项目中所有旧架构残留，为重构铺干净的地基。

| 序号 | 任务 | 状态 |
|------|------|------|
| 0.1 | docs/ 目录清理 | ✅ docs/ 从 12 文件精简到 4 个 |
| 0.2 | git rm 已删除文件 | ✅ hook_extractor, hook_heartbeat, treehole_reply |
| 0.3 | AGENTS.md 审查 | ✅ 保留，待合并到 CLAUDE.md |
| 0.4 | 旧迁移代码清理 | ✅ db.py 中旧重命名代码已移除 |
| 0.5 | memory/ priority 修正 | ✅ 4 条降级（core→normal ×1, important→normal ×3） |
| 0.6 | extractor prompt 改进 | ✅ 新增 mem_type 选择规则（feedback/project/reference） |

**产出**：干净的项目根目录，旧架构文档完全清除，提取 prompt 增加分类规则。**7 commits**。

---

### Phase 1：数据库层 ✅（已完成）

**目标**：建立新的 SQLite 统一存储层，迁移现有数据。

| 序号 | 任务 | 状态 |
|------|------|------|
| 1.1 | 编写 `memory_agent/db.py` | ✅ Schema（memories / memory_chunks / memory_pending_actions / memories_fts） |
| 1.2 | 数据迁移脚本 | ✅ 23 条 .md → SQLite |
| 1.3 | FTS5 关键词检索 | ✅ 中文/英文前缀查询 |
| 1.4 | 写入路径切换到 SQLite | ✅ extractor、mcp_server、GUI API 已切换；旧 store/search 能力由 `db.py`/`retrieval.py` 兼容替代 |

**产出**：23 条记忆全部在 SQLite，mem_type 按规则分散（user=13, feedback=3, project=3, reference=4）。**2 commits**。

---

### Phase 2：向量嵌入 ✅（已完成）

**目标**：对所有记忆生成嵌入向量，建立语义检索能力。

| 序号 | 任务 | 状态 |
|------|------|------|
| 2.1 | 编写 `memory_agent/embedding.py` | ✅ BAAI/bge-small-zh-v1.5（本地，中文优化，512维） |
| 2.2 | 文本分段 | ✅ `_chunk_text()` 按段落分割，≤500字/块 |
| 2.3 | 向量存储 + 余弦检索 | ✅ 在 `db.py` 中，含 slug 去重 |
| 2.4 | 为所有记忆生成嵌入 | ✅ 23 条记忆 → 42 chunk → 全部向量化 |

**产出**：每条记忆至少对应一个向量，语义检索可用。**1 commit**。

---

### Phase 3：检索层 ✅（已完成）

**目标**：用混合检索替代当前 BM25-only。

| 序号 | 任务 | 状态 |
|------|------|------|
| 3.1 | 编写 `memory_agent/query_builder.py` | ✅ 从对话上下文生成 2-5 条检索 query（启发式） |
| 3.2 | 编写 `memory_agent/retrieval.py` | ✅ 向量 + FTS5 + RRF + 时间衰减 + priority 加权 |
| 3.3 | L0/L1 始终加载逻辑 | ✅ 在 `prompt_service.preview_prompt()` 与真实 `ContextBuilder` 中实现 |
| 3.4 | L2/L3 按需检索注入 | ✅ `hybrid_search` → `format_memory_context` → 真实 agent system prompt |

**产出**：对话过程中自动检索相关记忆并注入上下文，标记来源。**1 commit**。

---

### Phase 4：触发 + 提取改善 ✅（已完成）

**目标**：建立混合触发机制，改善提取质量。

| 序号 | 任务 | 状态 |
|------|------|------|
| 4.1 | 编写 `memory_agent/triggers.py` | ✅ 高速信号实时触发（关键词/正则匹配），零 LLM 调用 |
| 4.2 | 改善 extractor prompt | ✅ 新增 archive/merge 动作；传入当前日期推 event_date |
| 4.3 | 接入对话流实时提取 | ✅ append_message 时检测高信号 → 后台线程执行 extract_and_save |
| 4.4 | archive/merge 支持 | ✅ save_memory_candidate 处理 archive 降级和 merge 合并 |

**产出**：高价值信息实时入库，周期 consolidate 批量整理。

---

### Phase 5：淘汰机制 ✅（已完成）

**目标**：可配置的信息淘汰体系。

| 序号 | 任务 | 状态 |
|------|------|------|
| 5.1 | 记忆维护 LLM 审查 | ✅ consolidate 每 3 次触发；当前仅生成保守 archive 候选，merge 不自动执行 |
| 5.2 | 新增 REST 端点 | ✅ pending-actions CRUD + approve/reject 已上线 |
| 5.3 | pending_actions 入库 | ✅ 候选写入 memory_pending_actions 表（status=pending） |
| 5.4 | 淘汰审计 | ✅ 批准/拒绝操作写入 `memory_audit`；修改前快照写入 `memory_versions` |

**产出**：记忆不会无限膨胀，过时信息有序降级/归档。

---

### Phase 6：WebSocket 统一通道 ⏳（设计完成，待实现）

**目标**：建立 JSON-RPC 2.0 over WebSocket 作为主要控制通道。

| 序号 | 任务 | 状态 |
|------|------|------|
| 6.1 | 后端 WSManager | 📝 设计完成（见 [phase6-websocket-design.md](phase6-websocket-design.md)） |
| 6.2 | 前端 WSManager | 📝 设计完成 |
| 6.3 | 逐步迁移 chat 流 | ⏳ 待实现（过渡期两者并行） |
| 6.4 | agent.runner 接入 WS | ⏳ 待实现 |

**产出**：一条 WS 连接走所有控制通信，REST 仅保留 CRUD。

---

### Phase 7：清理收尾 ✅（已完成）

| 序号 | 任务 | 状态 |
|------|------|------|
| 7.1 | 删除旧 store.py / search.py | ✅ 功能已完全被新模块替代，已 git rm |
| 7.2 | 删除 memory/.history | ✅ 历史版本迁移到 SQLite memory_versions 表 |
| 7.3 | 更新 CLAUDE.md | ✅ 同步 SQLite 真源、向量检索、混合触发等最新架构 |
| 7.4 | 更新设计文档 | ✅ 状态同步 |

**产出**：无旧架构残留，所有文档反映当前架构。**3 commits**。

---

## 文件变更清单

### 已新增文件
```
memory_agent/db.py              — SQLite schema + connection + CRUD + FTS5 + 向量
memory_agent/embedding.py       — BGE 本地向量嵌入（代替原 OpenAI 方案）
memory_agent/query_builder.py   — 对话上下文 → 检索 query
memory_agent/retrieval.py       — 混合检索引擎（向量 + FTS5 + RRF）
memory_agent/triggers.py        — 高信号实时触发检测
```

### 已修改文件
```
memory_agent/config.py           — 增加 DB 路径、向量嵌入、检索配置项
memory_agent/extractor.py        — mem_type 规则；archive/merge 动作；传入当前日期
memory_agent/mcp_server.py       — list_memories 从 db.py 导入
selfecho_session/service.py      — 实时触发接入 append_message；记忆维护接入 consolidate
selfecho_api/prompt_service.py   — 切换到 SQLite 读取；L2/L3 语义检索注入
selfecho_api/server.py           — pending-actions API 端点
selfecho_config/prompts/memory_consolidation.md  — 更新提取指令
.gitignore                       — 增加 .venv/
```

### 已删除的旧文件（Phase 0/7）
```
docs/ 8 个旧文件（见 Phase 0）
memory_agent/hook_extractor.py
memory_agent/hook_heartbeat.py
memory_agent/store.py          — 旧 Markdown 文件操作，功能已由 db.py 替代
memory_agent/search.py         — 旧 BM25 搜索，功能已由 retrieval.py 替代
memory/.history/               — 历史版本已迁移到 SQLite memory_versions 表
selfecho_config/prompts/treehole_reply.md
.venv/（从 git 跟踪移除，保留磁盘文件）
```

### 未实施的计划文件（Phase 6）
```
selfecho_api/ws.py              — WebSocket 连接管理与 JSON-RPC 传输层
selfecho_api/ws_handlers.py     — WS 方法路由，委托 memory/session/agent 模块
web/src/api.ts WSManager        — 前端 WS 管理器
web/src/App.vue pending 组件     — 前端确认区域
```

---

## 数据库 Schema 新增表

```sql
-- 记忆主表（取代 memory/*.md）
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    mem_type TEXT NOT NULL DEFAULT 'user',
    priority TEXT NOT NULL DEFAULT 'normal',
    event_date TEXT,
    recorded_date TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    access_count INTEGER DEFAULT 0,
    last_access_at TEXT,
    embedding_model TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);

-- 分块向量存储
CREATE TABLE memory_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    vector BLOB,
    model_version TEXT
);

-- FTS5 关键词检索
CREATE VIRTUAL TABLE memories_fts USING fts5(
    content, description,
    content='memories',
    content_rowid='rowid'
);

-- 触发器：保持 FTS 同步
CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, description)
    VALUES (new.rowid, new.content, new.description);
END;

-- 版本历史（替代旧的 memory/.history 文件）
CREATE TABLE memory_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id       TEXT,
    slug            TEXT NOT NULL,
    content         TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    mem_type        TEXT NOT NULL DEFAULT 'user',
    priority        TEXT NOT NULL DEFAULT 'normal',
    event_date      TEXT,
    saved_at        TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT ''
);

-- 待确认动作（淘汰/删除前需人工审批）
CREATE TABLE memory_pending_actions (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    session_id TEXT,
    cycle_no INTEGER,
    action TEXT NOT NULL,
    target_memory_id TEXT,
    source_memory_ids TEXT,
    reason TEXT,
    status TEXT DEFAULT 'pending',
    details TEXT DEFAULT '{}'
);
```

---

## 更新配置项

```python
# memory_agent/config.py 新增

# ── DB ──
MEMORY_DB_PATH = PROJECT_ROOT / "selfecho_data" / "sessions.db"  # 与会话层合并

# ── 向量嵌入 ──
EMBEDDING_PROVIDER = "local"  # local | openai
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"  # 本地中文优化，512维
EMBEDDING_DIMENSIONS = 512
EMBEDDING_BATCH_SIZE = 16
EMBEDDING_DEVICE = "cpu"  # cpu | cuda | mps

# ── 检索 ──
HYBRID_TOP_K = 10
HYBRID_SEMANTIC_WEIGHT = 0.5
HYBRID_BM25_WEIGHT = 0.2
HYBRID_TIME_WEIGHT = 0.15
HYBRID_PRIORITY_WEIGHT = 0.15
HYBRID_RELEVANCE_THRESHOLD = 0.4
MEMORY_RECALL_MAX_CHARS = 2500  # 注入上下文的上限

# ── 淘汰 ──
ELIMINATION_STRATEGY = "hybrid"  # llm_judge | rules | hybrid | manual
ELIMINATION_CONFIRM = True       # 是否在前端展示确认
ELIMINATION_HALF_LIFE_DAYS = 90  # 90 天无访问触发规则淘汰
ELIMINATION_MAX_MEMORIES = 500   # 超过此数量触发主动淘汰

# ── WS ──
WS_PORT = 8765
WS_HEARTBEAT_INTERVAL = 30
```

---

## 时间估算

| 阶段 | 预计工作量 | 状态 |
|------|-----------|------|
| Phase 0：剪枝 | ~2 小时 | ✅ 已完成 |
| Phase 1：数据库层 | ~4 小时 | ✅ 已完成 |
| Phase 2：向量嵌入 | ~3 小时 | ✅ 已完成 |
| Phase 3：检索层 | ~4 小时 | ✅ 已完成 |
| Phase 4：触发 + 提取 | ~3 小时 | ✅ 已完成 |
| Phase 5：淘汰机制 | ~4 小时 | ✅ 已完成 |
| Phase 6：WS 通道 | ~6 小时 | ⏳ 设计文档已完成，待实现 |
| Phase 7：清理收尾 | ~2 小时 | ✅ 已完成 |

**已实现**：~22 小时开发工作量（Phase 0～5 + Phase 7）。当前以审查修复项作为验收补强：版本快照、rollback、真实上下文注入、依赖声明和文档契约一致性。
