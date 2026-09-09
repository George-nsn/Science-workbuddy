param(
    [ValidateSet('up', 'down', 'restart', 'status', 'logs', 'doctor', 'test', 'check', 'migrate')]
    [string]$Action = 'up',
    [switch]$WithWorker,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = if ($env:SCIENCE_BUDDY_PYTHON) { $env:SCIENCE_BUDDY_PYTHON } else { "D:\ProgramData\anaconda3\python.exe" }
Push-Location $Root
try {
    switch ($Action) {
        'up' { & "$Root\scripts\launcher\current.ps1" start -WithWorker:$WithWorker -NoBrowser:$NoBrowser }
        'down' { & "$Root\scripts\launcher\current.ps1" stop }
        'restart' { & "$Root\scripts\launcher\current.ps1" restart -WithWorker:$WithWorker -NoBrowser:$NoBrowser }
        'status' { & "$Root\scripts\launcher\current.ps1" status }
        'logs' { & "$Root\scripts\launcher\current.ps1" logs }
        'doctor' { & "$Root\scripts\launcher\current.ps1" doctor }
        'test' { Push-Location apps/api; & $Python -m pytest; Pop-Location }
        'check' {
            Push-Location apps/api
            & $Python -m ruff check src tests
            & $Python -m mypy src
            & $Python -m pytest
            Pop-Location
            npm run lint:web
            npm run typecheck:web
            npm run build:web
        }
        'migrate' { & "$Root\scripts\launcher\current.ps1" migrate }
    }
}
finally {
    Pop-Location
}
