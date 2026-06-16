# IwIw

IwIw 是一个记忆驱动的本地个人智能体实验项目。

它现在由四层组成：

- `memory_agent/`：维护 `memory/` 下的 Markdown 长期事实记忆。
- `selfecho_session/`：IwIw 原生会话记忆层，保存 GUI 树洞会话、周期摘要、会话搜索和历史回放。
- `selfecho_agent/`：规划中的 agent harness 层，用于运行追踪、上下文组装、模式识别、工具执行和问题处理。
- `selfecho_api/` + `web/`：本地 API 与简洁 GUI。

## 设计原则

- Markdown 长期记忆仍是真源，保留 `core / important / normal / archive` 权重分级。
- GUI 会话保存完整原始消息，摘要只用于上下文管理，不替代原始记录。
- 记忆整理从单条 hook 前置抽取，转向 GUI 会话周期整理。
- 旧历史库只作为可迁移资料，不作为 IwIw 的产品边界。
- 树洞是一种对话策略分支，用于倾诉、承接和记录；项目整体目标是成熟的个人智能体。
- 智能体能力应通过可观察、可测试、可回滚的 harness 推进，而不是直接堆叠工具调用。

## 快速开始

1. 复制 `.env.example` 为 `.env`，填入 `MEMORY_AGENT_LLM_API_KEY`。
2. 安装后端依赖：

```powershell
pip install -r requirements.txt
```

3. 安装前端依赖并构建：

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

开发前端时可使用：

```powershell
cd web
npm run dev
```

Vite 开发服务器默认代理 `/api` 到 `http://127.0.0.1:8765`。

## 数据目录

```text
memory/                         # Markdown 长期事实记忆
memory/.history/                # 记忆编辑、删除、合并前的备份
selfecho_data/sessions.db       # SelfEcho 会话记忆数据库
selfecho_data/legacy/           # 旧会话历史导入资料
selfecho_data/audit/            # 审计和导出预留目录
selfecho_config/prompts/        # 对话、问题处理与记忆整理策略
selfecho_config/providers.local.json  # 本地模型配置（不提交）
```

## 主要能力

- 倾诉模式：承接用户表达，保存原始消息，可手动或周期整理会话。
- 问题处理：规划中的 agent harness 将负责模式识别、上下文组装、计划、执行和纠错。
- 会话记忆：搜索、回放、导入旧历史资料。
- 长期记忆：查看、搜索、编辑、归档、删除、合并、回滚。
- Prompt 策略：查看和编辑倾诉回复、问题处理、记忆整理、摘要、编辑策略。
- 模型管理：本地 provider 配置和连接测试。
- 健康状态：查看记忆、会话库、模型和 Prompt 状态。

## 架构文档

- [智能体方向决策记录](docs/agent-direction-decisions.md)
- [SelfEcho 演变计划评审与修订建议](docs/selfecho-evolution-plan-review.md)
- [DeepSeek、DualPath 与 oMLX 对 SelfEcho 智能体架构的启发](docs/deepseek-dualpath-omlx-agent-architecture.md)
