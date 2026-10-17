<#
.SYNOPSIS
    Désinstalle le moteur NineDeo (registre + dossier %LOCALAPPDATA%\NineDeo).
.PARAMETER KeepFiles
    Conserve le dossier d'installation (journaux, ffmpeg).
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'NineDeo'),
    [switch]$KeepFiles
)
$HostName = 'com.ninedeo.host'
$roots = @(
    'HKCU:\Software\Google\Chrome\NativeMessagingHosts',
    'HKCU:\Software\Microsoft\Edge\NativeMessagingHosts',
    'HKCU:\Software\Chromium\NativeMessagingHosts',
    'HKCU:\Software\BraveSoftware\Brave-Browser\NativeMessagingHosts',
    'HKCU:\Software\Mozilla\NativeMessagingHosts'
)
foreach ($r in $roots) {
    $k = Join-Path $r $HostName
    if (Test-Path $k) { Remove-Item -Path $k -Force; Write-Host "Clé supprimée : $k" }
}
if (-not $KeepFiles -and (Test-Path $InstallDir)) {
    # Le script peut se trouver dans le dossier : suppression différée via cmd.
    Start-Process -WindowStyle Hidden cmd.exe -ArgumentList "/c timeout /t 2 >nul & rmdir /s /q `"$InstallDir`""
    Write-Host "Dossier supprimé : $InstallDir"
}
Write-Host 'NineDeo (moteur) désinstallé. Retirez l''extension depuis la page des extensions du navigateur.' -ForegroundColor Green
