<#
.SYNOPSIS
    Construit un exécutable autonome de l'hôte (aucun Python requis sur le poste cible).

.DESCRIPTION
    Produit installer\windows\dist\ninedeo_host\ninedeo_host.exe avec PyInstaller.
    install.ps1 l'utilise automatiquement s'il est présent.
    Optionnel : -WithFfmpeg copie ffmpeg.exe/ffprobe.exe (depuis le PATH) dans dist\ninedeo_host\bin.
#>
[CmdletBinding()]
param([string]$Python = 'py', [switch]$WithFfmpeg)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Build = Join-Path $PSScriptRoot 'build'
$Venv = Join-Path $Build 'venv'
New-Item -ItemType Directory -Force -Path $Build | Out-Null
if ($Python -eq 'py') { & py -3 -m venv $Venv } else { & $Python -m venv $Venv }
$Py = Join-Path $Venv 'Scripts\python.exe'
& $Py -m pip install -q --upgrade pip
& $Py -m pip install -q --upgrade -r (Join-Path $Root 'host\requirements.txt') pyinstaller
& $Py -m PyInstaller --noconfirm --clean --onedir --console --name ninedeo_host `
    --icon (Join-Path $PSScriptRoot 'ninedeo.ico') `
    --paths (Join-Path $Root 'host') `
    --collect-submodules yt_dlp --collect-data yt_dlp --collect-all curl_cffi `
    --distpath (Join-Path $PSScriptRoot 'dist') --workpath (Join-Path $Build 'work') --specpath $Build `
    (Join-Path $Root 'host\ninedeo_host.py')
if ($WithFfmpeg) {
    $bin = Join-Path $PSScriptRoot 'dist\ninedeo_host\bin'
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    foreach ($tool in 'ffmpeg', 'ffprobe') {
        $src = (Get-Command $tool -ErrorAction Stop).Source
        Copy-Item $src $bin -Force
    }
}
& (Join-Path $PSScriptRoot 'dist\ninedeo_host\ninedeo_host.exe') --selftest
Write-Host "`nExécutable prêt : installer\windows\dist\ninedeo_host\ninedeo_host.exe" -ForegroundColor Green
exit 0
