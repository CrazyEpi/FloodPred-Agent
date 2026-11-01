@echo off
setlocal
cd /d "%~dp0"
set "FLOODPRED_PYTHON=%~dp0.venv-d4\Scripts\python.exe"
set "PYTHONUTF8=1"

if not exist "%FLOODPRED_PYTHON%" goto MISSING_ENV
if not exist "%~dp0streamlit_app.py" goto MISSING_APP
if /I "%~1"=="--check" goto CHECK

echo FloodPred - local chat and historical replay
echo Using the existing .venv-d4 environment.
echo DeepSeek reads its key from the local .env file; it is not printed here.
echo Open http://127.0.0.1:8501 in your browser after startup.
echo Press Ctrl+C in this window to stop the server.
echo Run this file from Windows Explorer or a normal terminal, not a sandbox.
echo.
"%FLOODPRED_PYTHON%" -m streamlit run "%~dp0streamlit_app.py" --server.address 127.0.0.1 --server.port 8501 --server.headless true
if errorlevel 1 goto FAILED
exit /b 0

:CHECK
"%FLOODPRED_PYTHON%" -c "import sys, streamlit, app.conversation, app.chat_context, app.chat_memory; print('Startup imports OK'); print('Python:', sys.executable)"
exit /b %errorlevel%

:MISSING_ENV
echo ERROR: .venv-d4\Scripts\python.exe was not found beside this script.
echo No new environment has been created. Check the project folder.
pause
exit /b 1

:MISSING_APP
echo ERROR: streamlit_app.py was not found beside this script.
pause
exit /b 1

:FAILED
echo.
echo Server exited with an error. Check the output above.
echo If port 8501 is in use, close the other server before retrying.
echo If DeepSeek reports WinError 10013, run from a normal Windows terminal.
pause
exit /b 1
