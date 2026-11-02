---
name: setup
description: Check and prepare what StintRadio needs (its install, Le Mans Ultimate with Enable Plugins, ffmpeg, the voice) and fix it when the race engineer does not start or cannot be heard.
---

# StintRadio setup

Helper: `python "${CLAUDE_PLUGIN_ROOT}/scripts/sr.py" <command>`.

1. `sr health`. `home` empty: ask the user where the StintRadio folder is and run `sr home <path>`. It needs its own
   `.venv` there (`python -m venv .venv` then `.venv\Scripts\python -m pip install -e ..\DriverDirector -e .`):
   StintRadio uses DriverDirector's shared library for telemetry analysis, ffmpeg and ElevenLabs.
2. `game_dir` empty: Le Mans Ultimate was not found in the Steam libraries; set it with
   `sr settings '{"live": {"game_dir": "<install folder>"}}'`.
3. Coach stays "waiting for the game" with the game running: **Enable Plugins** must be on in the game's settings, and
   the game restarted once after turning it on.
4. No voice: for `elevenlabs`, ELEVENLABS_API_KEY must be set in the user's environment (never ask for the key or type
   it); without it the Windows voice is used and `sr state` says why in `error`.
