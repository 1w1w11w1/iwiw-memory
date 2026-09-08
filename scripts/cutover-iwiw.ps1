# cutover-iwiw.ps1 — 换装脚本：desktop profile 从 meow-memory 切换到 @iwiw/dsh-iwiw-memory
# 用法：
#   powershell -File scripts\cutover-iwiw.ps1            # 执行换装
#   powershell -File scripts\cutover-iwiw.ps1 -Rollback  # 回滚到 meow-memory
# 改动前自动备份 package.json / cordis.patch.yml 到 .cutover-bak\

param([switch]$Rollback)

$ErrorActionPreference = "Stop"
$profile = "C:\Users\YS\.dsh\profiles\desktop"
$src = "E:\desktop\111\dsh-iwiw-memory"
$bak = Join-Path $profile ".cutover-bak"
$link = Join-Path $profile "node_modules\@iwiw\dsh-iwiw-memory"

# ── 备份/还原辅助 ──
function Backup-Files {
    New-Item -ItemType Directory -Force -Path $bak | Out-Null
    Copy-Item (Join-Path $profile "package.json") $bak -Force
    Copy-Item (Join-Path $profile "cordis.patch.yml") $bak -Force
    Write-Host "[backup] profile 配置已备份到 $bak"
}

if ($Rollback) {
    Write-Host "=== 回滚：恢复 meow-memory ==="
    Copy-Item (Join-Path $bak "package.json") (Join-Path $profile "package.json") -Force
    Copy-Item (Join-Path $bak "cordis.patch.yml") (Join-Path $profile "cordis.patch.yml") -Force
    if (Test-Path $link) { (Get-Item $link).Delete(); Write-Host "[rollback] junction 已删除" }
    Write-Host "回滚完成。重启 DSH 生效。"
    exit 0
}

# ── 换装 ──
Backup-Files

# 1) junction link（幂等）
if (-not (Test-Path $link)) {
    New-Item -ItemType Junction -Path $link -Target $src | Out-Null
    Write-Host "[1/3] junction 已创建"
} else {
    Write-Host "[1/3] junction 已存在（跳过）"
}

# 2) package.json：dependencies + bundles 替换
$pkgPath = Join-Path $profile "package.json"
$pkg = Get-Content $pkgPath -Raw -Encoding UTF8
if ($pkg -notmatch [regex]::Escape('"@iwiw/dsh-iwiw-memory": "0.1.0"')) {
    $pkg = $pkg -replace [regex]::Escape('"meow-memory": "0.24.1",'), ('"@iwiw/dsh-iwiw-memory": "0.1.0",')
    $pkg = $pkg -replace [regex]::Escape('"meow-memory",'), ('"@iwiw/dsh-iwiw-memory",')
    Set-Content -Path $pkgPath -Value $pkg -Encoding UTF8
    Write-Host "[2/3] package.json 已替换（dependencies + bundles）"
} else {
    Write-Host "[2/3] package.json 已替换（跳过）"
}

# 3) cordis.patch.yml：meow 块 → iwiw 块
$patchPath = Join-Path $profile "cordis.patch.yml"
$patch = Get-Content $patchPath -Raw -Encoding UTF8
if ($patch -notmatch "dsh-iwiw-memory") {
    $newPatch = @'
# iwiw 记忆插件 —— 本地记忆内核（MCP 桥）
- id: '@iwiw/dsh-iwiw-memory'
  config:
    enabled: true
'@
    Set-Content -Path $patchPath -Value $newPatch -Encoding UTF8
    Write-Host "[3/3] cordis.patch.yml 已替换（meow 移除，iwiw 启用）"
} else {
    Write-Host "[3/3] cordis.patch.yml 已替换（跳过）"
}

Write-Host @"

=== 换装完成。下一步： ===
1. （可选）迁移 meow 记忆：python scripts\migrate_meow.py --dry-run 预览 → 去掉 --dry-run 执行
2. 重启 DSH Desktop
3. 验收清单见 scripts\cutover-verify.md
回滚：powershell -File scripts\cutover-iwiw.ps1 -Rollback
"@
