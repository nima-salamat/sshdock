$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (!(Test-Path .venv\Scripts\python.exe)) {
    py -3 -m venv .venv
}

.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
python -m app.main
