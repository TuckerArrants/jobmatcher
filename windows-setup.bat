@echo off
setlocal
cd /d "%~dp0"
title jobmatcher setup
echo Setting up jobmatcher in %CD%
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY goto :nopython

if not exist ".venv\Scripts\python.exe" (
  echo Creating a private Python environment in the .venv folder...
  %PY% -m venv .venv || goto :fail
)
echo Installing jobmatcher. This takes a minute the first time...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e . || goto :fail

if not exist ".env" (
  copy /y env.example .env >nul
  echo.
  echo Notepad will now open your key file.
  echo Paste your TypeSafe key right after TYPESAFE_API_KEY= with no spaces, then save and close Notepad.
  echo.
  pause
  notepad .env
)

echo.
echo Setup finished. Double-click windows-run.bat to run jobmatcher.
echo.
pause
exit /b 0

:nopython
echo Python was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH"
echo on the first screen of the installer. Then double-click this file again.
echo.
pause
exit /b 1

:fail
echo.
echo Setup failed. Select the messages above with the mouse, press Ctrl+C, and send them to
echo whoever is helping you.
echo.
pause
exit /b 1
