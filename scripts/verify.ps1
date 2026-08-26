$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$taskPython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw "Missing .venv. Create it and install the project with: python -m venv .venv"
}

$env:DJANGO_SECRET_KEY = "verification-only-secret"
$env:DJANGO_DEBUG = "1"

Write-Host "Running SQLite fast tests"
$env:DJANGO_USE_SQLITE = "1"
& $taskPython -m pytest -m "not postgres"

Write-Host "Running PostgreSQL integration tests"
$env:DJANGO_USE_SQLITE = "0"
& $taskPython -m pytest -m postgres

Write-Host "Running Ruff"
& $taskPython -m ruff check .

Write-Host "Running Django system checks"
& $taskPython manage.py check
