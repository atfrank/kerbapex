@echo off
rem The KerbApex control panel: start and stop StintRadio, its coach and the overlay, and change the settings.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m stintradio gui
) else (
  start "" pythonw -m stintradio gui
)
