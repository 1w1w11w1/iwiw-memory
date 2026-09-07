# DSH MCP 挂载指南（Phase A 交付）

> 让 DSH agent 通过 MCP 桥获得记忆能力（跨会话记忆 MVP）。
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
> - 重启 `dsh web` 后，DSH 会话即可调用 16 个 memory 工具。

## 3. 验证

在任意 DSH 会话中让模型调用：
- `extract_and_save`（提取记忆）
- `search_memories`（检索）
- `memory_update` / `memory_archive`（维护）

跨会话验证：会话 A 写入记忆 → 新会话 B 检索到同一条。

## 4. 局限与下一步

MCP 桥是"模型主动调用"，做不到自动注入/自动提取。
自动学习（启动注入 core / 消息事件自动提取）需要 Phase B 的 cordis 插件。
