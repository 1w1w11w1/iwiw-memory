# @iwiw/dsh-iwiw-memory

IwIw 记忆插件：iwiw-memory 记忆内核的 DSH 接入端。让 DSH agent 拥有跨会话记忆、自动学习、自动反省能力——内核（Python）经 MCP 子进程挂载，本插件（TypeScript）负责工具注册、上下文注入与设置页。

## 能力

- **4 个记忆工具**：memory_remember / memory_search / memory_read / memory_list，模型自主调用写入与检索（与内核 CLI 同源）
- **常驻注入**：core 层画像 + rules 准则段每轮注入 system prompt，写入后自动刷新
- **命中注入**：每条用户消息触发检索，命中记忆以快照消息插入上下文（会话内去重、压缩后自动补回）
- **reflect steering**：连续多步未写入记忆时注入一次性回顾提示
- **记忆巩固**：空闲期自动调内核巩固记忆（峰时抑制），结果下次对话一次性告知
- **设置页**：行为参数在 DSH 设置页可调，大部分热生效

## 前置要求

1. DSH 桌面版（desktop profile）——插件走 desktop bundle 契约加载
2. Python 3.10+ 与本仓库（记忆内核 `memory_agent/` 就在本仓库根目录）：

   ```powershell
   git clone https://github.com/1w1w11w1/iwiw-memory.git
   cd iwiw-memory
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

## 安装（desktop profile）

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

> desktop bundle 契约（缺一即启动崩溃）：包内 `package.json` 声明 `dsh.bundle.patch` 指向 `cordis.patch.yml`；包内 patch 用 `- insert:` 行装载插件（name 必须是 npm 包名 `@iwiw/dsh-iwiw-memory`）；profile patch 的 id 与 insert 行 id 一致。写入 profile 配置文件须无 BOM（UTF-8 无 BOM，否则 DSH 解析失败）。

重启 DSH 生效。启动日志出现 `applied: 4 tools + 2 prompt sections + pre-step hook + consolidation scheduler` 即挂载成功。

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
| `consolidateIdleMinutes` | 180 | 空闲巩固阈值（分钟），0=关闭 |
| `standingLayers` | profile,rules | 常驻记忆类型，逗号分隔 |
| `notifyRemember` | false | 记忆写入系统通知（60 秒窗口合并） |
| `notifyConsolidate` | true | 空闲巩固完成系统通知（聚焦时自动静默） |
| `notifyOnFailure` | true | 巩固失败 / 记忆内核连接异常系统通知 |

## 开发

```powershell
cd dsh-iwiw-memory
npm install
npm run build        # tsc（host 端）+ esbuild（client 端）
node scripts/host-smoke.mjs   # host 级冒烟（SMOKE_PYTHON / SMOKE_CWD 可覆盖部署路径）
```
