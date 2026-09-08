# cutover-iwiw.ps1 — 换装脚本：desktop profile 从 meow-memory 切换到 @iwiw/dsh-iwiw-memory
# 用法：
#   powershell -File scripts\cutover-iwiw.ps1            # 执行换装
#   powershell -File scripts\cutover-iwiw.ps1 -Rollback  # 回滚到 meow-memory
# 改动前自动备份 package.json / cordis.patch.yml 到 .cutover-bak\
#
# 桌面 bundle 契约（DSH dsh-plugin-desktop 校验，缺一即启动崩溃）：
#   1. 插件包 package.json 必须声明 dsh.bundle.patch（非空字符串，指向包内 patch 文件）
#   2. 包内 cordis.patch.yml 用 - insert: 行装载插件（name = npm 包名，Node 按它 import）
#   3. profile 的 cordis.patch.yml 只是 id 覆盖层，id 必须与 insert 行 id 一致
# 本脚本第 4 步做预检，契约不满足时拒绝换装（2026-09-08 崩溃教训）。

param([switch]$Rollback)

$ErrorActionPreference = "Stop"
$profile = "C:\Users\YS\.dsh\profiles\desktop"
$src = "E:\desktop\111\dsh-iwiw-memory"
$bak = Join-Path $profile ".cutover-bak"
$link = Join-Path $profile "node_modules\@iwiw\dsh-iwiw-memory"

# 无 BOM UTF-8 写入（PS5.1 的 Set-Content -Encoding UTF8 会写 BOM，
# 曾导致 DSH 解析 package.json / cordis.patch.yml 失败）
function Write-Utf8NoBom($path, $content) {
    [System.IO.File]::WriteAllText($path, $content, [System.Text.UTF8Encoding]::new($false))
}

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

# ── 1) bundle 契约预检（换装前验证插件自身可被桌面加载）──
$pluginPkgPath = Join-Path $src "package.json"
$pluginPkg = Get-Content $pluginPkgPath -Raw -Encoding UTF8 | ConvertFrom-Json
$patchRel = $pluginPkg.dsh.bundle.patch
if (-not $patchRel -or $patchRel -isnot [string]) {
    throw "预检失败：插件 package.json 缺 dsh.bundle.patch（非空字符串）——桌面 profile 会启动崩溃"
}
$pluginPatchPath = Join-Path $src $patchRel
if (-not (Test-Path $pluginPatchPath)) {
    throw "预检失败：dsh.bundle.patch 指向的文件不存在：$pluginPatchPath"
}
$pluginPatchText = Get-Content $pluginPatchPath -Raw -Encoding UTF8
if ($pluginPatchText -notmatch "insert" -or $pluginPatchText -notmatch "@iwiw/dsh-iwiw-memory") {
    throw "预检失败：包内 cordis.patch.yml 缺 insert 行或包名引用"
}
if (-not (Test-Path (Join-Path $src "lib\index.js"))) {
    throw "预检失败：lib\index.js 不存在——先 npm run build"
}
Write-Host "[预检] bundle 契约通过（dsh.bundle.patch + insert 行 + lib 产物）"

# ── 备份 ──
Backup-Files

# ── 2) junction link（幂等）──
if (-not (Test-Path $link)) {
    New-Item -ItemType Junction -Path $link -Target $src | Out-Null
    Write-Host "[2/5] junction 已创建"
} else {
    Write-Host "[2/5] junction 已存在（跳过）"
}

# ── 3) package.json：dependencies + bundles 替换 ──
$pkgPath = Join-Path $profile "package.json"
$pkg = Get-Content $pkgPath -Raw -Encoding UTF8
if ($pkg -notmatch [regex]::Escape('"@iwiw/dsh-iwiw-memory"')) {
    $pkg = $pkg -replace '"meow-memory":\s*"[^"]*"', '"@iwiw/dsh-iwiw-memory": "0.1.0"'
    $pkg = $pkg -replace [regex]::Escape('"meow-memory",'), ('"@iwiw/dsh-iwiw-memory",')
    Write-Utf8NoBom $pkgPath $pkg
    Write-Host "[3/5] package.json 已替换（dependencies + bundles，无 BOM）"
} else {
    Write-Host "[3/5] package.json 已替换（跳过）"
}

# ── 4) profile cordis.patch.yml：id 覆盖层（id 必须与包内 insert 行一致）──
$patchPath = Join-Path $profile "cordis.patch.yml"
$patch = Get-Content $patchPath -Raw -Encoding UTF8
if ($patch -notmatch "@iwiw/dsh-iwiw-memory") {
    $newPatch = @'
# iwiw 记忆插件 —— 本地记忆内核（MCP 桥）
# 插件装载由包内 cordis.patch.yml 的 insert 行承担；此处仅做 id 覆盖。
- id: '@iwiw/dsh-iwiw-memory'
  config:
    enabled: true
'@
    Write-Utf8NoBom $patchPath ($newPatch + "`n")
    Write-Host "[4/5] cordis.patch.yml 已替换（meow 移除，iwiw id 覆盖，无 BOM）"
} else {
    Write-Host "[4/5] cordis.patch.yml 已替换（跳过）"
}

# ── 5) 物理验证：junction 可解析 + profile 文件无 BOM ──
if (-not (Test-Path (Join-Path $link "lib\index.js"))) { throw "验证失败：junction 无法解析 lib\index.js" }
foreach ($f in @($pkgPath, $patchPath)) {
    $bytes = [System.IO.File]::ReadAllBytes($f)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        throw "验证失败：$f 仍含 BOM"
    }
}
Write-Host "[5/5] 物理验证通过（junction 解析 + 无 BOM）"

Write-Host @"

=== 换装完成。下一步： ===
1. 重启 DSH Desktop
2. 验收清单见 scripts\cutover-verify.md
回滚：powershell -File scripts\cutover-iwiw.ps1 -Rollback
"@
