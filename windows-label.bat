@echo off
setlocal
cd /d "%~dp0"
title jobmatcher labeling
set PYTHONUTF8=1

if not exist ".venv\Scripts\jobmatch.exe" (
  echo jobmatcher is not set up yet. Double-click windows-setup.bat first.
  echo.
  pause
  exit /b 1
)

echo The labeling page is opening in your browser.
echo Your labels are saved as you go. Close this window when you are done.
echo.
".venv\Scripts\jobmatch.exe" label
