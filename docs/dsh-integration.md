# 第二阶段：接入 DSH 规划

> **（历史文档）** 本文写于插件化之前，推荐的 MCP 直挂路线已被 `dsh-iwiw-memory` 插件取代（插件经 MCP 子进程挂载内核，工具面/注入/设置页一并托管）。保留作设计背景；当前路线见 [dsh-plugin-evolution.md](dsh-plugin-evolution.md) 与[插件 README](../dsh-iwiw-memory/README.md)。

> 本文是规划文档，第二阶段实施时才执行。当前阶段保持独立，不依赖 DSH。

## 目标

让记忆系统以工具面形式进入 DSH 会话：任何 DSH agent 都能检索、写入、维护长期记忆。

## 现状（第一阶段已就绪的部分）

1. **MCP 工具面完整**：`memory_agent/mcp_server.py` 通过 stdio 提供 17 个工具（提取/检索/CRUD/审批/回滚/维护）。
2. **零依赖核心**：memory_agent 不依赖任何外部 harness，可直接作为独立进程被 MCP 客户端拉起。
3. **本地嵌入降级**：嵌入不可用时检索自动降级到 FTS + 子串。

## 接入路径（推荐：MCP）

DSH 原生支持 MCP 客户端（`@deepseek-ai/dsh-mcp-client`）。在 DSH 的 cordis 配置中挂载：

```yaml
- id: memory-system
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: memory-system
    transport: stdio
    command: python
    args: ['-m', 'memory_agent.mcp_server']
    cwd: <本仓库路径>
    env:
      PYTHONPATH: <本仓库路径>
```

## 深度集成的差距（MCP 做不到的部分）

| 能力 | 现状 | 深度集成方案（cordis 插件） |
|---|---|---
| core 自动注入 | MCP 需模型主动调用 | 插件注册 prompt section，启动注入 |
| 每轮自动检索 | MCP 需模型主动调用 | 插件监听 agent 事件，自动检索注入 |
| 高信号自动提取 | MCP 需模型主动调用 | 插件在消息事件后调用 extract_and_save |
| 记忆管理 UI | 无 | 插件提供 Web 设置面 |

## 决策点（第二阶段开始时确认）

1. 先走 MCP 快速接入验证，还是直接开发 cordis 插件（深度集成）？
2. 与 DSH 内置 Hindsight 记忆的关系：并存（本系统专注个人长期事实，Hindsight 管平台知识）还是替代？
3. MCP 工具名是否加 `memory_` 前缀避免与 harness 工具冲突？
