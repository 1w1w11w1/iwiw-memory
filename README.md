# IwIw

IwIw 是一个记忆驱动的本地个人智能体。当前重点不是继续堆聊天能力，而是把记忆、会话、上下文组装、工具执行、权限边界和审计逐步收敛成成熟的 agent harness。

## 当前结构

- `memory_agent/`：长期事实记忆层，真源是 `selfecho_data/sessions.db` 中的 SQLite 表。
- `selfecho_session/`：IwIw 会话记忆层，保存 GUI 会话、原始消息、滚动摘要、周期摘要、会话搜索和回放。
- `selfecho_agent/`：agent harness 层，负责上下文组装、模式识别、运行追踪和问题处理流程。
- `selfecho_api/` + `web/`：本地 API 与 GUI。
- `memory/`：历史遗留或人工可读导出缓存，不再是写入真源。

## 记忆系统原则

- 长期事实记忆以 SQLite 为真源，核心表包括 `memories`、`memory_chunks`、`memories_fts`、`memory_pending_actions`、`memory_versions`、`memory_audit`。
- 启动时只加载 L0 `core` 和 L1 `important` 完整内容；L2/L3 按话题检索。
- GUI 会话必须保存完整原始消息，摘要只用于上下文管理，不能替代原始记录。
- 长期记忆的写入、编辑、合并、归档、删除和回滚必须走统一 mutation 入口，并写入版本与审计。
- Markdown 导出如存在，只是可读缓存；不能绕过 SQLite 直接改长期记忆。

## 快速开始

1. 复制 `.env.example` 为 `.env`，填写本地模型或 API 配置。
2. 安装后端依赖：

```powershell
pip install -r requirements.txt
```

3. 安装并构建前端：

```powershell
cd web
npm install
npm run build
cd ..
```

4. 启动本地 API 和 GUI：

```powershell
python -m selfecho_api
```

浏览器访问 `http://127.0.0.1:8765`。

开发前端时使用：

```powershell
cd web
npm run dev
```

Vite 开发服务器默认代理 `/api` 到 `http://127.0.0.1:8765`。

## 数据目录

```text
selfecho_data/sessions.db       # 长期事实记忆 + IwIw 会话记忆真源
selfecho_data/legacy/           # 旧历史导入资料
selfecho_data/audit/            # 审计和导出预留目录
memory/                         # 历史遗留或 SQLite 导出缓存
selfecho_config/prompts/        # 对话、问题处理、记忆整理与编辑策略
selfecho_config/providers.local.json  # 本地模型配置，不提交
```

## 主要能力

- 陪我想想：承接用户表达，保存原始会话，可手动或周期整理。
- 工作模式：逐步承载澄清、规划、执行、工具调用和错误恢复。
- 会话记忆：搜索、回放、导入旧历史资料。
- 长期记忆：查看、搜索、编辑、归档、删除、合并、回滚。
- Prompt 策略：查看和编辑对话、问题处理、记忆整理、摘要、记忆编辑策略。
- 模型管理：本地 provider 配置和连接测试。
- 健康状态：检查记忆库、会话库、模型和 Prompt 状态。

## 架构文档

- [记忆系统当前架构](docs/memory-system-architecture.md)
- [Agent Harness 架构](docs/iwiw-agent-harness-architecture.md)
- [模型管理](docs/iwiw-model-management.md)
- [WebSocket 统一通道设计](docs/phase6-websocket-design.md)
