# CLI 工作台使用指南

## 启动

```powershell
pip install -r requirements.txt        # 首次
# 编辑 .env 填入 MEMORY_AGENT_LLM_API_KEY
python -m memory_agent.chat
```

检索全部确定性（jieba 分词 + FTS + 同义词表 + 会话状态），无嵌入模型下载、零模型内存。

## 对话

- 普通输入即对话。启动时自动注入 core（必须载入）记忆全文；每轮按话题检索相关记忆注入上下文。
- 模型可在回复前自主调用记忆工具（memory_remember/search/read/list）写入与检索；写入在控制台以 `[记忆工具]` 回显（需 MEMORY_AGENT_ECHO_STATE=1）。
- 会话上下文只保存在内存（最近 N 轮，默认 12），退出即清空；持久化会话由外部 harness 承担。

## 命令参考

### 记忆管理

| 命令 | 说明 |
|---|---|
| /mem list [priority] | 列出记忆（可按 core/normal/archive 过滤） |
| /mem search <关键词> | 搜索记忆（关键词 + 同义词 + 会话状态联想） |
| /mem read <slug> | 读取记忆正文 |
| /mem edit <slug> | 编辑记忆：显示当前正文，输入新正文，单独一行 __END__ 结束 |
| /mem archive <slug> | 归档（priority → archive，可回滚） |
| /mem delete <slug> | 删除（先快照，可回滚，需确认 y） |
| /mem merge <目标> <来源> | 合并：显示两条内容，输入合并后正文，来源默认归档 |
| /mem history <slug> | 版本历史 |
| /mem rollback <slug> <版本号> | 回滚到指定版本 |

### 维护与审批

| 命令 | 说明 |
|---|---|
| /maintain | 审查维护候选（访问最少/更新最早的 normal），生成归档待确认动作 |
| /pending [status] | 查看待确认动作（pending/approved/rejected/executed） |
| /pending approve <id> | 审批通过并执行（走版本/审计） |
| /pending reject <id> | 拒绝 |

### 其他

| 命令 | 说明 |
|---|---|
| /stats | 记忆库统计（总数/分级/类型） |

| /help | 帮助 |
| /quit | 退出 |

## 典型使用流程

1. 聊天中自然积累记忆（模型自主调用 memory_remember 写入，值得记才写）。
2. 定期 `/maintain` 审查 → `/pending` 查看候选 → `/pending approve|reject` 处理。
3. 手动纠错：`/mem read <slug>` 查看 → `/mem edit <slug>` 修正，或 `/mem rollback <slug> <版本>` 回退。
4. 清理：`/mem archive <slug>` 归档过时内容，`/mem merge <目标> <来源>` 合并重复。

## MCP 工具面

`python -m memory_agent.mcp_server` 以 stdio 提供 14 个工具（供 DSH 等 harness 挂载）：

search_memories / list_memories / read_memory / memory_stats / memory_update / memory_archive / memory_delete / memory_merge / memory_history / memory_rollback / pending_actions / pending_approve / pending_reject / maintenance_review
