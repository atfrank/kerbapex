@echo off
rem Starts DriverDirector and opens its control panel in your browser (http://127.0.0.1:6410/).
rem Close this window to stop the app. Add --demo to try it without the game.
cd /d "%~dp0"
title DriverDirector
".venv\Scripts\python.exe" -m driverdirector %*
if errorlevel 1 (
  echo.
  echo DriverDirector stopped with an error. Press any key to close.
  pause >nul
)
