@echo off
rem Starts StintRadio and the race engineer; it waits for Le Mans Ultimate if the game is not running yet.
rem The live page is http://127.0.0.1:6420/ (Steam overlay: Shift+Tab > web browser > pin). Close this window to stop.
cd /d "%~dp0"
title StintRadio
".venv\Scripts\python.exe" -m stintradio --start %*
if errorlevel 1 (
  echo.
  echo StintRadio stopped with an error. Press any key to close.
  pause >nul
)
