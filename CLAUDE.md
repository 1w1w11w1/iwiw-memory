# IwIw 项目行为指令

IwIw 是一个记忆驱动的本地个人智能体。

## 当前架构

- `memory/`：Markdown 长期事实记忆，仍是真源。
- `memory_agent/`：长期记忆读写、搜索、索引重建、MCP 工具和提取引擎。
- `selfecho_session/`：IwIw 原生会话记忆层，使用 `selfecho_data/sessions.db`。
- `selfecho_api/`：本地 FastAPI 服务。
- `web/`：GUI。
- `selfecho_config/`：prompt、模型 provider 和后续策略配置。

旧 Claude Code 历史数据只作为 legacy 导入资料保存在 `selfecho_data/legacy/claude_code_history.db`，不再作为项目架构中心。

## 行为原则

1. 启动时读取 `memory/MEMORY.md`，加载 L0/L1 记忆。
2. L2/L3 只在话题相关或用户要求回顾时检索。
3. GUI 会话原始消息必须保留，摘要不能替代原始记录。
4. 会话整理应服务于长期记忆维护，但写入应可审计、可回滚。
5. 非工作状态优先接住表达；工作模式优先澄清、规划和推进。
6. 项目长期目标是成熟 agent harness，而不是纯聊天应用。

## 记忆分级

| 级别 | 标签 | 说明 |
|------|------|------|
| L0 | `core` | 核心身份、稳定认知模式、长期交流偏好 |
| L1 | `important` | 健康、关系、重大决策、长期压力源 |
| L2 | `normal` | 日常偏好、阶段计划、一般事实 |
| L3 | `archive` | 过时事实、历史事件、低频参考 |
