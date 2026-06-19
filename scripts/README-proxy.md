# 代理修复开机脚本

## 文件说明

| 文件 | 作用 |
|------|------|
| `fix-proxy.ps1` | 核心脚本：检测代理 → 不可达则清除 |
| `install-fix-proxy-task.ps1` | 安装为 Windows 开机任务 |

## 部署

在 **普通 PowerShell**（无需管理员，但有时需要）执行：

```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser   # 首次允许执行脚本
.\install-fix-proxy-task.ps1
```

安装后下次重启自动生效。也可以手动立即测试：

```powershell
Start-ScheduledTask -TaskName 'IwIwFixProxy'
```

## 日志

位置：`fix-proxy.log`（与脚本同目录）
每次运行追加一行结果，方便排查。

## 卸载

```powershell
Unregister-ScheduledTask -TaskName 'IwIwFixProxy' -Confirm:$false
```
