$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $repo "services\dbmap\src"
Set-Location $repo
python -c "from dbmap.cli import run_api; run_api()"
