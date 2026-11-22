@echo off
setlocal
cd /d "%~dp0"
echo ==============================================================
echo RWX Blast Radius - official sample test (30 commits)
echo ==============================================================
python --version
if errorlevel 1 (
  echo Python not found. Install Python 3.9 or newer.
  pause
  exit /b 1
)
set "DATA_DIR=%CD%\sample"
python selector.py > output.jsonl
if errorlevel 1 (
  echo ERROR: selector failed.
  pause
  exit /b 1
)
python score.py sample sample\answers.json output.jsonl
if errorlevel 1 (
  echo ERROR: scoring failed.
  pause
  exit /b 1
)
echo.
echo This is a local sample score, not the official hidden leaderboard score.
pause
