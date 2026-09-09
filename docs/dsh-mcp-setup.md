# DSH MCP 挂载指南

> 让 DSH agent 通过 MCP 直挂获得记忆工具面（适合轻量接入；完整体验——常驻注入/空闲巩固——使用 `dsh-iwiw-memory` 插件，见插件 README）。
> 前提：memory_agent 已包化（`pip install -e .`，入口 `memory-agent-mcp`）。

## 1. 验证后端可启动

```powershell
python -m memory_agent.mcp_server        # 或 memory-agent-mcp
```

## 2. 在 DSH 挂载（cordis.patch.yml）

在 `$DSH_HOME/cordis.patch.yml`（或对应 profile 的 patch）追加一行：

```yaml
- id: memory-system
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: memory-system
    transport: stdio
    command: C:/Users/YS/AppData/Local/Programs/Python/Python312/python.exe
    args:
      - -m
      - memory_agent.mcp_server
    cwd: E:/desktop/111
    env:
      HF_HUB_OFFLINE: "1"
```

> 说明：
> - `command` 用安装了 mcp/jieba 的 Python（本项目 .venv 或系统 Python + pip install 本项目）；
> - `cwd` 指向 memory_agent 所在目录（包化后可通过 pip 安装，不必依赖 cwd）；
> - 重启 DSH 后，会话即可调用 14 个记忆工具。

## 3. 验证

在任意 DSH 会话中让模型调用：
- `memory_remember`（写入记忆）
- `search_memories`（检索）
- `memory_update` / `memory_archive`（维护）

跨会话验证：会话 A 写入记忆 → 新会话 B 检索到同一条。

## 4. 局限与完整体验

MCP 直挂是"模型主动调用"，不做常驻注入与空闲巩固。
需要跨会话记忆完整体验（常驻注入 / 命中注入 / 空闲巩固），使用 `dsh-iwiw-memory` 插件（见插件 README）。
