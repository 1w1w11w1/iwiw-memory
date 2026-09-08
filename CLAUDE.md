# 记忆系统项目约束（CLAUDE.md）

本文件是 Claude Code 在本项目中的项目级约束。与 AGENTS.md 保持一致；若冲突，先修正文档契约再继续实现。

## 项目定位

独立的本地长期记忆系统。核心 memory_agent/ 自包含；CLI 工作台 memory_agent/chat.py 是唯一对话界面；memory_agent/mcp_server.py 是对外工具面（第二阶段接入 DSH 的桥）。不再维护自研 harness / 会话层 / Web GUI。

## 当前架构

- 数据真源：data/memory.db（SQLite，含 memories、memory_versions、memory_audit、memory_pending_actions、memories_fts）。
- 记忆核心：memory_agent/db.py（schema、mutation、版本/审计、FTS）、retrieval.py（确定性检索）、query_builder.py / session_state.py（确定性检索与状态）、llm.py（多轮 LLM 调用与工具调用）。记忆写入 = 模型在对话中自主调用记忆工具（chat.py 注册 memory_remember/search/read/list 四个 function calling 工具），无独立提取管线。
- 界面：chat.py（CLI）、mcp_server.py（MCP 工具）。

## 记忆系统不变量

1. SQLite 为唯一真源；无 Markdown 真源。
2. 分级三值：core 必须载入（启动注入）；normal 按需检索；archive 为归档状态。
3. 所有破坏性/可见变更走 *_result mutation 入口，返回 version_id/audit_id/changed_rows。
4. 删除保留版本，可回滚。
5. 更新是全文替换（非追加）。
6. 内容变更后刷新 FTS。
7. 检索必须进入真实对话上下文链路。

## 开发护栏

1. 文档状态不算完成；必须附带命令、测试或 smoke。
2. 跨模块改动先做 tracer bullet 最小闭环。
3. 删除模块前列替代矩阵。
4. 数据破坏性操作走统一 mutation 入口。
5. 新依赖同步 requirements.txt。
6. 核心机制评估：python tests/memory_system_eval.py。
