$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$candidates = @()

if ($env:PYTHON) {
  $candidates += $env:PYTHON
}

$candidates += @(
  (Join-Path $root ".venv\Scripts\python.exe"),
  "py",
  "python",
  (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
)

$python = $null
foreach ($candidate in $candidates) {
  try {
    if ($candidate -match "[\\/]" -and -not (Test-Path $candidate)) {
      continue
    }
    $version = & $candidate --version 2>$null
    if ($LASTEXITCODE -eq 0 -and $version) {
      $python = $candidate
      break
    }
  } catch {
    continue
  }
}

if (-not $python) {
  Write-Error "No usable Python runtime found. Install Python 3.11+, or set the PYTHON environment variable to a python.exe path."
}

$port = $env:PORT
if (-not $port) {
  $port = "4173"
}

Write-Host "Using Python: $python"
Write-Host "Preview URL: http://localhost:$port"
if (Test-Path (Join-Path $root ".env")) {
  Write-Host "Loading local .env from project root"
} else {
  Write-Host "No .env found. UI preview still works; chat needs credentials."
}
& $python app.py
