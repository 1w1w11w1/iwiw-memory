# 数据流

本文件描述记忆系统内五条核心数据流：工具写入流、检索流、写入（mutation）流、维护流、启动流。

## 总览

```
用户 ──▶ CLI chat ──▶ 工具写入流 ──▶ SQLite（记忆真源）
   │        │                        ▲
   │        └──▶ 检索流 ─────────────┘（注入回对话）
   └──▶ 命令 ──▶ mutation 流 / 维护流 / 审批

SQLite ──▶ 启动流（core 注入）──▶ 对话上下文

MCP 工具 ──▶ 同一套 mutation 流 / 检索流 / 维护流
```

## 1. 工具写入流（对话 → 记忆）

模型在对话中自主调用记忆工具完成写入（无独立提取管线、无触发词表）：

```
用户消息
  │
  ▼
chat 主循环（回复前）→ llm.complete_with_tools（非流式，带 4 个记忆工具）
  │  模型自主决策：
  │    ├─ memory_remember：写入稳定事实（slug 可选；写入即 upsert 全文替换）
  │    │    新建前模型可先 memory_search 查重（工具描述引导）
  │    ├─ memory_search / memory_read / memory_list：检索与读取
  │  工具结果以 role=tool 消息回传（tool_call_id 一一对应），最多 TOOL_MAX_LOOPS 轮
  │
  ▼
db.upsert_memory(slug, description, content, ...)
  │  slug 不存在 → 创建（audit: create）
  │  slug 已存在 → replace_memory_result（全文替换，快照进版本表，audit: upsert_replace）
  │
  ▼
mutation 契约（详见第 3 节）
  │  变更前快照 → memory_versions
  │  事件       → memory_audit
  │  FTS        → 触发器自动同步
  ▼
memories 表（真源）

写入准则（工具描述 + system prompt 承载）：
- 只记稳定事实与用户明确要求记住的内容；一次性/临时话题不写
- 用户最新表述优先
- 不向用户承诺"已记住"，除非确实调用了 memory_remember
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
- **工具查重**：模型新建记忆前可用同一检索（memory_search）查重（第 1 节）。

## 3. 写入（mutation）流

所有破坏性/可见变更统一走 *_result 入口，保证版本与审计不缺失：

```
调用方（chat 命令 / MCP 工具 / 模型记忆工具 / maintenance 审批）
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
| 工具写入 | chat.py | complete_with_tools → upsert_memory |
| 检索 | chat.py | search_memories → build_queries |
| 写入 | 各 *result | replace/archive/delete/merge/approve/restore |
| 维护 | chat.py / mcp_server.py | review_maintenance → create_pending_action |
| 启动 | chat.py | _always_load_text |
