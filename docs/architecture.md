# 记忆系统架构

## 目标形态

一个**独立的本地长期记忆系统**：以 SQLite 分级事实记忆（core/normal/archive）为真源，自带 CLI 对话工作台作为使用与验证界面，并以 MCP 对外提供完整工具面。不依赖任何外部 harness；第二阶段再以插件/MCP 形式接入 DSH。

## 分层

```
┌─────────────────────────────────────────────────────────┐
│ 界面层                                                     │
│   chat.py        CLI 对话工作台（core 注入 + 话题检索 + 实时提取） │
│   mcp_server.py  MCP 工具面（16 个工具，DSH 接入桥）           │
├─────────────────────────────────────────────────────────┤
│ 应用逻辑层                                                  │
│   extractor.py     LLM 提取（create/update/archive/merge）   │
│   maintenance.py   记忆维护审查 → 生成待确认动作               │
│   triggers.py      高信号实时触发（纯规则）                    │
│   retrieval.py     确定性检索（词面+状态加权融合）             │
│   query_builder.py 查询扩展                                 │
├─────────────────────────────────────────────────────────┤
│ 存储与基础层                                                 │
│   db.py            SQLite 存储 + mutation + 版本/审计 + FTS     │
│   session_state.py 会话状态检查点（话题/决策/任务，DSC）      │
│   llm.py           多轮 LLM 调用（Anthropic/OpenAI 兼容）     │
│   config.py        环境变量配置（data/memory.db 真源）        │
└─────────────────────────────────────────────────────────┘
```

## 模块职责

### 界面层

- **chat.py**：唯一对话入口。启动时注入 core（必须载入）记忆全文；每轮对用户消息做话题检索并注入相关记忆；高信号消息立即触发提取；提供全套记忆管理命令。
- **mcp_server.py**：无界面工具面，覆盖记忆系统的完整能力（提取/检索/CRUD/审批/回滚/维护），供 DSH 等外部 harness 调用。

### 应用逻辑层

- **extractor.py**：把一段对话文本交给 LLM，产出维护动作候选（create/update/archive/merge/ignore）。**update 是全文替换语义**：调用前先用混合检索把可能相关的已有记忆（含全文）喂给模型，使模型能基于旧内容改写完整新正文。
- **maintenance.py**：定期/按需审查记忆候选（访问最少、更新最早的 normal），由 LLM 判断哪些过时或冗余，生成**待确认**的归档动作（不自动执行）。
- **triggers.py**：纯规则实时检测高信号（偏好声明、决策、反馈、重要事件、明确要求记忆等），命中即触发提取；低信号（技术提问、简短确认、含代码符号）排除。
- **retrieval.py**：确定性检索入口。FTS 词面召回 + 会话状态候选（SessionState 回指联想）+ 时间衰减与优先级加权，再经阈值过滤（无向量，见 associative-recall-architecture.md）。
- **query_builder.py**：jieba 分词 + 同义词扩展 + 对话主题联想，生成 2–5 条检索 query。

### 存储与基础层

- **db.py**：schema（memories / memory_versions / memory_audit / memory_pending_actions / memories_fts）；统一 mutation 入口（*_result 系列）；版本快照、审计、回滚；FTS5 触发器同步；SQLite WAL + busy_timeout。
- **session_state.py**：DSC 会话状态检查点 —— 记录会话中讨论过的话题（关联记忆）、决策、任务，用于回指联想与上下文压缩。
- **llm.py**：Anthropic/OpenAI 两种兼容调用，支持多轮消息与流式。
- **config.py**：路径（data/memory.db 真源）、LLM、提取、分级、检索、会话压缩参数，全部可经环境变量覆盖。

## 数据真源

| 数据 | 位置 | 说明 |
|---|---|---|
| 长期记忆 | data/memory.db → memories | 唯一真源 |
| 会话状态 | SessionState（内存） | 会话话题/决策/任务检查点 |
| 版本历史 | memory_versions | 变更前快照，可回滚 |
| 审计 | memory_audit | 每次变更的完整事件 |
| 待确认动作 | memory_pending_actions | 维护候选审批队列 |
| 关键词索引 | memories_fts（FTS5） | 中文子串兜底 |

## 记忆分级（三值）

| 值 | 语义 | 加载策略 |
|---|---|---|
| core | 身份、健康、关系、重大决策、核心偏好 | 必须载入（启动注入） |
| normal | 日常信息、阶段计划、一般偏好 | 按需载入（话题检索） |
| archive | 过时/冗余记忆（归档状态，可回滚） | 不参与常规检索与维护候选 |

> archive 是生命周期状态（维护流程的产物），不是重要性档位；旧四级中的 important 并入 core。

## 设计不变量

1. data/memory.db 的 SQLite 是唯一真源；不存在 Markdown/文件真源。
2. 所有破坏性/可见变更走 *_result mutation 入口，返回 version_id / audit_id / changed_rows；changed_rows == 0 不算成功。
3. 删除保留版本（memory_versions），可回滚恢复。
4. 更新是全文替换，不是追加。
5. 内容变更后同步刷新/清理 FTS。检索全部确定性（FTS + 同义词 + 会话状态），无向量。
6. 检索必须进入真实对话上下文链路（注入），不只是展示。
7. 维护动作（归档/删除候选）只生成 pending，由用户确认后执行。
