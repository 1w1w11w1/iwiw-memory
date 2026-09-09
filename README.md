# iwiw-memory（iwiw 记忆）

一个带长期记忆系统的 chat：核心是 SQLite 标签记忆内核（五层类型 profile/fact/lesson/rules/project × active/archived 生命周期 + 版本/审计/回滚），自带 CLI 对话工作台用于日常使用与机制验证，并通过 MCP 对外提供完整工具面；对话能力经多端接入（CLI / DSH 插件，QQ-bot 规划中）复用同一内核。

> 项目早期为个人智能体原型（含自研 harness 与 Web 界面），现已转型为独立的记忆系统核心，移除 harness、会话层与界面层。

## 系统亮点

**核心亮点**

- **完整 mutation 契约**：所有破坏性操作走统一入口，返回 `version_id` / `audit_id` / `changed_rows`，变更前快照进版本表——删除永不丢历史，一切可回滚；审计事件全量留痕。
- **会话状态回指联想**：`session_state` 记录会话话题/决策/任务检查点，"那件事怎么样了"这类指代可经回指通道命中记忆。
- **全确定性检索**：FTS 词面（jieba 分词 + 同义词扩展 + CJK 兜底）+ 会话回指 + 时间衰减，零向量零模型依赖；检索结果可解释、可复现，部署轻量。
- **感知与通知体系**：巩固完成 / 写入确认 / 内核异常经 DSH 原生桌面通知（聚焦自动静默、点击唤起），触发点全覆盖且逐项可在设置页开关（[设计文档](docs/notification-design.md)）。

**能力清单**

- 模型自主写入（function calling，无触发词表）
- profile/rules 常驻注入（启动全量）
- 五层类型 × active/archived 生命周期
- consolidate 空闲自动巩固（低风险自愈留痕 + 高风险走审批）
- 使用强化（检索命中 touch，维护按访问热度排序）
- 上下文压缩后高分记忆重注入
- 事件时间字段（event_date / recorded_date 全链路携带）
- 14 个 MCP 工具 + CLI 全套管理命令

**设计取舍（明确不做）**

- 向量/BM25 混合检索：与确定性检索路线冲突，已定量论证为联想的错配工具（[论证](docs/associative-recall-architecture.md)），不引入
- 实体图谱：个人记忆量级下收益未证实，以 event_date 字段化元数据探路

演进待办见 [roadmap](docs/roadmap.md)。

## 文档

- [架构总览](docs/architecture.md) — 分层、模块职责、不变量
- [数据流](docs/data-flow.md) — 检索 / 写入 / 维护 / 启动链路
- [CLI 使用指南](docs/chat-guide.md) — 命令参考与典型流程
- [感知与通知体系](docs/notification-design.md) — 触发点矩阵与系统通知设计
- [演进路线](docs/roadmap.md) — 独立演进待办
- [多端接入演进](docs/dsh-plugin-evolution.md) — 路线图：DSH 插件已落地，QQ-bot 规划中

## 结构

- `memory_agent/`：记忆系统核心（自包含，零外部项目依赖）
  - `db.py`：SQLite 存储、mutation 入口（版本/审计/回滚）、FTS 索引同步
  - `retrieval.py`：确定性检索（FTS 词面 + 会话状态联想 + 时间衰减；排序不按重要程度标签，常驻性由 profile/rules 全量注入承担）
  - `llm.py`：LLM 调用封装（Anthropic/OpenAI 兼容，支持多轮与工具调用）
  - `query_builder.py`：查询扩展
  - `chat.py`：CLI 对话工作台（`python -m memory_agent.chat`）
  - `mcp_server.py`：MCP 工具服务器（`python -m memory_agent.mcp_server`）
- `dsh-iwiw-memory/`：DSH 接入端（插件）——让任意 DSH agent 获得跨会话记忆，见[插件 README](dsh-iwiw-memory/README.md)
- `data/`：数据目录（`memory.db` 单一真源；`legacy/` 历史资料；`logs/`）
- `scripts/`：开发与部署辅助脚本
- `tests/`：核心机制评估

## 快速开始

1. 复制 `.env.example` 为 `.env`，填写 `MEMORY_AGENT_LLM_API_KEY`。
2. 安装依赖：

   ```powershell
   pip install -r requirements.txt
   ```

3. 启动 CLI 工作台：

   ```powershell
   python -m memory_agent.chat
   ```

## 接入层（多端复用）

记忆内核是共享能力，经不同接入端进入对话场景：

| 接入端 | 状态 | 说明 |
|---|---|---|
| CLI 工作台（`memory_agent/chat.py`） | ✅ 可用 | 内置对话界面，日常使用与机制验证 |
| DSH 插件（`dsh-iwiw-memory/`） | ✅ 可用 | 让任意 DSH agent 获得跨会话记忆，安装与配置见[插件 README](dsh-iwiw-memory/README.md) |
| QQ-bot | 🚧 规划中 | 多端复用方案见[演进文档](docs/dsh-plugin-evolution.md) |

## 记忆分类与生命周期

记忆条目按两个维度组织（分类体系 v3，废除重要程度分级，全标签化）：

**类型（mem_type，五层）**：

| 值 | 语义 | 注入策略 |
|---|---|---|
| `profile` | 用户身份画像、健康、偏好 | 常驻：启动全量注入 |
| `rules` | 用户要求持续遵守的准则 | 常驻：启动全量注入 |
| `fact` | 一般事实 | 按话题检索召回 |
| `lesson` | 教训与经验 | 按话题检索召回 |
| `project` | 项目脉络与决策 | 按话题检索召回 |

**生命周期（priority，二态）**：

| 值 | 语义 | 策略 |
|---|---|---|
| `active` | 在役 | 参与检索与注入 |
| `archived` | 归档留痕 | 不参与常规检索与维护候选，可回滚 |

> 分类体系 v3 全标签化（废除重要程度分级）；旧值映射由 `db.py` v3 幂等迁移负责。

## 记忆系统不变量

1. 长期事实记忆以 `data/memory.db` 的 SQLite 为唯一真源；不再有 Markdown 真源或导出缓存。
2. 写入、编辑、合并、归档、删除、回滚必须走统一 mutation 入口，保存变更前版本并写入审计。
3. 破坏性操作返回 `version_id`、`audit_id`、`changed_rows`；`changed_rows == 0` 不能当成功。
4. 删除不能导致历史版本丢失（`memory_versions` 独立保留，可回滚恢复）。
5. 内容变更后必须刷新 FTS 索引（`db.py` 的 FTS 触发器保证）。
6. 更新采用全文替换语义（非追加），避免正文无限膨胀；变更前内容进版本表。
7. 检索必须进入真实对话上下文链路（chat 每轮注入 + 常驻层启动注入）。

## CLI 工作台命令

| 命令 | 说明 |
|---|---|
| 普通输入 | 对话（启动注入常驻层 profile/rules 记忆，每轮话题检索注入相关记忆；模型可自主调用记忆工具写入） |
| `/mem list [priority]` | 列出记忆 |
| `/mem search <q>` | 搜索记忆 |
| `/mem read <slug>` | 读取记忆正文 |
| `/mem edit <slug>` | 编辑记忆（多行，`__END__` 结束） |
| `/mem archive|delete|merge|history|rollback` | 归档/删除/合并/历史/回滚 |
| `/pending [status]` `/pending approve|reject <id>` | 待确认维护动作审批 |
| `/maintain` | 审查记忆维护候选（生成归档待确认动作） |
| `/stats` | 记忆库统计 |
| `/quit` | 退出 |

## MCP 工具面（DSH 接入桥）

`mcp_server.py` 提供 14 个工具：search_memories / list_memories / read_memory / memory_stats / memory_update / memory_archive / memory_delete / memory_merge / memory_history / memory_rollback / pending_actions / pending_approve / pending_reject / maintenance_review。

记忆写入由模型在对话中自主调用记忆工具完成（CLI chat 注册 memory_remember / memory_search / memory_read / memory_list 四个 function calling 工具），不再使用独立提取管线。

DSH 插件（`dsh-iwiw-memory/`）已落地：插件注册记忆工具、注入上下文并调度空闲巩固，宿主侧工具经 `memory_agent/mcp_server.py` 与 CLI 同源。

## 开发

- 核心机制评估：`python tests/memory_system_eval.py`
- 联想质量评估：`python tests/associative_recall_eval.py`
- 万字长对话评估：`python tests/long_conversation_eval.py`
- 检索质量手测：chat 内 `/mem search`，或直接调用 `search_memories`
