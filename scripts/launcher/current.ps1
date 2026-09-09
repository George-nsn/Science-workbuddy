param(
    [ValidateSet('start', 'stop', 'restart', 'status', 'logs', 'migrate', 'doctor')]
    [string]$Action = 'start',
    [switch]$WithWorker,
    [switch]$NoBrowser,
    [switch]$KeepWindow,
    [ValidateSet('dev', 'production')]
    [string]$WebMode = 'dev'
)

$ErrorActionPreference = 'Stop'
$Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$ConfigPath = Join-Path $Root 'launcher.config.json'
if (-not (Test-Path $ConfigPath)) {
    throw "Launcher config not found: $ConfigPath"
}
$config = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$version = [string]$config.launcherVersion
if ($version -notmatch '^v\d+$') {
    throw "Invalid launcherVersion '$version'."
}
$implementation = Join-Path $PSScriptRoot "$version\launcher.ps1"
if (-not (Test-Path $implementation)) {
    throw "Launcher implementation not found: $implementation"
}
& $implementation -Action $Action -WithWorker:$WithWorker -NoBrowser:$NoBrowser -KeepWindow:$KeepWindow -WebMode $WebMode
exit $LASTEXITCODE
