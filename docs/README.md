# 文档索引

本项目（记忆系统）的设计文档。

| 文档 | 内容 |
|------|------|
| [architecture.md](architecture.md) | 系统架构：模块分层、职责、设计不变量、核心机制 |
| [data-flow.md](data-flow.md) | 数据流：提取流 / 检索流 / 写入（mutation）流 / 维护流 / 启动流 |
| [chat-guide.md](chat-guide.md) | CLI 工作台使用指南与命令参考 |
| [dsh-plugin-evolution.md](dsh-plugin-evolution.md) | 多端接入演进方案 / 路线图（DSH 插件已落地，QQ-bot 规划中） |
| [dsh-mcp-setup.md](dsh-mcp-setup.md) | DSH MCP 挂载指南（Phase A） |
| [dsh-integration.md](dsh-integration.md) | （历史）MCP 直挂规划——已被 `dsh-iwiw-memory` 插件路径取代 |
| [context-strategy-review.md](context-strategy-review.md) | 上下文压缩与注入策略系统性评估 |
| [codex-context-strategy.md](codex-context-strategy.md) | Codex 上下文压缩机制梳理（DSC 检查点） |
| [associative-recall-architecture.md](associative-recall-architecture.md) | 联想架构评估：向量是错配工具，重构方向 |
| [competitive-comparison.md](competitive-comparison.md) | 竞品结构对比：经验吸取与不足清单 |
| [evidence-audit.md](evidence-audit.md) | 机制证据审计：验证有效/竞品存在/直觉自创 |
| [exploration-report.md](exploration-report.md) | LoCoMo 评测 + 认知科学架构 详细探索报告 |
| [session-experience-review.md](session-experience-review.md) | 会话体验审视：架构不足及其 chat 后果 |

快速入口：
- 想了解系统怎么组织 → [architecture.md](architecture.md)
- 想了解一条消息如何变成记忆、记忆如何回到对话 → [data-flow.md](data-flow.md)
- 想上手使用 → [chat-guide.md](chat-guide.md)
