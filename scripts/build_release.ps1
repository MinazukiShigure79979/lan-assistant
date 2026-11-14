#requires -Version 5.1
<#
.SYNOPSIS
    Build a portable Lan.Assistant folder and a Windows installer.
.DESCRIPTION
    This script does NOT bundle models or Ollama.  The resulting installer
    contains only the PyInstaller runtime and application code.  Models are
    downloaded by setup_manager.py on first launch.

    Build prerequisites:
      * .venv with the app dependencies and PyInstaller
      * Inno Setup 6 (ISCC.exe)
    Install tools first: scripts\install_build_tools.ps1
#>
[CmdletBinding()]
param(
    [string]$IsccPath = "",
    [switch]$SkipInstaller,
    [switch]$InstallBuildDependencies
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$DistApp = Join-Path $Root "dist\Lan.Assistant"
$DistInstaller = Join-Path $Root "dist\installer"
$VersionInfoFile = Join-Path $Root "build\lan_version_info.txt"

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "BUILD FAILED: $Message" -ForegroundColor Red
    exit 1
}

function Read-VersionValue([string]$Expression) {
    $value = & $Python -c "import version; print($Expression)" 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
        Fail "无法读取 version.py"
    }
    return ([string]$value).Trim()
}

if (-not (Test-Path $Python)) {
    $Python = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $Python) {
    Fail "找不到 Python。请先创建 .venv 或安装 Python。"
}

$Version = Read-VersionValue "version.VERSION"
$DisplayVersion = Read-VersionValue "version.DISPLAY_VERSION"
$InstallerVersion = Read-VersionValue "version.INSTALLER_VERSION"
Write-Host "Lan.Assistant $Version ($DisplayVersion)" -ForegroundColor Cyan

if ($InstallBuildDependencies) {
    Write-Host "Installing build dependencies into $Python ..." -ForegroundColor Yellow
    & $Python -m pip install --upgrade -r (Join-Path $Root "requirements-build.txt")
    if ($LASTEXITCODE -ne 0) { Fail "PyInstaller 安装失败" }
}

& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Fail "当前 Python 没有 PyInstaller。先运行: $Python -m pip install -r requirements-build.txt"
}
& $Python -c "import faster_whisper, pyaudiowpatch, pystray, onnxruntime" 2>$null
if ($LASTEXITCODE -ne 0) {
    Fail "运行依赖不完整，请先在当前 .venv 安装程序依赖。"
}

if (Test-Path (Join-Path $Root "build")) {
    Remove-Item (Join-Path $Root "build") -Recurse -Force -ErrorAction SilentlyContinue
}
if (Test-Path $DistApp) {
    Remove-Item $DistApp -Recurse -Force
}
if (Test-Path $DistInstaller) {
    Remove-Item $DistInstaller -Recurse -Force
}
New-Item -ItemType Directory -Force -Path (Join-Path $Root "build") | Out-Null

Write-Host "1/3  Generating Windows version resource..." -ForegroundColor Cyan
& $Python (Join-Path $Root "scripts\make_version_info.py") $VersionInfoFile
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VersionInfoFile)) {
    Fail "版本资源生成失败"
}
$env:LAN_BUILD_VERSION_FILE = $VersionInfoFile

Write-Host "2/3  Running PyInstaller (onedir)..." -ForegroundColor Cyan
Push-Location $Root
try {
    & $Python -m PyInstaller --noconfirm --clean --distpath (Join-Path $Root "dist") --workpath (Join-Path $Root "build\pyinstaller") "Lan.Assistant.spec"
    if ($LASTEXITCODE -ne 0) { Fail "PyInstaller 构建失败" }
} finally {
    Pop-Location
}
$AppExe = Join-Path $DistApp "Lan.Assistant.exe"
if (-not (Test-Path $AppExe)) { Fail "未找到 $AppExe" }

# The installer must never contain model weights or the Ollama runtime.
$Forbidden = Get-ChildItem $DistApp -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Extension -eq ".gguf" -or
        $_.Name -ieq "ollama.exe" -or
        ($_.Name -eq "model.bin" -and $_.FullName -match "faster-whisper")
    }
if ($Forbidden) {
    $names = ($Forbidden | Select-Object -First 8 -ExpandProperty FullName) -join "`n"
    Fail "构建结果意外包含模型/Ollama 文件：`n$names"
}
Write-Host "    app size: $([math]::Round(((Get-ChildItem $DistApp -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)) MB" -ForegroundColor DarkGray

if ($SkipInstaller) {
    Write-Host ""
    Write-Host "Build complete (installer skipped): $DistApp" -ForegroundColor Green
    exit 0
}

Write-Host "3/3  Building Inno Setup installer..." -ForegroundColor Cyan
if ([string]::IsNullOrWhiteSpace($IsccPath)) {
    $candidates = @(
        $env:INNO_SETUP_ISCC,
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }
    $IsccPath = $candidates | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($IsccPath) -or -not (Test-Path $IsccPath)) {
    Fail "找不到 Inno Setup 6 的 ISCC.exe。安装后重试，或传 -IsccPath。`n  winget install --id JRSoftware.InnoSetup -e"
}

New-Item -ItemType Directory -Force -Path $DistInstaller | Out-Null
$iss = Join-Path $Root "installer\Lan.Assistant.iss"
Push-Location (Join-Path $Root "installer")
try {
    & $IsccPath "/DMyAppVersion=$Version" "/DMyAppDisplayVersion=$DisplayVersion" "/DMyVersionInfo=$InstallerVersion" "/DSourceDir=$DistApp" "/O$DistInstaller" $iss
    if ($LASTEXITCODE -ne 0) { Fail "Inno Setup 构建失败" }
} finally {
    Pop-Location
}

$Setup = Get-ChildItem $DistInstaller -Filter "Lan.Assistant-Setup-*.exe" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $Setup) { Fail "安装包生成后未找到输出文件" }

$Hash = Get-FileHash $Setup.FullName -Algorithm SHA256
$hashFile = "$($Setup.FullName).sha256"
"$($Hash.Hash.ToLower())  $($Setup.Name)" | Set-Content -Path $hashFile -Encoding ASCII
$manifest = [ordered]@{
    product = "Lan.Assistant"
    version = $Version
    display_version = $DisplayVersion
    installer_version = $InstallerVersion
    built_at = (Get-Date).ToString("o")
    installer = $Setup.Name
    installer_bytes = $Setup.Length
    sha256 = $Hash.Hash.ToLower()
    app_folder = $DistApp
    models_bundled = $false
    ollama_bundled = $false
}
$manifestPath = Join-Path $DistInstaller "release.json"
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $manifestPath -Encoding UTF8

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
Write-Host "  app:      $DistApp"
Write-Host "  setup:    $($Setup.FullName)"
Write-Host "  sha256:   $($Hash.Hash.ToLower())"
Write-Host "  manifest: $manifestPath"

