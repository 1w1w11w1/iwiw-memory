# 旧会话历史导入说明

SelfEcho 早期曾使用外部会话历史项目保存 Claude Code 对话。该外部项目现在已经从顶层目录移除，旧会话数据库被保留为 SelfEcho 自己的数据资产：

```text
selfecho_data/legacy/claude_code_history.db
```

## 当前原则

- 旧历史只作为导入资料和背景搜索资料。
- 旧历史不再定义 SelfEcho 的产品概念。
- 旧历史不参与新 GUI 会话生命周期。
- GUI 默认优先显示 SelfEcho 自己的会话。
- 导入后的旧会话 source 标记为 `legacy_claude_code`。

## 导入入口

API：

```http
POST /api/session-memory/migrate-legacy
```

GUI：

```text
记忆 -> 会话 -> 导入旧历史
```

## 数据迁移记录

旧数据库从顶层外部项目目录迁移到：

```text
selfecho_data/legacy/claude_code_history.db
```

迁移后，顶层旧项目目录已移除，以避免后续开发时误把旧架构当作当前架构中心。

