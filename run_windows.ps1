$ErrorActionPreference = "Stop"
if (!(Test-Path .venv)) { python -m venv .venv }
. .\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
python -m app.main
