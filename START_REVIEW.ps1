param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('backend', 'frontend')]
    [string]$Mode,
    [int]$ApiPort = 8001,
    [int]$UiPort = 3001
)
$ErrorActionPreference = 'Stop'
if ($Mode -eq 'backend') {
    $python = Join-Path $PSScriptRoot 'backend/.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Create backend/.venv and install backend/requirements.txt first.'
    }
    Set-Location (Join-Path $PSScriptRoot 'backend')
    $uvicornArguments = @('-B', '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', [string]$ApiPort, '--workers', '1')
    if (Test-Path -LiteralPath '.env') {
        $uvicornArguments += @('--env-file', '.env')
    }
    & $python @uvicornArguments
} else {
    $node = (Get-Command node.exe -ErrorAction Stop).Source
    $next = Join-Path $PSScriptRoot 'frontend/node_modules/next/dist/bin/next'
    if (-not (Test-Path -LiteralPath $next)) {
        throw 'Run npm ci inside the candidate frontend directory first.'
    }
    $env:NEXT_PUBLIC_API_BASE_URL = "http://localhost:$ApiPort"
    $env:PORT = [string]$UiPort
    Set-Location (Join-Path $PSScriptRoot 'frontend')
    & $node $next dev --webpack --hostname 127.0.0.1 --port $UiPort
}
exit $LASTEXITCODE
