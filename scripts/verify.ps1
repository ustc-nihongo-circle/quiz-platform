$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$taskPython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw "Missing .venv. Create it and install the project with: python -m venv .venv"
}

$env:DJANGO_SECRET_KEY = "verification-only-secret"
$env:DJANGO_DEBUG = "1"
if (-not $env:POSTGRES_PASSWORD) {
    $env:POSTGRES_PASSWORD = "local-development-only"
}

Write-Host "Running SQLite fast tests"
$env:DJANGO_USE_SQLITE = "1"
& $taskPython -m pytest -m "not postgres" --basetemp ".pytest-tmp-verify-sqlite"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Running PostgreSQL integration tests"
$env:DJANGO_USE_SQLITE = "0"
& $taskPython -m pytest -m postgres --basetemp ".pytest-tmp-verify-postgres"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Running Ruff"
& $taskPython -m ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Running Django system checks"
& $taskPython manage.py check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Checking migration drift"
& $taskPython manage.py makemigrations --check --dry-run
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Checking static collection"
& $taskPython manage.py collectstatic --dry-run --noinput --clear
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
