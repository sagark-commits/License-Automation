# Prepare USB bundle on Windows for Linux server (no internet).
# Usage: .\prepare_usb_bundle.ps1 -PythonVersion 3.9

param(
    [string]$PythonVersion = "3.9",
    [switch]$LegacyServer = $true
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bundle = Join-Path $Root "offline_bundle"
$Wheels = Join-Path $Bundle "wheels"
$App = Join-Path $Bundle "license-utilization-automation"

function Reset-BundleDirectory {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    $backupName = "offline_bundle_old_{0}" -f (Get-Date -Format "yyyyMMdd_HHmmss")
    Write-Host "Moving previous bundle to $backupName ..."
    try {
        Rename-Item -Path $Path -NewName $backupName -ErrorAction Stop
        return
    } catch {
        Write-Host "WARN: rename failed" -ForegroundColor Yellow
    }
    Remove-Item -Path $Path -Recurse -Force -ErrorAction Stop
}

Write-Host "=== TMONE offline USB bundle builder ===" -ForegroundColor Cyan
Write-Host "Python target: $PythonVersion"
if ($LegacyServer) { Write-Host "Wheel platform: manylinux2014 (RHEL 7 / old glibc servers)" -ForegroundColor Yellow }

docker info 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is not running. Start Docker Desktop and retry."
}

Reset-BundleDirectory -Path $Bundle
New-Item -ItemType Directory -Path $Wheels -Force | Out-Null
New-Item -ItemType Directory -Path $App -Force | Out-Null

$Image = "python:$PythonVersion-slim-bookworm"
Write-Host "Pulling $Image ..."
docker pull $Image

Write-Host "Downloading Linux wheels ..."
$cid = docker create $Image sleep 600
$req = Join-Path $Root "requirements.txt"
docker cp "$req" "${cid}:/tmp/requirements.txt"
docker start $cid | Out-Null
$pyVerShort = $PythonVersion -replace '\.', ''
if ($LegacyServer) {
    $bashCmd = "pip install -q --upgrade pip wheel setuptools; mkdir -p /tmp/wheels; pip download -r /tmp/requirements.txt -d /tmp/wheels --platform manylinux2014_x86_64 --python-version $PythonVersion --implementation cp --only-binary=:all:"
} else {
    $bashCmd = 'pip install -q --upgrade pip wheel setuptools; mkdir -p /tmp/wheels; pip download -r /tmp/requirements.txt -d /tmp/wheels'
}
# pip writes warnings to stderr; do not treat that as a hard failure
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
docker exec $cid bash -lc $bashCmd 2>&1 | Out-Host
$dlExit = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($dlExit -ne 0) { throw "pip download failed (exit $dlExit)" }
docker cp "${cid}:/tmp/wheels/." $Wheels
docker rm -f $cid | Out-Null

$AppFiles = @(
    "tmone_report.py", "db_connections.py", "tmone_db_mode.py", "db_usage_queries.py",
    "db_login_queries.py", "login_export.py", "csv_login.py", "excel_format.py",
    "tenants.yaml", "requirements.txt", "db_config.yaml.example",
    "run_from_db.sh", "run_full_login.py", "export_login_csvs_psql.sh"
)
foreach ($f in $AppFiles) {
    $src = Join-Path $Root $f
    if (Test-Path $src) { Copy-Item $src $App }
}
Copy-Item (Join-Path $Root "install_offline.sh") $Bundle
Copy-Item (Join-Path $Root "SERVER_HANDOFF_README.txt") $Bundle -ErrorAction SilentlyContinue

$Zip = Join-Path $Root "tmone_offline_bundle_py$PythonVersion.zip"
if (Test-Path $Zip) {
    $zipOld = "tmone_offline_bundle_py{0}_old_{1}.zip" -f $PythonVersion, (Get-Date -Format "yyyyMMdd_HHmmss")
    Rename-Item $Zip $zipOld -ErrorAction SilentlyContinue
}
Compress-Archive -Path $Bundle -DestinationPath $Zip -Force

$wheelCount = (Get-ChildItem $Wheels -Filter *.whl).Count
Write-Host ""
Write-Host "SUCCESS" -ForegroundColor Green
Write-Host "  Folder: $Bundle"
Write-Host "  Zip:    $Zip"
Write-Host "  Wheels: $wheelCount"
if ($wheelCount -eq 0) { throw "No wheels downloaded. Check Docker output above." }
Write-Host ""
Write-Host "Copy the ZIP to USB. Server admin: unzip and run install_offline.sh"