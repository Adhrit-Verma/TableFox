$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $repo "services\dbmap\src"
Set-Location $repo
python -m dbmap.mcp_server
