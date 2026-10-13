$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment not found at $python. Create it with: python -m venv venv"
}

# Install dependencies on first run if they are missing.
try {
    & $python -c "import fastapi, uvicorn, requests, multipart" | Out-Null
} catch {
    & $python -m pip install -r requirements.txt
}

& $python main.py
