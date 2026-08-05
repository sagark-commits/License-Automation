@echo off
cd /d "%~dp0"
echo Fetching login sessions from oneproduct DB...
python tmone_report.py --verify-db %*
pause
