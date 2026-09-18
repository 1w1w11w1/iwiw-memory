# iwiw-memory（iwiw 记忆）

一个**独立的本地长期记忆系统**：SQLite 单库事实记忆（类型 × 生命周期双轴，全程版本 / 审计 / 回滚）、
全确定性检索（FTS 词面 + 会话状态回指，零向量零模型依赖），自带 CLI 对话工作台，
并经 MCP 与 DSH 插件把记忆接进任意 agent 会话。

> 项目早期是含自研 harness 与 Web 界面的个人智能体原型；现在只保留记忆内核与接入端，
> harness / 会话层 / Web GUI 已移除，不再复活。

## 亮点

- **完整 mutation 契约**：写入、编辑、合并、归档、删除、回滚全部走统一入口，返回 `version_id` / `audit_id` / `changed_rows`；变更前快照进 `memory_versions`——删除永不丢历史，一切可回滚，事件全量进 `memory_audit`。
- **全确定性检索**：FTS5（jieba 分词 + 同义词扩展 + CJK 兜底）+ 会话状态回指（「那件事怎么样了」式指代）+ 时间衰减与优先级加权。可解释、可复现，无嵌入模型、无向量库。
- **常驻层是配置出来的**：哪些类型每轮全量注入由 `STANDING_LAYERS`（默认 `profile,rules`）决定，不写死在类型里；其余类型按话题检索召回。
- **写入闸门**：凭据类（API key / token / 密码 / 私钥）与公网 IP 在落库前由 `redaction.sanitize` 确定性脱敏（幂等），命中详情进审计；回环与内网地址保留。
- **模型自主写入**：无触发词表、无独立提取管线——模型在对话中自行调用 `memory_remember` / `memory_search` 等工具。
- **多端同源**：CLI 工作台与 DSH 插件共用同一内核、同一套工具定义（`memory_agent/model_tools.py` 单一真源）。

## 结构

- `memory_agent/`：记忆系统核心（自包含，零外部项目依赖）
  - `db.py`：SQLite 存储、mutation 入口（版本 / 审计 / 回滚）、FTS 索引同步
  - `retrieval.py`：确定性检索（FTS 词面 + 会话状态联想 + 时间衰减 + 优先级加权）
  - `query_builder.py`：查询扩展（同义词表 + jieba 分词 + CJK 兜底）
  - `session_state.py`：会话状态检查点（话题 / 决策 / 任务 → 关联记忆，支撑回指联想）
  - `model_tools.py`：模型记忆工具定义与执行（chat 与 MCP 同源）
  - `maintenance.py`：维护审查与巩固（低风险修正自动留痕执行，高风险走审批）
  - `redaction.py`：脱敏单源实现（写入闸门 + 调试展示共用）
  - `llm.py`：LLM 调用封装（Anthropic / OpenAI 兼容，支持多轮与工具调用）
  - `chat.py` / `mcp_server.py`：CLI 对话工作台 / MCP 工具服务器
  - `debug_bundle.py`：把 DSH 导出的会话包解析成逐轮事实链（注入 / 命中 / 工具调用 / 报错）
- `dsh-iwiw-memory/`：DSH 接入端（TypeScript 插件，经 MCP 子进程桥接内核）——见[插件 README](dsh-iwiw-memory/README.md)
- `data/`：数据目录（`memory.db` 单一真源；`logs/`）；`.venv/`、`data/`、`debug-inbox/` 均不入库
- `scripts/check_db_state.py`：只读检查真库 schema 版本与条目分布
- `tests/`：机制评估与冒烟（见[开发与验证](#开发与验证)）

## 快速开始

1. 复制 `.env.example` 为 `.env`，填 `MEMORY_AGENT_LLM_API_KEY`（默认走 DeepSeek 的 Anthropic 兼容端点）。
2. 安装依赖：

   ```powershell
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

3. 启动 CLI 工作台：

   ```powershell
   .venv\Scripts\python -m memory_agent.chat
   ```

## 记忆模型

记忆由两个正交的轴描述：**类型轴**（`mem_type`，记忆的内在属性）与**生命周期轴**（`priority`）。

| mem_type | 语义 | 默认加载 |
|---|---|---|
| `profile` | 身份画像（身份、健康、关系、长期偏好） | 常驻注入 |
| `fact` | 事实（日常信息、阶段计划） | 按需检索 |
| `lesson` | 经验教训（被纠正后的结论） | 按需检索 |
| `rules` | 准则（设计原则、行为准则） | 常驻注入 |
| `project` | 项目（目标、结构、决策、进度） | 按需检索，**按项目隔离** |

| priority | 语义 |
|---|---|
| `active` | 参与常规检索与维护候选 |
| `archived` | 归档状态，不参与常规检索（保留版本，可回滚） |

> 常驻层由 `MEMORY_AGENT_STANDING_LAYERS`（默认 `profile,rules`；dev 模式建议仅 `rules`）决定；
> 插件侧由设置页的「常驻记忆类型」同源控制。旧的三值分级（core / normal / archive）已废除：
> 重要程度不再是分级依据，常驻性改由模式配置承担。

> **项目隔离**：仅 `project` 型按项目过滤（`profile`/`rules` 是跨项目的工作方式，
> `fact`/`lesson` 天然跨项目，均不隔离）。项目标识取会话 cwd 的末段目录名
> （如 `E:\desktop\111` → `E-desktop-111`），插件侧可用 `projectTag` 配置整体覆盖。
> 标签语义：为空 = 未知（保守放行）、`全局` = 显式跨项目（放行）、等于当前项目 = 可见。
> 写入 `project` 型记忆时自动落标签；**不打标等于该条对所有项目可见**。

## CLI 工作台

```powershell
python -m memory_agent.chat
```

普通输入即对话：启动注入常驻层记忆全文，每轮按话题检索注入相关记忆（会话内去重、滑出上下文窗口后允许重新联想）；
模型可在回复前自主调用记忆工具。会话上下文只保存在内存（最近 N 轮），退出即清空——持久化会话交给外部 harness。
`MEMORY_AGENT_ECHO_STATE=1` 可回显联想注入 / 记忆工具等内部状态（E2E 以此作确定性锚点）。

| 命令 | 说明 |
|---|---|
| `/mem list [priority]` | 列出记忆（可按 `active`/`archived` 过滤） |
| `/mem search <q>` | 搜索记忆（词面 + 同义词 + 会话状态联想） |
| `/mem read <slug>` | 读取记忆正文 |
| `/mem edit <slug>` | 编辑记忆（多行输入，单独一行 `__END__` 结束） |
| `/mem archive\|delete\|merge\|history\|rollback` | 归档 / 删除（需确认 y）/ 合并 / 版本历史 / 回滚到版本 |
| `/maintain` | 审查维护候选（访问最少、更新最早的 active）→ 生成归档待确认动作 |
| `/consolidate` | 巩固：审查近期记忆，低风险修正自动留痕执行，归档走审批、合并仅建议 |
| `/pending [status]`、`/pending approve\|reject <id>` | 待确认动作的查看与审批 |
| `/stats`、`/help`、`/quit` | 记忆库统计 / 帮助 / 退出 |

## MCP 工具面

```powershell
python -m memory_agent.mcp_server      # 等价于 python -m memory_agent（默认入口即 MCP）
```

以 stdio 提供 17 个工具：

| 分组 | 工具 |
|---|---|
| 写入 | `memory_remember`（`level` 选类型，`slug` 命中即全文替换） |
| 检索 | `search_memories`（可带 `session_id` / `context` 启用回指，`exclude_mem_types` 排除常驻层防重复）、`list_memories`、`read_memory`、`memory_stats`、`touch_memories`（命中自增，供维护排序） |
| 变更 | `memory_update`、`memory_archive`、`memory_delete`、`memory_merge`、`memory_history`、`memory_rollback` |
| 维护 | `maintenance_review`、`pending_actions`、`pending_approve`、`pending_reject`、`run_consolidate`（支持 `since_ms`/`until_ms` 窗口分批） |

## DSH 接入（插件）

`dsh-iwiw-memory/` 让任意 DSH agent 获得跨会话记忆：4 个记忆工具 + 常驻段注入 + 每轮命中注入 +
reflect 回顾提示 + 启动补账 + 设置页（行为参数热生效）。内核以 MCP 子进程挂载，插件包随仓库分发（未发布 npm）。

安装、profile patch 契约、配置字段与已知边界见[插件 README](dsh-iwiw-memory/README.md)；
多端路线图见[演进文档](docs/dsh-plugin-evolution.md)。

## 调试闭环（跨项目排查记忆问题）

1. 在出问题的 DSH 会话里用头部导出按钮或 `/export` 导出整会话 ZIP；
2. 放进 `debug-inbox/`（已 gitignore，含会话明文，不可入库）；
3. 解析成逐轮事实链（注入 / 命中 / `memory_*` 调用与结果 / 报错；默认脱敏，`--raw` 保真）：

   ```powershell
   python -m memory_agent.debug_bundle            # 取 inbox 最新的包
   python -m memory_agent.debug_bundle <包.zip> --out 报告.md
   ```

插件侧另有 `/memo <想法>`（别的项目会话里落现场到 inbox）、`/recall`（本仓库会话里回灌最新现场）、
`/iwiw-prompt`（回显当前注入的 system prompt 段）三个开发者工具。

## 开发与验证

| 命令 | 作用 |
|---|---|
| `python tests/memory_system_eval.py` | 核心机制断言集（替换语义、mutation 契约、CJK 兜底） |
| `python tests/associative_recall_eval.py` | 联想质量离线评估（直接 / 语义 / 主题回指 / 反例） |
| `python tests/long_conversation_eval.py` | 万字级长对话注入质量（命中率 / 预算 / 长程联想） |
| `python tests/e2e_chat_eval.py` | 真实 chat 管道 E2E（子进程驱动，硬判定 + 软审阅，消耗 LLM 额度） |
| `python tests/smoke_*.py` | MCP A/B 面、reflect/consolidate、使用强化的本地冒烟（临时库） |
| `python scripts/check_db_state.py` | 只读查看真库 schema 版本与分布 |

引入依赖须同步 `requirements.txt`；跨模块改动先做 tracer bullet（真实入口到真实输出的最小闭环）；
文档状态不算完成，必须附命令 / 测试 / 真实链路 smoke。

## 不变量

1. 长期事实记忆以 `data/memory.db` 的 SQLite 为唯一真源，没有 Markdown 真源或导出缓存。
2. 写入、编辑、合并、归档、删除、回滚必须走统一 mutation 入口，保存变更前版本并写入审计。
3. 破坏性操作返回 `version_id`、`audit_id`、`changed_rows`；`changed_rows == 0` 不能当成功。
4. 删除不能导致历史版本丢失（`memory_versions` 独立保留，可回滚恢复）。
5. 内容变更后必须刷新 FTS 索引（`db.py` 的 FTS 触发器保证）。
6. 更新采用全文替换语义（非追加），避免正文无限膨胀。
7. 检索必须进入真实对话上下文链路（chat 每轮注入 + 常驻层启动注入）。
8. 脱敏必须幂等，且只能走统一 mutation 入口，不在调用方各写一份。

## 文档

- [架构总览](docs/architecture.md) — 分层、模块职责、不变量
- [数据流](docs/data-flow.md) — 检索 / 写入 / 维护 / 启动四条链路
- [CLI 使用指南](docs/chat-guide.md) — 命令参考与典型流程
- [文档索引](docs/README.md) — 全部设计与评估文档
