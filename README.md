# IwIw

IwIw 是一个记忆驱动的本地个人智能体。当前开发重点是重构记忆系统，并让 agent 的上下文组装、运行追踪、工具执行、权限边界和审计逐步收敛成清晰的本地 harness。

## 当前结构

- `memory_agent/`：记忆层，负责全量材料入库、轻量召回、倾向编译、版本和审计。
- `selfecho_session/`：会话层，保存 GUI 会话、原始消息、滚动摘要、会话搜索和回放。
- `selfecho_agent/`：agent harness 层，负责模式识别、上下文组装、运行追踪和问题处理流程。
- `selfecho_api/` + `web/`：本地 API 与 GUI。
- `memory/`：人工可读导出缓存，不是写入真源。

## 记忆系统

记忆系统分成两层：

- **全量记忆库**：保存会话消息、工具结果、手动记录和项目事件的原始包装。它不经 LLM 裁决是否值得保存，依靠向量、关键词、时间和作用域做检索。
- **倾向上下文**：由 LLM 从高信号对话和项目事件中整理 agent 全局倾向、工作目录倾向和当前会话 overlay，形成每轮按继承链注入的短 profile。

全量记忆库不对材料做预先价值判断；倾向上下文只负责每轮回应和决策的行为默认值。

运行时规则：

- 每轮默认做轻量候选检索，但只有通过 relevance gate 的片段才注入上下文。
- 倾向 profile 每轮稳定注入，按 agent 全局、工作目录、当前会话 overlay 的顺序继承。
- 倾向整理由观察、编译、重建三层触发，不只依赖固定周期。
- GUI 会话必须保存完整原始消息，摘要和 profile 不能替代原始记录。
- 写入、编辑、合并、删除和回滚必须保存版本并写入审计。

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
selfecho_data/sessions.db       # 记忆系统 + 会话系统真源
selfecho_data/audit/            # 审计和导出预留目录
memory/                         # 人工可读导出缓存
selfecho_config/prompts/        # 对话、摘要、记忆整理和编辑策略
selfecho_config/providers.local.json  # 本地模型配置，不提交
```

## 主要能力

- 陪我想想：承接用户表达，保存原始会话，并在合适时整理倾向。
- 工作模式：承载澄清、规划、执行、工具调用、错误恢复和可回放 trace。
- 全量记忆：保存原始材料，支持轻量召回和深度检索。
- 倾向上下文：维护 agent 全局、工作目录和当前会话 overlay profile，影响 agent 回应和决策默认值。
- 记忆维护：查看、搜索、编辑、删除、合并、回滚和审计。
- 模型管理：本地 provider 配置和连接测试。

## 架构文档

- [记忆系统架构](docs/memory-system-architecture.md)
- [智能体 Harness 架构](docs/agent-harness-architecture.md)
- [系统设计架构](docs/iwiw-system-design.md)
- [模型管理](docs/iwiw-model-management.md)
