# Hindsight 插件缺陷评审（对本项目的启示）

> 评审对象：DSH 内置记忆插件 @vectorize-io/hindsight-coding-agents（daemon 模式，本地服务 127.0.0.1:9077，LLM 走火山方舟 Agent Plan）。
> 方法：2026-09-04 实测——读取服务日志、进程树、uv 环境、源码（hindsight-embed / hindsight-api / litellm），并做直连对照实验。
> 结论先行：Hindsight 的**架构骨架（bank/分级/幂等/审计）值得借鉴，但进程生命周期、LLM 集成、健康可观测三块缺陷严重**，直接导致"服务启动后会无日志消失、活着也不干活"。本文档按缺陷级别整理，每条附「对本项目启示」。

---

## P0 —— 进程生命周期（致命）

### D1 服务进程绑定临时缓存环境，环境被回收即无日志死亡
- **现象**：服务进程（PID 27652）命令行指向 `uv\\cache\\archive-v0\\<随机hash>\\Scripts\\hindsight-api.exe`。服务多次启动（12:00/12:04/12:25/13:28/13:36），多数在数分钟后**戛然而止**：无 Python traceback、无 Windows 崩溃事件（Application Error / WER 全空）。
- **根因**：`uvx hindsight-embed@latest` 把解释器与全部依赖解到 uv 缓存目录（每次运行还换新 hash 目录）。服务虽以 DETACHED_PROCESS 脱离终端，但运行中的进程在需要加载被回收目录里的新模块时即崩溃，且因解释器/日志链已坏而留不下任何痕迹。
- **对本项目启示**：
  - **永远不要从临时/缓存环境启动常驻服务**。memory-agent 的 MCP server 若被 DSH 以 stdio 拉起，进程解释器是系统 python（固定），无此问题；但若未来做独立 daemon，必须用固定 venv（`uv tool install` / 项目内 .venv），并拒绝"从缓存跑"。
  - **崩溃现场必须可留存**：独立进程的 stderr 永远重定向到文件（本项目 mcp_server 被拉起时 stdout 被 MCP 协议占用，stderr 必须单独落盘）。

### D2 重复启动互相杀：端口回收无 ownership 区分
- **现象**：日志显示 5 次启动、多次短命。`daemon start` 内部 `_clear_port` 会回收 9077 上的旧监听者——手动启动与插件自动拉起并存时，后启动者杀掉先启动者。
- **根因**：单例守护只有"端口占用"这一弱信号，缺进程身份/锁的强所有权语义（源码里有按命令行 marker 匹配的逻辑，但回收路径仍按端口判定）。
- **对本项目启示**：
  - 单实例是**硬需求**：MCP 模式下多个 DSH 会话可能并发拉起多个 `mcp_server` 进程，同时写同一个 `data/memory.db` ——这是本项目**当前就存在的竞态**（SQLite WAL 只保证单进程内安全，多进程写需要 busy_timeout + 应用层串行化，Hindsight 教训是"进程级锁 + 后到者退让"）。
  - 方案：启动时抢锁（`msvcrt.locking` 或 `filelock`），抢不到则退出并提示已有实例；或接受多进程只读、写路径集中到单 writer。

### D3 服务死后无独立看护，依赖业务触发的 ensure
- **现象**：服务在 12:41 死亡后一直没人拉起，直到 DSH 会话启动（SessionStart/retain 钩子）才 `ensureDaemon`。没有常驻 supervisor。
- **对本项目启示**：MCP 进程由客户端生命周期管理（客户端退出进程即死），这**本身可接受**（随用随起）；但记忆写回若想异步/持久，就要有独立于会话的守护与重试队列。Hindsight 的教训：**不要用"业务触发"当"看护触发"**——要么接受随用随起，要么上计划任务/systemd 常驻。

---

## P1 —— LLM 集成（核心功能受损）

### D4 同一端点直连 1.1s，Hindsight 调用 20–200s 且超时
- **现象**：对照实验——直连 `ark.cn-beijing.volces.com/api/plan/v3`（同 key 同模型）返回 1.1s；Hindsight 经 litellm 调同一模型 `slow llm call: time=200.554s`、`APITimeoutError: Request timed out`（默认超时 120s，重试 4 次），fact extraction / consolidation 大面积失败。
- **根因**：litellm 的 volcengine 适配器对 Agent Plan 端点兼容性差（`thinking` 等参数、URL 处理），请求在服务端拖到超时。
- **对本项目启示**：
  - 自研 LLM 层（`llm.py`）用**标准 OpenAI 兼容协议 + 显式参数白名单**，是正确路线；引入 litellm 之类适配层时，必须对新端点做**真实调用冒烟**（本项目 requirements 只有 60 字节，说明 LLM 层是自己写的——保持）。
  - 每类 provider 配置要带"直连基准耗时"验证脚本，把"适配层问题"与"上游慢"区分开。

### D5 提取强依赖 LLM 成功，无规则兜底
- **现象**：`RuntimeError: Fact extraction failed: 1/1 chunks failed ... APITimeoutError`，整批任务失败并重试（attempt 1/4→4/4），无降级路径。
- **对本项目启示**：本项目已有 `triggers.py`（纯规则）+ `extractor.py`（LLM）双通道，方向正确。补一条：**LLM 提取失败时，降级为规则提取或"保持观察不写"**（与不变量 9 一致），且失败计数要可观测，不能无限重试。

### D6 慢调用阻塞事件循环，健康检查被误判
- **现象**：Hindsight 源码注释自认 "the daemon serves /health on the same asyncio event loop that runs LLM calls"，慢调用（200s）会 stall 健康探针。
- **对本项目启示**：慢 IO（LLM、嵌入）必须与主服务 loop/线程分离；健康探针只反映"进程活着 + 存储可读"，LLM/嵌入可用性单独上报（见 D10）。本项目是同步 python，天然串行——多路并发请求时一个慢 LLM 会卡住其他工具调用，**需要线程池隔离**（`concurrent.futures` 包 LLM 调用）。

---

## P2 —— 任务与可观测（运维隐患）

### D7 任务堆积、STUCK、重试无上限可见性、payload 丢失
- **现象**：`[STUCK?] op=... age=691s`、`payload_null=2`（消费者重启后负载丢失）、`attempt=1/4` 重试无进度。
- **对本项目启示**：若做异步写回/维护队列：任务表带 `op_id` + `age` + `attempt` + `state`，超龄回收；**负载随任务持久化**（不要只存队列引用）；重试要指数退避且封顶。

### D8 内嵌 PostgreSQL（pg0）生命周期绑定进程
- **现象**：服务内嵌 PG（`pg0://hindsight-embed-coding-agent`），首次 initdb 慢（启动 75s 中有相当部分耗于此），DB 随服务进程生死，另见 `slow DB pool acquire: waited 1.0–1.3s`。
- **对本项目启示**：本项目用 SQLite 单一文件真源 + WAL，**正确避开了这个坑**。保持：存储与进程解耦、单文件可备份、无独立 DB 服务依赖。

### D9 配置三处分散 + 诊断日志路径在 Windows 无效
- **现象**：配置散在 `coding-agent.json`（serverMode）、`coding-agent.env`（LLM/端口）、`metadata.json`；诊断日志写 `/tmp/hindsight-plugin.log`，Windows 下路径无效、写入失败被 `catch` 静默吞掉，排障无日志可用。
- **对本项目启示**：
  - 配置**单一真源**（本项目 `config.py` 环境变量覆盖，好），所有派生值从一处解析。
  - 日志路径跨平台必须正确（`pathlib` 拼 `%TEMP%` 而非硬编码 `/tmp`）；**日志写入失败要可见**（本项目 stderr 落盘时同样要处理"落盘失败"本身）。

### D10 依赖健康无统一消费面
- **现象**：`/version` 的 features 里 `bank_llm_health: false`——LLM 不健康状态其实已上报，但无人消费，服务照常"活着"，用户无感知。
- **对本项目启示**：定义统一健康面：`storage / llm / embedding / fts` 各自 ok/deg/fail；检索与写入在 deg 时降级（嵌入失败→FTS，已有）、在 fail 时报错而非静默。让"活着但不可用"可被探测。

---

## P3 —— 配置校验

### D11 未知策略仅告警
- **现象**：`WARNING - Unknown retain strategy 'conversation', using resolved config as-is`——配置拼错只告警不报错，错误配置静默生效。
- **对本项目启示**：配置/策略名校验 fail-fast（本项目 mutation 类型、分级值应枚举校验），未知值在启动时报错并拒绝启动，而不是运行期静默降级。

---

## ✅ 值得借鉴的正面设计

1. **写路径幂等**：retain 带 `operation_id`，服务端按 id 去重（`MIN_IDEMPOTENT_RETAIN_VERSION` 探测能力后启用）——对应本项目 `audit_id`/版本回滚，但可再加"按业务幂等键去重"。
2. **变更前快照**：consolidation 保留旧 chunk（"RECOVERY: found N already-committed chunks — preserving existing data"），与本项目 `memory_versions` 同思想。
3. **冷启动后台化**：daemon 首次启动在后台完成、会话不阻塞——对应本项目"首次嵌入下载 BGE 后台化"，已做（懒加载）。
4. **银行/租户隔离**（bank 按工作区路由）——本项目可预留 `workspace_id` 维度，避免多项目记忆混写。

---

## 给本项目的落地清单（按优先级）

1. **MCP 多进程单例锁**（对应 D2）：mcp_server 启动抢 `data/memory.lock`，抢不到直接退出——防多会话并发写 SQLite。
2. **LLM 调用隔离 + 可观测**（对应 D4/D6）：`llm.py` 加超时/重试/慢调用日志（>10s 记 WARN），线程池包裹；保留"直连基准测试"脚本。
3. **提取失败降级**（对应 D5）：LLM 提取失败 → 规则提取或观察不写 + 失败计数告警。
4. **健康面**（对应 D10）：`/health` 或 `memory_stats` 拆分 storage/llm/embedding 状态。
5. **stderr 落盘**（对应 D1）：mcp_server 被拉起时 stderr → `data/logs/`，路径用 `pathlib` 拼。
6. **任务队列化**（对应 D7，二期）：维护/写回异步化时带 op_id/attempt/持久负载。
