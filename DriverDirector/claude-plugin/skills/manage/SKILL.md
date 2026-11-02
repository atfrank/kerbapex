---
name: manage
description: Check on, adjust or stop a running DriverDirector job, view or change its settings, finish a recording that stopped early, or prepare the music. Use for "how is it going", "cancel", "change the voice/music/inlay", "produce the video" after a recording.
---

# Manage DriverDirector

Helper: `python "${CLAUDE_PLUGIN_ROOT}/scripts/dd.py" <command>` (called `dd` below).

- Progress: `dd status` (one snapshot) or `dd watch` (follows until the job ends, up to 9 minutes per call).
- Stop a running job: `dd cancel` (confirm with the user first; a recording in progress is lost).
- Finish a recording that stopped at "recorded" (memory was tight): the user closes Le Mans Ultimate or you run `dd exit-replay`, then `dd produce` and `dd watch`.
- Settings: `dd settings` shows all; `dd settings '<json patch>'` merges a change, for example `{"commentary":{"voice":"british-female-normal"}}` or `{"music":{"level_db":-28}}`. Only change what the user asked for, and report what changed.
- Leftover raw recordings from earlier runs: `dd cleanup` lists them, and after the user agrees `dd cleanup --yes` moves them into `_cleanup/` (recoverable; batches older than 7 days are deleted later, `dd cleanup --purge DAYS` does that now).
- Music ahead of time: `dd music` (generates the loop once; needs 4 GB free RAM).
- Voices: `dd voices` shows the engine, which cloud keys the app sees (true/false only, never the keys) and the voice IDs; `dd voice-test [chatterbox|edge|fish|elevenlabs] [text]` voices one short line and prints the file to listen to (a cloud engine sends that one line of text to the service, so confirm first). Never ask for, type or store an API key: the user sets `ELEVENLABS_API_KEY` / `FISH_API_KEY` as Windows user environment variables and restarts the Claude app.
- New commentary wording or a new voice over the same recording: `dd redo-commentary`, then `dd watch`. It writes, voices and mixes the commentary again and records nothing. It needs the race loaded and planned first (`dd load`, `dd plan`; the game must be running with the replay), and writes a new numbered video.
- Redo only the reference-lap clip (best qualifying lap box) and finish the video again: `dd rerecord-reference`, then `dd watch`. It needs the race loaded and planned first (`dd load`, `dd plan`) and the game free.
- If anything cannot connect, run the setup skill.

Never start `dd run` from here; that is the direct skill, which asks for confirmation first.
