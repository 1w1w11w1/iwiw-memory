# 记忆内核接入 DSH / QQ-bot 演进方案

> 主开发方向：生动的 chat 方案。记忆内核（memory_agent）作为共享能力，经 DSH 插件、QQ-bot 等多端复用。
> 本方案聚焦 DSH 插件（先做），并给出多端复用的整体演进。

## 0. 现状与目标能力

记忆内核已就绪：SQLite 分级记忆 + DSC 会话状态检查点 + 确定性检索（jieba/FTS/同义词）+ LLM 提取 + 维护审查 + MCP 桥（17 工具）。

接入 DSH 后要达成的 agent 能力：
- **跨会话记忆**：不同 DSH 会话共享同一记忆库，新会话自动带上用户长期画像；
- **自动学习**：DSH 对话中自动提取记忆（无需用户说"记住"）；
- **自动反省优化**：周期性维护（归档过时）、经验提炼、按使用反馈调整记忆权重。

## 1. 整体架构（多端复用）

```
                    ┌────────────── 记忆内核（memory_agent）──────────────┐
                    │  Python 包（可安装）+ 独立 HTTP 服务（多端共享）       │
                    │  · 记忆 CRUD / 检索 / 提取 / 维护 / 会话状态          │
                    └──────┬──────────────┬──────────────┬──────────────┘
                           │              │              │
         DSH 插件（TS）    │   QQ-bot      │   生动 chat  │
         · MCP 桥（先）    │   · 复用内核  │    · chat.py │
         · cordis 插件     │   · 用户画像  │    · 多模态  │
           （自动学习）     │              │              │
```

## 2. 演进阶段

### Phase A：MCP 桥接入（最快见效）

- 用 DSH 的 dsh-mcp-client 挂载 memory_agent.mcp_server（stdio）；
- DSH agent 立即获得 17 个记忆工具（检索/写入/审批/维护）；
- 验证：DSH 会话内 /mem 检索，两个会话间记忆共享；
- 局限：模型需主动调用，非"自动"。

### Phase B：dsh-memory cordis 插件（跨会话记忆 + 自动学习）

用 TypeScript 开发 cordis 插件包（参照 dsh-tool-* / preset 机制），注册：

| 能力 | 机制 | 效果 |
|---|---|---|
| 工具面 | 注册 memory_* 工具（转发后端 HTTP） | agent 主动操作记忆 |
| 启动注入 | prompt section：会话启动注入 core（L0） | 跨会话记忆（新会话带画像） |
| 自动提取 | 监听消息事件 → triggers.should_trigger → extract_and_save | 自动学习（无需用户指令） |
| 联想注入 | 每轮话题检索注入 system | 对话自然联想（沿用 SessionState 思路） |
| 审批入口 | pending 动作展示/审批 | 维护可控 |

- 后端：memory_agent 以独立 HTTP 服务跑（多端共享，而非 MCP stdio 单会话）；
- 插件与后端解耦：插件只做 DSH 侧编排，记忆逻辑全在后端。

### Phase C：自动反省优化

| 能力 | 机制 | 说明 |
|---|---|---|
| 周期维护 | 定时任务调 review_maintenance | 生成归档候选 → DSH 会话内审批或自动执行 |
| 经验提炼 | 会话结束后总结 → 提炼经验类记忆（reference） | 从实践中学到"怎么做更好" |
| 使用反馈 | 追踪记忆引用 → 更新 access_count/权重 | 常用记忆升权、冷门降权（自动学习闭环） |
| 冲突检测 | 新提取与旧记忆矛盾时标记 | 避免覆盖用户明确更正 |

### Phase D：生动的 chat 方案 + QQ-bot

- **生动 chat**：升级 chat.py 为产品界面（个性、情绪感知、主动互动、多模态），复用记忆内核；或作为 DSH 前端皮肤。
- **QQ-bot**：接入 QQ 官方/协议框架，复用内核 HTTP API；跨会话用户画像 + 长期偏好自动适配。

## 3. 技术选型要点

| 决策 | 建议 | 理由 |
|---|---|---|
| 后端进程形态 | 独立 HTTP 服务 | 多端（DSH/QQ/chat）共享一个后端，而非每端各起 MCP |
| 插件 vs MCP | MCP 先行验证，cordis 插件做自动 | MCP 快速、插件深度（自动提取/注入只有插件能做） |
| 插件部署 | 本地 link 先行，npm 发布可选 | 开发期 link 到 profile，稳定后发 npm |
| 记忆内核包化 | memory_agent 拆 pyproject | 可安装、版本化、被多端依赖 |
| 与 Hindsight 关系 | 并存 | Hindsight 管平台知识，本系统管个人长期事实 |

## 4. 关键风险与对策

| 风险 | 对策 |
|---|---|
| 插件里 prompt 注入稀释模型注意力 | 沿用 core 预算 + 联想预算（MEMORY_RECALL_MAX_CHARS） |
| 自动提取误抓技术讨论 | triggers 低信号排除 + 提取 prompt 规则（已有） |
| 多端并发写 | 后端 HTTP 统一 mutation 入口 + WAL/busy_timeout（已有） |
| 插件与 DSH 版本兼容 | 跟随 dsh-* 包的 cordis 契约；先本地验证再发布 |

## 5. 落地顺序

1. Phase A：MCP 挂载（半天）→ 验证跨会话记忆；
2. 内核 HTTP 服务 + pyproject 包化（1-2 天）；
3. Phase B：dsh-memory 插件（工具 + 启动注入 + 自动提取，3-5 天）；
4. Phase C：自动反省（维护/经验/反馈，2-3 天）；
5. Phase D：生动 chat 与 QQ-bot 按业务并行。
