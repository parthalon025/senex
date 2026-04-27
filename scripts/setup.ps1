# senex/scripts/setup.ps1 -- first-time setup on Windows
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "[setup] Creating virtual environment at .venv ..." -ForegroundColor Cyan
if (Test-Path .venv) { Write-Host "[setup] .venv already exists; skipping creation." }
else { python -m venv .venv }

Write-Host "[setup] Installing senex (editable) + dependencies ..." -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -e .

Write-Host "[setup] Verifying senex --version ..." -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m senex --version

Write-Host ""
Write-Host "[setup] Done." -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Copy senex.config.toml.example -> senex.config.toml and edit"
Write-Host "  2. Run: .\.venv\Scripts\activate; senex doctor"
Write-Host "  3. Run: senex audit C:\path\to\your\repo"
