$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $repo "services\dbmap\src"
Set-Location $repo
$venvPython = Join-Path $repo ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }
& $python -m dbmap.mcp_server
