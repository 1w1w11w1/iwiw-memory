<#
.SYNOPSIS
    将 fix-proxy.ps1 注册为开机自启任务（Windows 任务计划程序）
.DESCRIPTION
    以当前用户身份创建开机触发任务，静默运行（无窗口弹出）。
    如需卸载：在管理员 PowerShell 执行
      Unregister-ScheduledTask -TaskName 'IwIwFixProxy' -Confirm:$false
#>

$scriptPath = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'fix-proxy.ps1'
$taskName   = 'IwIwFixProxy'
$taskDesc   = '重启后检测并修复系统代理残留异常'

# 获取当前用户 SID（用于「只在用户登录时运行」）
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

# 构建执行命令：PowerShell 无窗口执行脚本
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""

# 开机触发 + 延时 10 秒，等网卡就绪
$trigger = New-ScheduledTaskTrigger -AtStartup -RandomDelay (New-TimeSpan -Seconds 10)

# 以当前用户身份运行，不存储密码（仅登录后生效）
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

# 可选：即使离线也触发（默认 false）
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

try {
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description $taskDesc `
        -Force

    Write-Host "✅ 已创建开机任务「$taskName」" -ForegroundColor Green
    Write-Host "   → 脚本路径: $scriptPath"
    Write-Host "   → 每次开机登录后自动运行，静默修复代理残留。"
    Write-Host ""
    Write-Host "手动测试任务（立即运行一次）："
    Write-Host "  Start-ScheduledTask -TaskName '$taskName'"
    Write-Host ""
    Write-Host "查看运行日志："
    Write-Host "  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
    Write-Host "  日志文件: $scriptPath\..\fix-proxy.log"
} catch {
    Write-Host "❌ 注册失败: $_" -ForegroundColor Red
    Write-Host "是否以管理员身份运行？部分系统需要管理员权限注册开机任务。"
}
