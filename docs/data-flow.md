# 数据流

本文件描述记忆系统内五条核心数据流：提取流、检索流、写入（mutation）流、维护流、启动流。

## 总览

```
用户 ──▶ CLI chat ──▶ 提取流 ──▶ SQLite（记忆真源）
   │        │                        ▲
   │        └──▶ 检索流 ─────────────┘（注入回对话）
   └──▶ 命令 ──▶ mutation 流 / 维护流 / 审批

SQLite ──▶ 启动流（core 注入）──▶ 对话上下文

MCP 工具 ──▶ 同一套 提取流 / mutation 流 / 检索流 / 维护流
```

## 1. 提取流（对话 → 记忆）

一条高价值消息如何变成长期记忆：

```
用户消息
  │
  ├─ 实时路径：chat 主循环调用 triggers.should_trigger(message)
  │     高信号（偏好/决策/反馈/事件/明确要求记忆）→ 触发 extract_and_save
  │     低信号（技术提问/简短确认/含代码符号）→ 跳过
  │
  └─ 手动路径：/extract 命令 / MCP extract_and_save 工具
        │
        ▼
extractor.extract_and_save(message, context)
  │  1. _should_skip：短消息/IDE 注入/纯命令 → 直接跳过
  │  2. _memory_catalog_text：加载已有记忆目录（slug/类型/级别/描述）
  │  3. _related_memories_text：search_memories 检索与消息相关的已有记忆全文
  │     （模型据此精准 update 与去重）
  │  4. 组装 prompt → llm.complete_text（提取系统提示）
  │  5. _parse_extraction_result：容错解析 JSON 候选
  │
  ▼
db.save_memory_candidate(candidate)   ← 每条候选
  │  create  → upsert_memory（插入）
  │  update  → replace_memory_result（全文替换，变更前快照进版本表）
  │  archive → archive_memory_result
  │  merge   → replace_memory_result 到目标 + 归档来源
  │  ignore  → 丢弃
  │
  ▼
mutation 契约（详见第 3 节）
  │  变更前快照 → memory_versions
  │  事件       → memory_audit
  │  FTS        → 触发器自动同步
  ▼
memories 表（真源）
```

## 2. 检索流（记忆 → 对话）

对话时记忆如何回到上下文：

```
用户当前消息 + 最近几轮上下文
  │
  ▼
query_builder.build_queries(message, context)
  │  关键词提取（去停用词、中文 bigram/trigram）
  │  生成 2–5 条 query：完整关键词 / 前 3–4 词 / 个人话题前缀 / 上下文关键词
  ▼
retrieval.search_memories(query, top_k)
  │  对每条 query 做词面召回，另加会话状态候选：
  │    ├─ FTS ：search_fts（FTS5 短语/前缀；空结果或分词失败 → 子串兜底）
  │    └─ 状态：session_state.recall_candidates（最近/高频讨论话题 → 关联记忆）
  │
  │  加权融合（权重来自 config）：
  │    score = Σ(FTS命中×bm25) + 状态候选×state
  │    score ×= 时间衰减因子（90 天半衰期）× 优先级因子（core 1.2 … archive 0.8）
  │  排序 → 阈值过滤（SEARCH_RELEVANCE_THRESHOLD 0.15）→ top_k
  ▼
chat 组装：相关记忆块 + 用户消息 → llm（注入对话上下文）
```

两个使用场景：
- **对话回复**：每轮把检索结果作为『## 相关记忆』块放在用户消息前，让模型基于记忆作答。
- **提取前置**：extractor 用同一检索把相关记忆全文喂给模型（第 1 节第 3 步）。

## 3. 写入（mutation）流

所有破坏性/可见变更统一走 *_result 入口，保证版本与审计不缺失：

```
调用方（chat 命令 / MCP 工具 / extractor / maintenance 审批）
  │
  ├─ replace_memory_result   全文替换
  ├─ archive_memory_result   归档（priority → archive）
  ├─ delete_memory_result    删除
  ├─ merge_memories_result   合并（双版本快照 + 归档来源）
  ├─ approve_pending_action_result  执行待确认动作
  └─ restore_memory_from_history_result  回滚恢复
        │
        ▼
MemoryMutationResult { ok, action, target_slug, changed_rows, version_id, audit_id, backup_path, error }
  │  1. _save_version → 变更前快照写入 memory_versions
  │  2. 执行变更（INSERT/UPDATE/DELETE）
  │  3. _record_audit → 事件写入 memory_audit
  │  4. changed_rows ≤ 0 → 回滚，返回失败（禁止伪成功）
  │  5. 提交（FTS 由触发器同步）
  ▼
调用方检查 ok / version_id / audit_id 后展示结果
```

## 4. 维护流

记忆库的自我维护（归档过时/冗余记忆），人工确认后执行：

```
用户 /maintain 或 MCP maintenance_review
  │
  ▼
maintenance.review_maintenance(limit)
  │  1. db.get_maintenance_candidates：访问最少、更新最早的 normal
  │  2. 组装候选目录 → llm.complete_text（维护审查提示词：保守为主）
  │  3. 解析 LLM 输出 → 仅 archive 动作
  │  4. create_pending_action → memory_pending_actions（status=pending）
  ▼
用户 /pending 查看 → /pending approve|reject
  │  approve → approve_pending_action_result（走 mutation 流：快照+审计+执行）
  │  reject  → 状态置 rejected
  ▼
记忆归档/保持不变
```

## 5. 启动流

对话工作台启动时构建初始上下文：

```
python -m memory_agent.chat
  │
  ├─ _always_load_text()：加载 core（必须载入）记忆全文 → 追加到系统提示（受字符预算约束）
  ├─ 初始化内存会话上下文（最近 N 轮，不持久化）
  ▼
进入主循环：输入 → 触发检测 → 检索注入 → LLM 回复 → 记录历史
```

## 关键文件映射

| 数据流 | 入口 | 关键函数 |
|---|---|---|
| 提取 | chat.py / mcp_server.py | extract_and_save → save_memory_candidate |
| 检索 | chat.py / extractor.py | search_memories → build_queries |
| 写入 | 各 *result | replace/archive/delete/merge/approve/restore |
| 维护 | chat.py / mcp_server.py | review_maintenance → create_pending_action |
| 启动 | chat.py | _always_load_text |
