# DSH 插件安装指南（快速开始）

为 DSH agent 装上跨会话记忆的推荐方式：`dsh-iwiw-memory` 插件——记忆工具注册、常驻注入、空闲巩固、桌面通知全部由插件编排，记忆内核（Python）经 MCP 子进程挂载。

> 只要轻量的工具面（模型主动调用，无常驻注入/巩固/通知）？见 [DSH MCP 挂载指南](dsh-mcp-setup.md)。

## 前置要求

1. **DSH 桌面版**（desktop profile）——插件走 desktop bundle 契约加载
2. **Python 3.10+** 与记忆内核（本仓库）：

   ```powershell
   git clone https://github.com/1w1w11w1/iwiw-memory.git
   cd iwiw-memory
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

## 第 1 步：安装插件包到 profile

```powershell
npm install dsh-iwiw-memory --prefix "<DSH_HOME>\profiles\desktop"
```

- `<DSH_HOME>` 默认为 `~/.dsh`
- 开发模式（跟随本仓库改动）可用 junction 替代：

  ```powershell
  New-Item -ItemType Junction -Path "<DSH_HOME>\profiles\desktop\node_modules\@iwiw\dsh-iwiw-memory" -Target "<仓库根>\dsh-iwiw-memory"
  ```

## 第 2 步：部署配置（profile 的 cordis.patch.yml）

```yaml
- id: 'dsh-iwiw-memory'
  config:
    enabled: true
    python: '<仓库根>/.venv/Scripts/python.exe'
    cwd: '<仓库根>'
```

- `python`：含 mcp/jieba 依赖的解释器（缺省取 PATH 上的 python）
- `cwd`：必填，指向记忆内核（memory_agent）所在目录
- 写入 profile 配置文件须为 **UTF-8 无 BOM**，否则 DSH 解析失败

## 第 3 步：重启 DSH 并验证

启动日志出现以下行即挂载成功：

```
[dsh-iwiw-memory] applied: 4 tools + 2 prompt sections + pre-step hook (reflect/hit) + consolidation scheduler
```

验证清单：
- DSH 设置页出现「iwiw 记忆」标签页（注入/巩固/通知参数可调）
- 会话 A 中让模型记住一件事 → 会话 B 中可直接问出（跨会话记忆生效）

## 常用配置

在 DSH 设置页「iwiw 记忆」标签页调整（热生效）：

| 参数 | 默认 | 说明 |
|---|---|---|
| 命中注入条数上限 | 3 | 每条消息最多联想注入的记忆条数 |
| 常驻记忆段字符预算 | 2500 | profile/rules 注入的总字符上限 |
| 空闲巩固阈值（分钟） | 180 | 空闲多久后自动巩固记忆，0=关闭 |
| 记忆写入系统通知 | 关 | 写入确认桌面通知（60 秒合并） |
| 巩固完成系统通知 | 开 | 空闲巩固结束的桌面通知 |
| 后台异常系统通知 | 开 | 巩固失败 / 内核异常的桌面通知 |

完整参数与行为细节见[插件 README](https://github.com/1w1w11w1/iwiw-memory/tree/main/dsh-iwiw-memory)。
