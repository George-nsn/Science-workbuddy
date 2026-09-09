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
Set-StrictMode -Version Latest

$Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
$ConfigPath = Join-Path $Root 'launcher.config.json'
$RuntimeRoot = Join-Path $Root '.runtime\launcher'
$PidRoot = Join-Path $RuntimeRoot 'pids'
$LogRoot = Join-Path $RuntimeRoot 'logs'
$LockPath = Join-Path $RuntimeRoot 'launcher.lock'
$StatePath = Join-Path $RuntimeRoot 'state.json'
$LauncherLog = Join-Path $LogRoot 'launcher.log'

New-Item -ItemType Directory -Path $PidRoot -Force | Out-Null
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null

if (-not (Test-Path $ConfigPath)) {
    throw "Launcher config not found: $ConfigPath"
}
$Config = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json

function Write-LauncherLog {
    param([string]$Message, [ValidateSet('INFO', 'WARN', 'ERROR')] [string]$Level = 'INFO')
    $line = "{0} [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $Level, $Message
    Add-Content -Path $LauncherLog -Value $line -Encoding UTF8
    $color = if ($Level -eq 'ERROR') { 'Red' } elseif ($Level -eq 'WARN') { 'Yellow' } else { 'Cyan' }
    Write-Host $line -ForegroundColor $color
}

function Convert-Arguments {
    param([object[]]$Values)
    return @($Values | ForEach-Object { [string]$_ })
}

function Find-Python {
    $candidates = @()
    if ($env:SCIENCE_BUDDY_PYTHON) { $candidates += $env:SCIENCE_BUDDY_PYTHON }
    $candidates += @(
        'D:\ProgramData\anaconda3\python.exe',
        (Join-Path $Root '.venv\Scripts\python.exe'),
        (Join-Path $Root 'apps\api\.venv\Scripts\python.exe')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return [IO.Path]::GetFullPath($candidate) }
    }
    foreach ($name in @('python', 'py')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { return $command.Source }
    }
    throw 'Python 3.12+ was not found. Set SCIENCE_BUDDY_PYTHON to the project interpreter.'
}

function Find-Npm {
    $command = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $command) { $command = Get-Command npm -ErrorAction SilentlyContinue }
    if (-not $command) { throw 'npm was not found. Install Node.js 22 or newer.' }
    return $command.Source
}

function Find-RedisServer {
    $command = Get-Command redis-server.exe -ErrorAction SilentlyContinue
    if (-not $command) { $command = Get-Command redis-server -ErrorAction SilentlyContinue }
    if ($command) { return $command.Source }
    return $null
}

function Test-Http {
    param([string]$Url, [int]$TimeoutSeconds = 3)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch { return $false }
}

function Get-HealthPayload {
    param([string]$Url)
    try { return Invoke-RestMethod -Uri $Url -TimeoutSec 4 }
    catch { return $null }
}

function Test-Port {
    param([int]$Port)
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Get-PortOwner {
    param([int]$Port)
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($connection) { return [int]$connection.OwningProcess }
    return $null
}

function Get-PidPath {
    param([string]$Name)
    return Join-Path $PidRoot "$Name.pid"
}

function Get-TrackedProcessId {
    param([string]$Name)
    $path = Get-PidPath $Name
    if (-not (Test-Path $path)) { return $null }
    $value = (Get-Content $path -Raw).Trim()
    if ($value -notmatch '^\d+$') { return $null }
    return [int]$value
}

function Test-ProcessAlive {
    param([Nullable[int]]$ProcessId)
    if (-not $ProcessId) { return $false }
    return [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Write-ProcessId {
    param([string]$Name, [int]$ProcessId)
    Set-Content -Path (Get-PidPath $Name) -Value $ProcessId -Encoding ASCII
}

function Remove-ProcessId {
    param([string]$Name)
    Remove-Item (Get-PidPath $Name) -Force -ErrorAction SilentlyContinue
}

function Enter-LauncherLock {
    if (Test-Path $LockPath) {
        $age = (Get-Date) - (Get-Item $LockPath).LastWriteTime
        if ($age.TotalMinutes -lt 5) { throw 'Another launcher operation is running.' }
        Remove-Item $LockPath -Force
    }
    Set-Content -Path $LockPath -Value "$PID|$(Get-Date -Format o)" -Encoding ASCII
}

function Exit-LauncherLock {
    Remove-Item $LockPath -Force -ErrorAction SilentlyContinue
}

function Start-TrackedProcess {
    param(
        [string]$Name,
        [string]$Executable,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [hashtable]$Environment = @{}
    )
    $existing = Get-TrackedProcessId $Name
    if (Test-ProcessAlive $existing) {
        Write-LauncherLog "$Name already running (PID $existing)."
        return $existing
    }
    Remove-ProcessId $Name
    $stdout = Join-Path $LogRoot "$Name.out.log"
    $stderr = Join-Path $LogRoot "$Name.err.log"
    $escapedExecutable = $Executable.Replace("'", "''")
    $escapedWorkingDirectory = $WorkingDirectory.Replace("'", "''")
    $argumentExpression = ($Arguments | ForEach-Object { "'" + $_.Replace("'", "''") + "'" }) -join ', '
    $environmentLines = @($Environment.GetEnumerator() | ForEach-Object {
        '$env:{0} = ''{1}''' -f $_.Key, ([string]$_.Value).Replace("'", "''")
    }) -join "`r`n"
    $command = @"
`$ErrorActionPreference = 'Continue'
`$env:PYTHONUNBUFFERED = '1'
$environmentLines
Set-Location '$escapedWorkingDirectory'
& '$escapedExecutable' @($argumentExpression)
exit `$LASTEXITCODE
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded
    ) -WorkingDirectory $WorkingDirectory -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    Write-ProcessId $Name $process.Id
    Write-LauncherLog "Started $Name (PID $($process.Id)); logs: $stdout / $stderr"
    return $process.Id
}

function Stop-TrackedProcess {
    param([string]$Name)
    $tracked = Get-TrackedProcessId $Name
    if (-not (Test-ProcessAlive $tracked)) {
        Remove-ProcessId $Name
        return
    }
    Write-LauncherLog "Stopping $Name (PID $tracked)..."
    & taskkill.exe /PID $tracked /T /F 2>$null | Out-Null
    Remove-ProcessId $Name
}

function Wait-ForHttp {
    param([string]$Name, [string]$Url, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Http $Url) {
            Write-LauncherLog "$Name is healthy: $Url"
            return
        }
        $processIdValue = Get-TrackedProcessId $Name
        if (-not (Test-ProcessAlive $processIdValue)) {
            throw "$Name exited before becoming healthy. Check $LogRoot\$Name.err.log"
        }
        Start-Sleep -Milliseconds 750
    }
    throw "$Name did not become healthy in $TimeoutSeconds seconds. Check $LogRoot\$Name.err.log"
}

function Assert-PortAvailable {
    param([string]$Name, [int]$Port, [string]$HealthUrl)
    if (-not (Test-Port $Port)) { return }
    if (Test-Http $HealthUrl) {
        Write-LauncherLog "$Name is already healthy on port $Port; adopting the existing service." 'WARN'
        return
    }
    $owner = Get-PortOwner $Port
    throw "Port $Port is occupied by PID $owner and is not a healthy Science Buddy $Name."
}

function Invoke-Migration {
    param([string]$Python)
    Write-LauncherLog 'Applying database migrations...'
    Push-Location (Join-Path $Root 'apps\api')
    try {
        $process = Start-Process -FilePath $Python -ArgumentList (Convert-Arguments $Config.commands.migration) -NoNewWindow -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Database migration failed with exit code $($process.ExitCode)." }
    }
    finally { Pop-Location }
    Write-LauncherLog 'Database migration completed.'
}

function Start-RedisIfAvailable {
    if (Test-Port 6379) {
        Write-LauncherLog 'Redis already listens on port 6379.'
        return $true
    }
    $server = Find-RedisServer
    if (-not $server) {
        Write-LauncherLog 'Redis is unavailable; API/Web will run with L1 + SQLite cache. Worker remains disabled.' 'WARN'
        return $false
    }
    Start-TrackedProcess -Name 'redis' -Executable $server -Arguments @('--port', '6379') -WorkingDirectory $Root | Out-Null
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port 6379) { return $true }
        Start-Sleep -Milliseconds 500
    }
    throw 'Redis did not listen on port 6379.'
}

function Start-Stack {
    Enter-LauncherLock
    try {
        $python = Find-Python
        $npm = Find-Npm
        Write-LauncherLog "Using Python: $python"
        Write-LauncherLog "Using npm: $npm"
        Assert-PortAvailable -Name 'API' -Port ([int]$Config.ports.api) -HealthUrl ([string]$Config.urls.apiLive)
        Assert-PortAvailable -Name 'Web' -Port ([int]$Config.ports.web) -HealthUrl ([string]$Config.urls.web)
        Invoke-Migration $python
        $redisReady = Start-RedisIfAvailable

        if (-not (Test-Http ([string]$Config.urls.apiLive))) {
            Start-TrackedProcess -Name 'api' -Executable $python -Arguments (Convert-Arguments $Config.commands.api) -WorkingDirectory (Join-Path $Root 'apps\api') | Out-Null
            Wait-ForHttp -Name 'api' -Url ([string]$Config.urls.apiLive) -TimeoutSeconds ([int]$Config.timeoutsSeconds.api)
        }

        $startWorker = $WithWorker.IsPresent -or [bool]$Config.services.workerDefault
        if ($startWorker) {
            if (-not $redisReady) {
                Write-LauncherLog 'Worker requested but Redis is unavailable; API/Web continue without Worker.' 'WARN'
            }
            elseif (-not (Test-ProcessAlive (Get-TrackedProcessId 'worker'))) {
                Start-TrackedProcess -Name 'worker' -Executable $python -Arguments (Convert-Arguments $Config.commands.worker) -WorkingDirectory (Join-Path $Root 'apps\api') | Out-Null
            }
        }

        if (-not (Test-Http ([string]$Config.urls.web))) {
            $webArguments = if ($WebMode -eq 'production') {
                @('--workspace', 'apps/web', 'run', 'start')
            }
            else {
                @('run', 'dev:web')
            }
            Start-TrackedProcess -Name 'web' -Executable $npm -Arguments $webArguments -WorkingDirectory $Root -Environment @{
                'NEXT_PUBLIC_API_URL' = 'http://127.0.0.1:8000/api/v1'
            } | Out-Null
            Wait-ForHttp -Name 'web' -Url ([string]$Config.urls.web) -TimeoutSeconds ([int]$Config.timeoutsSeconds.web)
        }

        $ready = Get-HealthPayload ([string]$Config.urls.apiReady)
        $state = [ordered]@{
            launcherVersion = $Config.launcherVersion
            startedAt = (Get-Date -Format o)
            webMode = $WebMode
            workerRequested = $startWorker
            redisAvailable = $redisReady
            apiReady = $ready
            urls = $Config.urls
        }
        $state | ConvertTo-Json -Depth 8 | Set-Content -Path $StatePath -Encoding UTF8
        Write-LauncherLog "Science Buddy is ready: $($Config.urls.web)"
        if (-not $NoBrowser) { Start-Process ([string]$Config.urls.web) }
    }
    finally { Exit-LauncherLock }
}

function Stop-Stack {
    Enter-LauncherLock
    try {
        foreach ($name in @('worker', 'web', 'api', 'redis')) { Stop-TrackedProcess $name }
        Remove-Item $StatePath -Force -ErrorAction SilentlyContinue
        Write-LauncherLog 'Science Buddy launcher-managed processes stopped.'
    }
    finally { Exit-LauncherLock }
}

function Show-Status {
    $ready = Get-HealthPayload ([string]$Config.urls.apiReady)
    $rows = @(
        [pscustomobject]@{ Service = 'API'; ProcessId = (Get-TrackedProcessId 'api'); Alive = (Test-ProcessAlive (Get-TrackedProcessId 'api')); Port = [int]$Config.ports.api; Healthy = (Test-Http ([string]$Config.urls.apiLive)); Log = (Join-Path $LogRoot 'api.err.log') },
        [pscustomobject]@{ Service = 'Web'; ProcessId = (Get-TrackedProcessId 'web'); Alive = (Test-ProcessAlive (Get-TrackedProcessId 'web')); Port = [int]$Config.ports.web; Healthy = (Test-Http ([string]$Config.urls.web)); Log = (Join-Path $LogRoot 'web.err.log') },
        [pscustomobject]@{ Service = 'Redis'; ProcessId = (Get-TrackedProcessId 'redis'); Alive = (Test-ProcessAlive (Get-TrackedProcessId 'redis')); Port = 6379; Healthy = (Test-Port 6379); Log = (Join-Path $LogRoot 'redis.err.log') },
        [pscustomobject]@{ Service = 'Worker'; ProcessId = (Get-TrackedProcessId 'worker'); Alive = (Test-ProcessAlive (Get-TrackedProcessId 'worker')); Port = '-'; Healthy = (Test-ProcessAlive (Get-TrackedProcessId 'worker')); Log = (Join-Path $LogRoot 'worker.err.log') }
    )
    $rows | Format-Table -AutoSize
    if ($ready) { Write-Host ('API readiness: ' + ($ready | ConvertTo-Json -Compress -Depth 6)) }
    Write-Host "Runtime directory: $RuntimeRoot"
}

function Show-Logs {
    Write-Host "Launcher log: $LauncherLog"
    Get-ChildItem $LogRoot -File | Sort-Object LastWriteTime -Descending | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
    if (Test-Path $LauncherLog) { Get-Content $LauncherLog -Tail 60 }
}

function Invoke-Doctor {
    $python = Find-Python
    $npm = Find-Npm
    Write-Host "Root: $Root"
    Write-Host "Python: $python"
    & $python -c "import sys; print(sys.version); import fastapi, sqlalchemy, numpy, networkx; print('backend imports: ok')"
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency check failed.' }
    Write-Host "npm: $npm"
    & $npm --version
    & node --version
    Write-Host "API port owner: $(Get-PortOwner ([int]$Config.ports.api))"
    Write-Host "Web port owner: $(Get-PortOwner ([int]$Config.ports.web))"
    Write-Host "Redis available: $(Test-Port 6379)"
    Write-Host 'Doctor check completed.'
}

try {
    switch ($Action) {
        'start' { Start-Stack }
        'stop' { Stop-Stack }
        'restart' { Stop-Stack; Start-Stack }
        'status' { Show-Status }
        'logs' { Show-Logs }
        'migrate' { Invoke-Migration (Find-Python) }
        'doctor' { Invoke-Doctor }
    }
}
catch {
    Write-LauncherLog $_.Exception.Message 'ERROR'
    Write-Host "See launcher log: $LauncherLog" -ForegroundColor Yellow
    if ($KeepWindow) { Read-Host 'Press Enter to close' | Out-Null }
    exit 1
}

if ($KeepWindow) { Read-Host 'Press Enter to close' | Out-Null }
