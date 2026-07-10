param(
    [switch]$Install,
    [switch]$NoBrowser,
    [switch]$Check
)

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
$venvPython = Join-Path $repo ".venv\Scripts\python.exe"

if ($Install) {
    if (-not (Test-Path $venvPython)) {
        python -m venv (Join-Path $repo ".venv")
    }

    & $venvPython -m pip install -e (Join-Path $repo "services\dbmap[dev]")
    Push-Location $repo
    try {
        npm.cmd install
    }
    finally {
        Pop-Location
    }
}

$python = if (Test-Path $venvPython) { $venvPython } else { "python" }
$arguments = @((Join-Path $repo "scripts\dev.py"))

if ($NoBrowser) { $arguments += "--no-browser" }
if ($Check) { $arguments += "--check" }

& $python @arguments
exit $LASTEXITCODE
