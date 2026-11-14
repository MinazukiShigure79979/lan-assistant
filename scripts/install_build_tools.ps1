#requires -Version 5.1
<#
.SYNOPSIS
    Install the tools needed to build the Lan.Assistant installer.
.DESCRIPTION
    Installs PyInstaller into the project's .venv and checks for Inno Setup 6.
    It does not install Python, models or Ollama.
#>
[CmdletBinding()]
param(
    [switch]$InstallInnoSetup
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "找不到 .venv\Scripts\python.exe"
}

Write-Host "Installing PyInstaller into $Python ..." -ForegroundColor Cyan
& $Python -m pip install --upgrade -r (Join-Path $Root "requirements-build.txt")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 安装失败"
}
& $Python -c "import PyInstaller; print('PyInstaller', PyInstaller.__version__)"

$iscc = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    Write-Host "Inno Setup: $iscc" -ForegroundColor Green
    exit 0
}
if ($InstallInnoSetup) {
    $winget = (Get-Command winget -ErrorAction SilentlyContinue).Source
    if (-not $winget) {
        throw "没有 winget，请手动安装 Inno Setup 6: https://jrsoftware.org/isdl.php"
    }
    & $winget install --id JRSoftware.InnoSetup -e --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup 安装失败，请手动安装"
    }
    Write-Host "Inno Setup 安装完成，重新打开终端后运行 build_release.ps1。" -ForegroundColor Green
    exit 0
}

Write-Host "Inno Setup 6 未安装。" -ForegroundColor Yellow
Write-Host "可以运行: scripts\install_build_tools.ps1 -InstallInnoSetup"
Write-Host "或手动下载: https://jrsoftware.org/isdl.php"

