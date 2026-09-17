# @iwiw/dsh-iwiw-memory

IwIw 记忆插件：iwiw-memory 记忆内核的 DSH 接入端。让 DSH agent 拥有跨会话记忆、自动学习、自动反省能力——内核（Python）经 MCP 子进程挂载，本插件（TypeScript）负责工具注册、上下文注入与设置页。

## 能力

- **4 个记忆工具**：memory_remember / memory_search / memory_read / memory_list，模型自主调用写入与检索（与内核 CLI 同源）
- **常驻注入**：常驻层记忆（`standingLayers`，默认 `profile,rules`）每轮全量注入 system prompt，写入后自动刷新；注入视图剥离只对检索有意义的 `关键词:` 行（正文不动）
- **命中注入**：每条用户消息触发检索，命中记忆以快照消息插入上下文（会话内去重、压缩后自动补回）
- **reflect steering**：连续多步未写入记忆时注入一次性回顾提示
- **启动补账**：每次插件启动后台跑一次巩固——检查上次水位之后新完成的对话，整理已有记忆；结果下次对话一次性告知。与聊天活跃度无关，没有峰时抑制
- **设置页**：行为参数在 DSH 设置页可调，大部分热生效（启动补账下次启动生效）

## 前置要求

1. DSH（任意 profile）——插件走 profile bundle 契约加载。本机实际挂载在 `web` profile
   （`~/.dsh/profiles/web`）；`scripts/boot-sim.mjs` 会自动发现挂载了本插件的 profile，
   不写死名称或安装路径
2. Python 3.10+ 与本仓库（记忆内核 `memory_agent/` 就在本仓库根目录）：

   ```powershell
   git clone https://github.com/1w1w11w1/iwiw-memory.git
   cd iwiw-memory
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

## 安装（profile）

插件包位于本仓库 `dsh-iwiw-memory/` 子目录，`lib/` 为预构建产物，无需本地编译。

**1. junction 到 profile 的 node_modules：**

```powershell
New-Item -ItemType Junction -Path "<profile>\node_modules\@iwiw\dsh-iwiw-memory" -Target "<仓库根>\dsh-iwiw-memory"
```

**2. profile 的 `cordis.patch.yml` 部署配置**（`python` 缺省取 PATH 上的 python，`cwd` 必填且必须指向仓库根）：

```yaml
- id: '@iwiw/dsh-iwiw-memory'
  config:
    enabled: true
    python: '<仓库根>/.venv/Scripts/python.exe'
    cwd: '<仓库根>'
```

> profile bundle 契约（缺一即启动崩溃）：包内 `package.json` 声明 `dsh.bundle.patch` 指向 `cordis.patch.yml`；包内 patch 用 `- insert:` 行装载插件（name 必须是 npm 包名 `@iwiw/dsh-iwiw-memory`）；profile patch 的 id 与 insert 行 id 一致。写入 profile 配置文件须无 BOM（UTF-8 无 BOM，否则 DSH 解析失败）。

重启 DSH 生效。启动日志出现 `[dsh-iwiw-memory] applied: 4 tools + 3 prompt sections + pre-step hook (reflect/hit) + startup reconcile` 即挂载成功（随后一行 `capture commands registered (/memo, /recall, /iwiw-prompt)`）。

## 配置

**部署级**（仅 profile patch config，不进设置页）：

| 字段 | 默认 | 说明 |
|---|---|---|
| `python` | PATH 上的 `python` | Python 解释器路径（含 mcp/jieba 依赖的 venv 或系统 Python） |
| `cwd` | 必填 | 仓库根目录（内核以 `python -m memory_agent.mcp_server` 在此目录拉起） |
| `env` | — | 覆盖 MCP 子进程环境变量（如 `MEMORY_AGENT_DB_PATH` 指向隔离库） |

**行为级**（设置页可调，patch config 作初始值）：

| 字段 | 默认 | 说明 |
|---|---|---|
| `hitTopK` | 3 | 每条消息命中注入条数上限 |
| `coreMaxChars` | 2500 | 常驻记忆段字符预算 |
| `reflectTurns` | 7 | 回顾提示触发步数，0=关闭 |
| `startupConsolidate` | true | 启动时自动补账（替代旧的空闲轮询），下次启动生效 |
| `standingLayers` | profile,rules | 常驻记忆类型，逗号分隔 |

```
consolidateIdleMinutes（已弃用）
```

旧字段只做兼容读取：显式设为 0 会映射为 `startupConsolidate=false`，正数或缺省映射为
启用（阈值本身不再生效，巩固改为每次启动一次）。不再在设置页展示。

### 已知边界

- 水位是两个独立边界：`lastRun`（已检查的会话事件）与 `lastConsolidatedAt`（已整理的记忆）。
  失败、取消或无法确认读取完整性时不推进；下次启动按旧水位重试，因此后端写入必须可安全重放。
- 增量判定依赖日志时间与本机时间可比。检测到时钟回拨时返回 `clock-regressed` 并**不降低水位**，
  也不假装恢复完成；校时后需要显式重扫（本版不承诺仅凭时间自动无损恢复）。
- 列目录、stat、读取水位这些元数据操作不可能为零；"第二次启动零成本"指的是无新增完成事件时
  零 LLM 调用、零记忆 mutation、且未变化的 cold 会话零完整日志读取。
- 长驻、永不重启的进程不会自动周期巩固；需要时手动调用 MCP 的 `run_consolidate`。

## 开发

```powershell
cd dsh-iwiw-memory
npm install
npm run build        # tsc（host 端）+ esbuild（client 端）
node scripts/host-smoke.mjs   # host 级冒烟（SMOKE_PYTHON / SMOKE_CWD 可覆盖部署路径）
```
