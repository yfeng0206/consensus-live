@echo off
REM ConsensusAITrader - Daily Live Trading Script (Windows)
REM Run after market close (~2:00 PM PST / 5:00 PM ET)
REM
REM Usage:
REM   run_daily.bat              Normal daily run
REM   run_daily.bat --dry-run    Run without pushing to gist
REM   run_daily.bat --force      Force re-run even if already ran today

setlocal
cd /d "%~dp0"

echo ============================================================
echo  ConsensusAITrader - Daily Live Run
echo  %DATE% %TIME%
echo ============================================================

REM Activate conda/venv if needed (uncomment and edit path)
REM call conda activate base

REM Run the live trader
python live\live_trader.py %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Live trader failed with exit code %ERRORLEVEL%
    echo Check live\logs\ for details.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo Done. Dashboard updated.
