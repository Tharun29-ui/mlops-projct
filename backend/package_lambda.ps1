$ErrorActionPreference = "Stop"
$backendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$packageDir = Join-Path $backendDir "build\lambda"
$pythonExe = Join-Path $backendDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Create backend/.venv first, then rerun this packaging script."
}

if (Test-Path -LiteralPath $packageDir) {
    Remove-Item -LiteralPath $packageDir -Recurse -Force
}
New-Item -ItemType Directory -Path $packageDir -Force | Out-Null

& $pythonExe -m pip install `
    --only-binary=:all: `
    --platform manylinux2014_x86_64 `
    --implementation cp `
    --python-version 3.12 `
    --abi cp312 `
    --target $packageDir `
    -r (Join-Path $backendDir "requirements-lambda.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Cross-platform Lambda dependency installation failed with exit code $LASTEXITCODE."
}

Copy-Item -LiteralPath (Join-Path $backendDir "app") -Destination $packageDir -Recurse
Copy-Item -LiteralPath (Join-Path $backendDir "lambda_handler.py") -Destination $packageDir
Write-Host "Lambda package ready at $packageDir"
