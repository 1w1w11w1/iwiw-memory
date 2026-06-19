<#
.SYNOPSIS
    开机自启代理修复脚本 — 检测系统代理是否配置异常，若代理不可达则自动清理。
.DESCRIPTION
    Windows 重启时若 VPN / 代理软件未提前退出，代理注册表残留可能导致
    「代理设置异常 → 浏览器/终端无法联网」。本脚本在开机时运行：
      1. 检查系统代理是否开启
      2. 若开启，尝试通过该代理访问两个已知站点
      3. 若全部失败 → 关闭代理并写日志
      4. 若至少一次成功 → 认为代理正常，不做任何修改
.NOTES
    部署方式（二选一）：
      A) 任务计划程序（推荐）— 开机触发
      B) 放入 shell:startup 启动文件夹（可能弹窗）
#>

$ErrorActionPreference = 'SilentlyContinue'

# — 日志路径（放在当前脚本同目录下，方便查找）
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $scriptDir -or $scriptDir -eq '') { $scriptDir = $env:USERPROFILE }
$logFile = Join-Path $scriptDir 'fix-proxy.log'

# 日志辅助
function Write-Log {
    param([string]$Msg)
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$timestamp] $Msg"
    Write-Host $line
    Add-Content -Path $logFile -Value $line
}

Write-Log '=== fix-proxy 启动 ==='

# — 1. 读取当前代理设置 —
$regPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
$proxyEnabled = (Get-ItemProperty -Path $regPath -Name 'ProxyEnable' -ErrorAction SilentlyContinue).ProxyEnable
$proxyServer  = (Get-ItemProperty -Path $regPath -Name 'ProxyServer'  -ErrorAction SilentlyContinue).ProxyServer

if (-not $proxyEnabled -or $proxyEnabled -eq 0) {
    Write-Log '系统代理未开启，无需处理。'
    # 顺便确认直连是否正常，仅做记录
    $directOk = Test-Connection -ComputerName '8.8.8.8' -Count 1 -Quiet
    Write-Log "直连 DNS(8.8.8.8) 可达 = $directOk"
    Write-Log '=== fix-proxy 结束（无需修改）==='
    exit 0
}

Write-Log "发现系统代理已开启 → ProxyServer = 「$proxyServer」"

# — 2. 测试代理是否可用 —
# 用 .NET 的 WebClient 走代理测试两个站，超时 5 秒
function Test-ProxyConnectivity {
    param([string]$Url)
    try {
        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
        $wc.Proxy = [System.Net.WebRequest]::GetSystemWebProxy()
        $wc.Proxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials
        $wc.DownloadString($Url) | Out-Null
        return $true
    } catch {
        return $false
    }
}

$testUrls = @('https://www.baidu.com', 'https://www.bing.com')
$successCount = 0

foreach ($url in $testUrls) {
    $ok = Test-ProxyConnectivity -Url $url
    if ($ok) {
        Write-Log "  代理测试 OK → $url"
        $successCount++
    } else {
        Write-Log "  代理测试 失败 → $url"
    }
}

# — 3. 判定 —
if ($successCount -ge 1) {
    Write-Log "代理可用（$successCount/$($testUrls.Count) 成功），无需修改。"
    Write-Log '=== fix-proxy 结束（无需修改）==='
    exit 0
}

# 全部失败 → 关闭代理
Write-Log '所有代理测试失败，正在关闭系统代理……'

Set-ItemProperty -Path $regPath -Name 'ProxyEnable' -Value 0
Set-ItemProperty -Path $regPath -Name 'ProxyServer' -Value '' -ErrorAction SilentlyContinue

Write-Log "已关闭系统代理（原 ProxyServer = 「$proxyServer」）。"

# — 4. 确认直连恢复 —
Start-Sleep -Seconds 2
$directOk = Test-Connection -ComputerName '8.8.8.8' -Count 1 -Quiet
Write-Log "关闭代理后直连 8.8.8.8 可达 = $directOk"

if ($directOk) {
    Write-Log '✅ 网络已恢复。'
} else {
    Write-Log '⚠️  代理已关闭但直连仍未恢复，可能存在其他网络问题（DNS / 网卡等）。'
}

Write-Log '=== fix-proxy 结束 ==='
