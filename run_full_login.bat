@echo off
cd /d "%~dp0"
echo Building FULL login count workbook (all sheets)...
python run_full_login.py -m 2026-06 --from-db --export-sql %*
if errorlevel 1 (
  echo.
  echo DB not available - trying CSV-only mode...
  python run_full_login.py -m 2026-06 --export-sql %*
)
pause
