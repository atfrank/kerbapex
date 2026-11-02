@echo off
rem Lays the track map, delta and sector chips over the game window (RaceRoom in windowed or borderless mode).
rem StintRadio itself must be running. Options: --corner top-left|top-right|bottom-left|bottom-right  --size 520x380  --opacity 0.85  --game raceroom|lmu
cd /d "%~dp0"
title StintRadio overlay
".venv\Scripts\python.exe" -m stintradio overlay %*
