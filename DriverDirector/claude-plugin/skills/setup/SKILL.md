---
name: setup
description: Check and prepare everything DriverDirector needs (the app, Le Mans Ultimate, OBS, ffmpeg, the voice runtime, the music model, the Claude CLI) and start the app. Use when the user wants to get DriverDirector working, or when something fails to connect.
---

# DriverDirector setup

All DriverDirector control goes through one helper. Run it with Bash from anywhere:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/dd.py" <command>
```

Below it is called `dd`.

1. Run `dd health`. If `home` is empty, ask the user where the DriverDirector folder is, then run `dd home "<path>"`.
2. If `app_running` is false, run `dd start`, then `dd health` again.
3. First check `game.live_session` in `dd health`: if it is true the user is in a live session: change nothing in the game and tell them.
   Otherwise, if `game.running` is false, run `dd launch game`; if `game.obs_running` is false, run `dd launch obs`. Do this straight away, without asking the user first. Then run `dd health` again (the game takes about a minute to answer on its API; check every 10 seconds or so, up to 4 minutes) and report whether each one came up.
4. Read the result and tell the user, in plain words, what is ready and what is not:
   - `game.running` / `game.api` / `game.replay_active`: Le Mans Ultimate. If it is still not running after step 3, say so. A replay is only needed later.
   - `game.obs_running`: OBS. If it is still not running after step 3, say so. The OBS scene `DriverDirector` must exist (the app's README explains it).
   - `ffmpeg`: required. If null, tell the user to install ffmpeg and put it on PATH.
   - `claude_cli`: needed for commentary (the Claude CLI is used, no API key). If null and commentary is enabled, say so. If `claude_logged_in` is false the CLI is signed out (its login expires now and then) and no commentary can be written: use the claude-login skill (`dd claude-login` prints the exact command for their own terminal; they sign in themselves, you never do it for them).
   - `voices.chatterbox`: true means the LMU Director voice engine is installed. If false, commentary falls back to the Edge voice.
   - `commentary.engine` and `voices.cloud_keys`: if the engine is `fish` or `elevenlabs` and its key shows false, say the key is not visible. The user sets `FISH_API_KEY` / `ELEVENLABS_API_KEY` themselves as Windows user environment variables and restarts the Claude app so new sessions see them. Never ask for a key in chat, never type or store one yourself.
   - `music`: `model_downloaded`, `ready` (the music loop is already generated) and `runtime`. If the source is `ai` and `ready` is false, offer `dd music` (about 3 minutes per 30 s clip, needs 4 GB free RAM, done once and cached).
5. Never start a long job here (a recording, a render, the music). Launching the game and OBS is the only thing setup starts without asking; everything else needs the user's agreement.
