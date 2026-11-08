@echo off
setlocal
cd /d "%~dp0"
title jobmatcher
set PYTHONUTF8=1

if not exist ".venv\Scripts\jobmatch.exe" (
  echo jobmatcher is not set up yet. Double-click windows-setup.bat first.
  echo.
  pause
  exit /b 1
)

if "%~1"=="" (
  ".venv\Scripts\jobmatch.exe" run
  if exist "user\reports\latest.html" start "" "user\reports\latest.html"
) else (
  ".venv\Scripts\jobmatch.exe" %*
)

echo.
echo Finished. To share the result, select the text above with the mouse and press Ctrl+C.
echo.
pause
