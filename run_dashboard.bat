@echo off
REM Launch the TMONE license dashboard (Streamlit)
cd /d "%~dp0"
set PORT=%1
if "%PORT%"=="" set PORT=8501
python -m streamlit run dashboard.py --server.port %PORT% --server.address 0.0.0.0
