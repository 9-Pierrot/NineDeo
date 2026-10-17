<#
.SYNOPSIS
    Installe le moteur NineDeo (hôte natif) pour Chrome, Edge, Brave, Chromium et Firefox.

.DESCRIPTION
    1. Trouve Python 3.9+ (ou utilise l'exécutable empaqueté s'il a été construit).
    2. Copie l'hôte dans %LOCALAPPDATA%\NineDeo et crée un environnement virtuel.
    3. Installe yt-dlp (+ curl_cffi) et télécharge ffmpeg s'il est absent.
    4. Déclare l'hôte natif dans le registre (HKCU : aucun droit administrateur requis).

.PARAMETER InstallDir
    Dossier d'installation (défaut : %LOCALAPPDATA%\NineDeo).
.PARAMETER Python
    Chemin d'un python.exe précis.
.PARAMETER UseExe
    Utilise l'exécutable empaqueté (dist\ninedeo_host\ninedeo_host.exe, voir build_exe.ps1).
.PARAMETER SkipFfmpeg
    Ne télécharge pas ffmpeg.
.PARAMETER ExtensionId
    Identifiant Chrome/Edge de l'extension (défaut : identifiant fixe de NineDeo).
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'NineDeo'),
    [string]$Python = '',
    [switch]$UseExe,
    [switch]$SkipFfmpeg,
    [string]$ExtensionId = 'hcijkjkgdbihledmefkjiafcejmcgjom',
    [string]$FirefoxId = 'ninedeo@ninedeo.app'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # accélère Invoke-WebRequest
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$HostName = 'com.ninedeo.host'
$Root     = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Utf8     = New-Object System.Text.UTF8Encoding $false

function Step($text)  { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Ok($text)    { Write-Host "    [OK] $text" -ForegroundColor Green }
function Warn($text)  { Write-Host "    [!]  $text" -ForegroundColor Yellow }
function Fail($text)  { Write-Host "`n[ERREUR] $text" -ForegroundColor Red; exit 1 }

Write-Host ''
Write-Host '  NineDeo - installation du moteur de telechargement' -ForegroundColor Blue
Write-Host "  Dossier : $InstallDir"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# --------------------------------------------------------------------------
# 1. Moteur : exécutable empaqueté ou Python + venv
# --------------------------------------------------------------------------
$ExeSource = Join-Path $PSScriptRoot 'dist\ninedeo_host'
if (-not $UseExe -and -not $Python -and (Test-Path (Join-Path $ExeSource 'ninedeo_host.exe'))) {
    $UseExe = $true
}

if ($UseExe) {
    Step 'Copie de l''exécutable empaqueté'
    if (-not (Test-Path (Join-Path $ExeSource 'ninedeo_host.exe'))) {
        Fail "Exécutable introuvable : lancez d'abord build_exe.ps1 (ou retirez -UseExe)."
    }
    $AppDir = Join-Path $InstallDir 'app'
    if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
    Copy-Item -Recurse $ExeSource $AppDir
    $HostExe = Join-Path $AppDir 'ninedeo_host.exe'
    $BinDir  = Join-Path $AppDir 'bin'
    Ok $HostExe
}
else {
    Step 'Recherche de Python 3.9+'
    $candidates = @()
    if ($Python) { $candidates += ,@($Python) }
    $candidates += ,@('py', '-3')
    $candidates += ,@('python')
    $candidates += ,@('python3')
    $PyCmd = $null
    foreach ($c in $candidates) {
        try {
            $exe = $c[0]; $pre = @($c | Select-Object -Skip 1)
            $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v) {
                $ver = [version]$v[0]
                if ($ver -ge [version]'3.9' -and $v[1] -notmatch 'WindowsApps') { $PyCmd = $v[1]; Ok "Python $($v[0]) : $PyCmd"; break }
            }
        } catch { }
    }
    if (-not $PyCmd) {
        Warn 'Python 3.9+ introuvable.'
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            $answer = Read-Host '    Installer Python 3.12 avec winget maintenant ? (O/n)'
            if ($answer -eq '' -or $answer -match '^[oOyY]') {
                winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
                $PyCmd = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
                if (-not (Test-Path $PyCmd)) { Fail 'Installation de Python terminée : relancez install.bat dans une nouvelle fenêtre.' }
            } else { Fail 'Installez Python depuis https://www.python.org/downloads/ (cochez « Add to PATH ») puis relancez.' }
        } else { Fail 'Installez Python depuis https://www.python.org/downloads/ (cochez « Add to PATH ») puis relancez.' }
    }

    Step 'Copie des fichiers de l''hôte'
    $HostDir = Join-Path $InstallDir 'host'
    New-Item -ItemType Directory -Force -Path $HostDir | Out-Null
    if (Test-Path (Join-Path $HostDir 'ninedeo')) { Remove-Item -Recurse -Force (Join-Path $HostDir 'ninedeo') }
    Copy-Item -Recurse -Force (Join-Path $Root 'host\ninedeo') $HostDir
    Copy-Item -Force (Join-Path $Root 'host\ninedeo_host.py'), (Join-Path $Root 'host\requirements.txt') $HostDir
    Get-ChildItem -Recurse -Directory -Filter '__pycache__' $HostDir | Remove-Item -Recurse -Force
    Ok $HostDir

    Step 'Environnement virtuel et dépendances (yt-dlp, curl_cffi)'
    $Venv = Join-Path $InstallDir 'venv'
    $VenvPy = Join-Path $Venv 'Scripts\python.exe'
    if (-not (Test-Path $VenvPy)) { & $PyCmd -m venv $Venv; if ($LASTEXITCODE -ne 0) { Fail 'Création du venv impossible.' } }
    & $VenvPy -m pip install --disable-pip-version-check -q --upgrade pip
    & $VenvPy -m pip install --disable-pip-version-check -q --upgrade -r (Join-Path $HostDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) {
        Warn 'Échec avec curl_cffi, nouvel essai avec yt-dlp seul.'
        & $VenvPy -m pip install --disable-pip-version-check -q --upgrade 'yt-dlp[default]'
        if ($LASTEXITCODE -ne 0) { Fail 'Installation de yt-dlp impossible (connexion internet ?).' }
    }
    Ok ("yt-dlp " + (& $VenvPy -c "from yt_dlp.version import __version__; print(__version__)"))

    # Lanceur : Chrome/Firefox exécutent ce .bat via cmd.exe (stdin/stdout transmis tels quels).
    $HostExe = Join-Path $InstallDir 'ninedeo-host.bat'
    $bat = "@echo off`r`n`"%~dp0venv\Scripts\python.exe`" -X utf8 `"%~dp0host\ninedeo_host.py`" %*`r`n"
    [IO.File]::WriteAllText($HostExe, $bat, [Text.Encoding]::ASCII)
    $BinDir = Join-Path $HostDir 'bin'
}

# --------------------------------------------------------------------------
# 2. ffmpeg
# --------------------------------------------------------------------------
Step 'ffmpeg'
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$ffLocal = Join-Path $BinDir 'ffmpeg.exe'
$ffPath  = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (Test-Path $ffLocal) {
    Ok "déjà présent : $ffLocal"
} elseif ($ffPath) {
    Ok "trouvé dans le PATH : $($ffPath.Source)"
} elseif ($SkipFfmpeg) {
    Warn 'ffmpeg absent (option -SkipFfmpeg) : fusion audio/vidéo et conversions désactivées.'
} else {
    $url = 'https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
    $zip = Join-Path $env:TEMP 'ninedeo-ffmpeg.zip'
    $tmp = Join-Path $env:TEMP 'ninedeo-ffmpeg'
    try {
        Write-Host '    Téléchargement de ffmpeg (~130 Mo)…'
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        Get-ChildItem -Recurse -Path $tmp -Include 'ffmpeg.exe', 'ffprobe.exe' | Copy-Item -Destination $BinDir -Force
        Ok "installé dans $BinDir"
    } catch {
        Warn "Téléchargement impossible ($($_.Exception.Message)). Installez ffmpeg manuellement : winget install Gyan.FFmpeg"
    } finally {
        Remove-Item -Force -ErrorAction SilentlyContinue $zip
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp
    }
}

# --------------------------------------------------------------------------
# 3. Manifestes Native Messaging + registre
# --------------------------------------------------------------------------
Step 'Déclaration de l''hôte natif auprès des navigateurs'
$chromeManifest = Join-Path $InstallDir "$HostName.chrome.json"
$firefoxManifest = Join-Path $InstallDir "$HostName.firefox.json"
function New-HostManifest([string]$allowKey, [string[]]$allowValues) {
    $m = [ordered]@{
        name        = $HostName
        description = 'NineDeo - moteur de telechargement (yt-dlp + ffmpeg)'
        path        = $HostExe
        type        = 'stdio'
    }
    $m[$allowKey] = $allowValues
    return ($m | ConvertTo-Json -Depth 3)
}
[IO.File]::WriteAllText($chromeManifest, (New-HostManifest 'allowed_origins' @("chrome-extension://$ExtensionId/")), $Utf8)
[IO.File]::WriteAllText($firefoxManifest, (New-HostManifest 'allowed_extensions' @($FirefoxId)), $Utf8)

$keys = @{
    'Google Chrome / Brave / Vivaldi / Opera' = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts'
    'Microsoft Edge'                          = 'HKCU:\Software\Microsoft\Edge\NativeMessagingHosts'
    'Chromium'                                = 'HKCU:\Software\Chromium\NativeMessagingHosts'
    'Brave'                                   = 'HKCU:\Software\BraveSoftware\Brave-Browser\NativeMessagingHosts'
    'Firefox'                                 = 'HKCU:\Software\Mozilla\NativeMessagingHosts'
}
foreach ($entry in $keys.GetEnumerator()) {
    $key = Join-Path $entry.Value $HostName
    New-Item -Path $key -Force | Out-Null
    $manifest = if ($entry.Key -eq 'Firefox') { $firefoxManifest } else { $chromeManifest }
    Set-Item -Path $key -Value $manifest
    Ok "$($entry.Key)"
}

Copy-Item -Force (Join-Path $PSScriptRoot 'uninstall.ps1') $InstallDir

# --------------------------------------------------------------------------
# 4. Vérification
# --------------------------------------------------------------------------
Step 'Auto-test du moteur'
if ($UseExe) { & $HostExe --selftest } else { & (Join-Path $InstallDir 'venv\Scripts\python.exe') (Join-Path $InstallDir 'host\ninedeo_host.py') --selftest }
if ($LASTEXITCODE -eq 2) { Warn 'Moteur opérationnel mais sans ffmpeg.' }
elseif ($LASTEXITCODE -ne 0) { Fail 'L''auto-test a échoué : voir le message ci-dessus.' }

Write-Host "`nInstallation terminée." -ForegroundColor Green
Write-Host "Ouvrez les paramètres de l'extension NineDeo et cliquez sur « Tester la connexion »."
Write-Host "(Si le navigateur était ouvert pendant l'installation, rechargez l'extension.)"
exit 0
