@echo off
REM ===========================================================================
REM DeskBrief refresh runner. This is what vba/Refresh.bas shells out to.
REM
REM Contract with the macro: append everything to logs\deskbrief.log and exit
REM with the REAL errorlevel, so the macro can tell success from failure and
REM point the user at the log.
REM ===========================================================================
setlocal

REM %~dp0 is this script's own folder, with a trailing backslash. Excel launches
REM us from whatever directory it feels like, so never rely on the caller's cwd.
cd /d "%~dp0"

if not exist "logs" mkdir "logs"

set "LOGFILE=%~dp0logs\deskbrief.log"
set "PYTHON=%~dp0.venv\Scripts\python.exe"

echo. >> "%LOGFILE%"
echo ============================================================ >> "%LOGFILE%"
echo DeskBrief refresh started %DATE% %TIME% >> "%LOGFILE%"
echo ============================================================ >> "%LOGFILE%"

if not exist "%PYTHON%" (
    echo ERROR: no virtualenv at %PYTHON% >> "%LOGFILE%"
    echo Create it with: py -3.11 -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt >> "%LOGFILE%"
    echo ERROR: no virtualenv at %PYTHON%
    endlocal
    exit /b 9009
)

REM Call the venv interpreter directly rather than activate.bat: it is one
REM process instead of two and it keeps the real errorlevel intact.
"%PYTHON%" -m src.cli refresh >> "%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"

echo DeskBrief refresh finished %DATE% %TIME% with exit code %RC% >> "%LOGFILE%"

if not "%RC%"=="0" (
    echo DeskBrief FAILED with exit code %RC%. See logs\deskbrief.log
) else (
    echo DeskBrief refresh OK. See logs\deskbrief.log
)

endlocal & exit /b %RC%
