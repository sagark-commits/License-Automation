@echo off
REM Run on Windows WITH internet. Python version should match server python3.
REM Creates offline_bundle\ for USB transfer to Linux server.

setlocal
cd /d "%~dp0"
set PY=python
where python3 >nul 2>&1 && set PY=python3

echo Using:
%PY% --version

if not exist offline_bundle\wheels mkdir offline_bundle\wheels
%PY% -m pip install --upgrade pip wheel setuptools
%PY% -m pip download -r requirements.txt -d offline_bundle\wheels

set APP=offline_bundle\license-utilization-automation
if not exist "%APP%" mkdir "%APP%"
for %%F in (tmone_report.py tmone_report_main.py db_connections.py tmone_db_mode.py db_usage_queries.py excel_format.py tenants.yaml requirements.txt db_config.yaml.example run_from_db.sh install_offline.sh) do (
  if exist "%%F" copy /Y "%%F" "%APP%\" >nul
)
copy /Y install_offline.sh offline_bundle\ >nul

echo.
echo Done. Copy offline_bundle folder to server.
echo IMPORTANT: Wheels from Windows may NOT work on Linux.
echo For Linux servers, run download_offline_packages.sh on a Linux VM with same OS/Python.
endlocal