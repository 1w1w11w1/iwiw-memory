# IwIw 记忆系统重构计划

> 基于 2026-06-20 架构讨论的最终决策。

---

## 架构决策总结

| 决策点 | 结论 |
|--------|------|
| 存储真源 | **SQLite 统一真源** —— 合并到 `selfecho_data/sessions.db` |
| 向量嵌入 | **OpenAI text-embedding-3-small**，dim=512，BLOB 存同一 DB |
| 检索 | **向量优先 + FTS5 回退**，相关性阈值过滤 |
| 触发机制 | **混合策略** —— 高信号消息实时提取 + 周期 LLM consolidate |
| 淘汰策略 | **配置化** —— `llm_judge` / `rules` / `hybrid` / `manual`，支持前端确认 |
| 通信 | **JSON-RPC 2.0 over WebSocket**（控制通道）+ **REST**（CRUD）+ SSE（可选高吞吐流） |
| Markdown | 迁移后停掉同步写入，保留文件作为可读缓存 |
| 前端确认 | 淘汰/删除操作前可在前端展示确认信息，配置可开关 |

---

## 分阶段实施路线

### Phase 0：剪枝（当前阶段）

**目标**：清理项目中所有旧架构残留，为重构铺干净的地基。

| 序号 | 任务 | 说明 |
|------|------|------|
| 0.1 | docs/ 目录清理 | 删除无用旧文档，保留当前和未来相关的 |
| 0.2 | git rm 已删除文件 | 从 git 中移除 `hook_extractor.py`、`hook_heartbeat.py`、`treehole_reply.md` |
| 0.3 | AGENTS.md 审查 | 决定保留还是删除 |
| 0.4 | 旧迁移代码清理 | `selfecho_session/db.py` 中旧重命名代码、LEGACY_DB_PATH |
| 0.5 | memory/ priority 修正 | 不合理的 L0/L1 降级 |
| 0.6 | SQLite schema 初始化 | 新增 memories/memory_chunks/memory_pending_actions 表 |

**产出**：干净的项目根目录，旧架构文档完全清除。

---

### Phase 1：数据库层

**目标**：建立新的 SQLite 统一存储层，迁移现有数据。

| 序号 | 任务 | 产出 |
|------|------|------|
| 1.1 | 编写 `memory_agent/db.py` | SQLite schema （memories / memory_chunks / memory_pending_actions 表） |
| 1.2 | 数据迁移脚本 | 将 `memory/*.md` 全量解析并 INSERT 到新表 |
| 1.3 | 添加 FTS5 索引 | 关键词搜索回退通道 |
| 1.4 | 停掉 Markdown 同步写入 | 旧 store.py 只读，不再写新 .md 文件 |

**产出**：数据全部在 SQLite，向量嵌入前的就绪状态。

---

### Phase 2：向量嵌入

**目标**：对所有记忆生成嵌入向量，建立语义检索能力。

| 序号 | 任务 | 产出 |
|------|------|------|
| 2.1 | 编写 `memory_agent/embedding.py` | OpenAI embedding API 封装（重试、错误处理、降级） |
| 2.2 | 编写 `memory_agent/chunker.py` | 长记忆分段逻辑（≤512 tokens/chunk） |
| 2.3 | 编写 `memory_agent/vector_store.py` | 向量 BLOB 读写 + cosine similarity 检索 |
| 2.4 | 为所有已有记忆生成嵌入 | 批量调用 API，写入 memory_chunks 表 |

**产出**：每条记忆至少对应一个向量，语义检索可用。

---

### Phase 3：检索层

**目标**：用混合检索替代当前 BM25-only。

| 序号 | 任务 | 产出 |
|------|------|------|
| 3.1 | 编写 `memory_agent/query_builder.py` | 从对话上下文生成 2-5 条检索 query（启发式 + 可选 LLM 展开） |
| 3.2 | 编写 `memory_agent/retrieval.py` | 混合检索：向量 cosine → RRF 融合→ FTS5 回退 → 阈值过滤 |
| 3.3 | L0/L1 始终加载逻辑 | 对话上下文中自动注入优先级记忆 |
| 3.4 | L2/L3 按需检索注入 | 对话时自动搜索相关记忆，附来源标记 |

**产出**：对话过程中自动检索相关记忆并注入上下文，标记来源。

---

### Phase 4：触发 + 提取改善

**目标**：建立混合触发机制，改善提取质量。

| 序号 | 任务 | 产出 |
|------|------|------|
| 4.1 | 编写 `memory_agent/triggers.py` | 高速信号实时触发（关键词/正则匹配），无需 LLM |
| 4.2 | 改善 extractor prompt | 传入当前时间正确标记 event_date；增加 archive/merge 输出动作 |
| 4.3 | 接入 WS 实时提取 | 高信号提取结果通过 WS 事件推送 |
| 4.4 | consolidate 适配新存储 | 改为写入 SQLite，接入淘汰判断 |

**产出**：高价值信息实时入库，周期 consolidate 批量整理。

---

### Phase 5：淘汰机制

**目标**：可配置的信息淘汰体系。

| 序号 | 任务 | 产出 |
|------|------|------|
| 5.1 | 编写 `memory_agent/elimination.py` | 四种策略实现（llm_judge / rules / hybrid / manual） |
| 5.2 | 新增 REST 端点 | pending-actions CRUD + approve/reject |
| 5.3 | 前端确认组件 | 展示待确认动作列表，可逐条审批 |
| 5.4 | 接入淘汰审计 | 所有淘汰操作写入 memory_audit |

**产出**：记忆不会无限膨胀，过时信息有序降级/归档。

---

### Phase 6：WebSocket 统一通道

**目标**：建立 JSON-RPC 2.0 over WebSocket 作为主要控制通道。

| 序号 | 任务 | 产出 |
|------|------|------|
| 6.1 | 后端 WSManager | 连接管理、消息路由、心跳、JSON-RPC 2.0 协议实现 |
| 6.2 | 前端 WSManager | 连接管理、重连、事件分发 |
| 6.3 | 逐步迁移 chat 流 | 从 SSE 迁移到 WS 通道（过渡期两者并行） |
| 6.4 | agent.runner 接入 WS | 运行状态推送、审批、取消 |

**产出**：一条 WS 连接走所有控制通信，REST 仅保留 CRUD。

---

### Phase 7：清理收尾

| 序号 | 任务 | 说明 |
|------|------|------|
| 7.1 | 删除旧 store.py / search.py | 功能已完全被新模块替代 |
| 7.2 | 删除 memory/.history | 历史版本已迁移到 SQLite |
| 7.3 | 更新 CLAUDE.md | 同步最新架构描述 |
| 7.4 | 更新设计文档 | memory-vector-embedding-design.md 状态更新 |

**产出**：最终状态——无旧架构残留，所有文档反映当前架构。

---

## 文件变更清单

### 新增文件
```
memory_agent/db.py              — SQLite schema + connection
memory_agent/embedding.py       — OpenAI embedding API 封装
memory_agent/chunker.py         — 记忆文本分段
memory_agent/vector_store.py    — 向量存储/检索
memory_agent/retrieval.py       — 混合检索引擎
memory_agent/query_builder.py   — 对话上下文 → 检索 query
memory_agent/triggers.py        — 高信号触发检测
memory_agent/elimination.py     — 淘汰策略引擎
memory_agent/ws_manager.py      — WebSocket 连接管理 + JSON-RPC 路由
memory_agent/session_manager.py — WS sesssion 状态管理
tests/test_memory_db.py         — 测试
tests/test_memory_embedding.py
tests/test_memory_retrieval.py
tests/test_memory_elimination.py
```

### 修改文件
```
memory_agent/__init__.py         — 更新模块导出
memory_agent/config.py           — 增加新配置项（DB路径、淘汰策略、WS端口等）
memory_agent/extractor.py        — 改进 prompt + 适配新存储 + 传入时间
memory_agent/mcp_server.py       — 使用新检索和存储
memory_agent/llm.py              — 增加重试逻辑、错误分类
selfecho_session/db.py           — 扩展 schema（新增表）
selfecho_session/service.py      — consolidate 适配新存储
selfecho_api/server.py           — 增加 WS 端点、pending actions 端点、淘汰确认端点
selfecho_config/prompts/memory_consolidation.md  — 更新提取指令
selfecho_config/prompts/session_summary.md       — 更新摘要指令
web/src/api.ts                   — 增加 WSManager 类
web/src/App.vue                  — 增加 pending actions 确认区域
```

### 删除文件
```
# Phase 0 剪枝
docs/agent-direction-decisions.md
docs/agent-direction-questionnaire.md
docs/deepseek-dualpath-omlx-agent-architecture.md
docs/legacy-history-import.md
docs/project-renaming-notes.md
docs/selfecho-evolution-plan-review.md
docs/claude-code-harness-to-iwiw-workflow.md
AGENTS.md（待审查后决定）

# Phase 0 git rm
memory_agent/hook_extractor.py
memory_agent/hook_heartbeat.py
selfecho_config/prompts/treehole_reply.md

# Phase 1 停用（保留读能力，删除写逻辑）
memory_agent/store.py → 保留读取方法，删除写相关

# Phase 7 彻底删除
memory_agent/store.py（完整）
memory_agent/search.py（被 retrieval.py 替代）
memory/.history/
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
EMBEDDING_PROVIDER = "openai"  # openai | local
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 512
EMBEDDING_BATCH_SIZE = 20
EMBEDDING_RETRY = 3

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

| 阶段 | 预计工作量 | 前置依赖 |
|------|-----------|---------|
| Phase 0：剪枝 | ~2 小时 | 无 |
| Phase 1：数据库层 | ~4 小时 | Phase 0 |
| Phase 2：向量嵌入 | ~3 小时 | Phase 1 |
| Phase 3：检索层 | ~4 小时 | Phase 2 |
| Phase 4：触发 + 提取 | ~3 小时 | Phase 1 |
| Phase 5：淘汰机制 | ~4 小时 | Phase 1 |
| Phase 6：WS 通道 | ~6 小时 | Phase 1-5 |
| Phase 7：清理收尾 | ~2 小时 | Phase 1-6 |

**总计**：~28 小时开发工作量，顺序执行各阶段。
