$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$python = Join-Path $repoRoot 'backend/.venv/Scripts/python.exe'
& $python (Join-Path $PSScriptRoot 'code/run_synthetic_demo.py')
if ($LASTEXITCODE -ne 0) { throw 'Synthetic training failed' }
& $python (Join-Path $PSScriptRoot 'code/verify_synthetic_demo.py')
