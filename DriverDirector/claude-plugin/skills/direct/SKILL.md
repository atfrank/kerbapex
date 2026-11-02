---
name: direct
description: Produce a directed replay video for one driver from start to finish (choose the replay, the focus driver and options, plan the camera work and picture-in-picture moments, confirm with the user, then record and produce the video). Use when the user wants a DriverDirector video made.
argument-hint: "[driver name] [replay name or id]"
disable-model-invocation: true
---

# Make a DriverDirector video

Helper: `python "${CLAUDE_PLUGIN_ROOT}/scripts/dd.py" <command>` (called `dd` below). Ask the user only for what they have not already said.

## 1. Get ready
**The user may be playing.** `dd health` shows `game.live_session`: when it is true the game is in a live session (someone is driving or
in the garage). Do not launch, load a replay, exit a replay or run anything that drives the game while it is true: say so and wait for the
user to say the game is free. (`dd exit-replay` and the app's own exits now refuse to leave a live session, but do not rely on that.)
Run `dd health`. If the app is not running, `dd start`. Le Mans Ultimate and OBS must be running: if `game.running` is false run `dd launch game`, and if `game.obs_running` is false run `dd launch obs`, straight away and without asking the user. Then run `dd health` every 10 seconds or so (up to 4 minutes) until the game answers on its API (`game.api` true) before going on.

## 2. Choose the replay
Run `dd replays` and show the user the race replays (id, track, session, date, size). Use the one they named, or ask. Replays other than races have no race history to direct from.
Run `dd load <id>`. It opens the replay in the game and reads its history (drivers, exact lap and sector times, incidents). Report the track, how many drivers and incidents.

## 3. Choose the driver and options
Run `dd roster` and confirm the focus driver by exact name (match the user's wording, for example "Alex Hartley").
Defaults are good for a full race. Offer these only if the user cares; change them with `dd settings '<json patch>'`:
- Part of the race: `{"direct":{"t_start":600,"t_end":900}}` (replay seconds; use `null` for the whole race)
- Picture-in-picture count/size/corner: `{"direct":{"max_inlays":4,"inlay_size":0.36,"inlay_corner":"top_right"}}`
- Camera mix: `{"camera":{"weights":{"trackside":2,"onboard":1,"driving":3,"tv":1,"rear":0}}}`. Every camera of the game is in a group (names read from its own UI, `Bin/UI.zip`): trackside `CycleGroup1-4`, onboard `Dashcam`, `RoofCam`, `FrontBumper`, `CockpitSide`, driving `COCKPIT`, `BonnetCamera`, `NoseCamera`, tv `TracksideCycle` (the game's own cycling trackside camera, weight 1), and rear `RearBumper`, `ReverseRoofCamera`. The rear group looks backwards, so it is only used (weight 2) while a car is on the driver's tail in a battle; its base weight stays 0. Each group rotates through its cameras. Change a group's list with `{"camera":{"cameras":{"driving":["COCKPIT","NoseCamera"]}}}` (a list replaces the old one). Do not make up camera names: an unknown name does nothing.
- Commentary on/off and voice: `{"commentary":{"enabled":true,"voice":"british-female-normal"}}`
- Cloud voices: `{"commentary":{"tts_engine":"elevenlabs","elevenlabs_voice_id":"<id>"}}` or `"fish"` with `"fish_voice_id"` (and `"fish_model":"s2.1-pro-free"` for Fish's free tier). The commentary text is sent to that service, so only use one when the user chose it. The API keys are read from the Windows user environment variables `ELEVENLABS_API_KEY` / `FISH_API_KEY`: never ask for a key in chat, never type one anywhere; if `dd voices` shows a key as false, tell the user to set the variable themselves (and restart the Claude app). `dd voice-test <engine>` voices one short line first. Changing the voice re-voices the commentary automatically.
- The driver's own mistakes: on by default (`{"direct":{"driving_feedback":true,"mistake_min_loss_s":0.25,"mistake_max":6}}`). From the game's telemetry file for the race (recorded for the player's own car only) each lap is compared with the best qualifying lap; stretches that cost at least that much, with a measured cause (too deep, braked too early, missed the apex, ran wide, poor exit), are narrated just after they happen. `dd plan` lists them under `driving` and says why there are none (no telemetry file, someone else drove).
- Commentary content (any replay with exact results, any driver): status lines say who is directly ahead and behind, the gap to each and which way it moved over the last lap; mistakes say what they did to those gaps; events say where on track they happened. Where the focus driver's telemetry exists, that is the corner (named for tracks with a table, else `corner five`, counted from the start line over the slow corners of the reference lap, so it may not match the official numbering; add your own names in `%APPDATA%\DriverDirector\corners.json`); for anyone else it is the timing sector. Times, speeds and distances are said the way people say them ("about a second and a half", "about thirty kilometres an hour", "about six car lengths"), never as decimals or technical units.
- Voice excitement (ElevenLabs): `commentary.elevenlabs_expression` holds `stability`, `style` and `speed` (up to 1.2) for the three excitement levels; lower stability and higher style are more animated. `dd voice-test elevenlabs "text"` to try it. The commentary style (`commentary.style`) is a live-television, excited, present-tense description.
- Incident cameras: a lone car (spin, wall) is shown onboard, several cars from the trackside; the box opens `incident_preroll_single` (6 s) / `incident_preroll_multi` (4 s) before the first contact.
- Slow-motion boxes: `{"direct":{"slow_mo_inlays":3,"slow_mo_speed":0.5}}` plays that many of the highest-scoring incident/pass boxes in slow motion (default 0 = none; speed 0.25 to 0.9). Each box keeps its time on screen; a shorter clip around the key moment is stretched, so frames repeat rather than interpolate. The reference-lap box is never slowed. `dd plan` marks them `slow_motion: true`.
- Race clock: the overlay shows the leader's lap (`LAP n`, or `LAP n / total` in lap-limited races) and the time left above the standings, and `FINAL LAP` once the clock is out. It reads the race limit from the results file; nothing to set.
- The opening: with the race covered from its start, the commentary opens by naming the track and where it is, how many turns it
  has (from a table of published turn counts, `%APPDATA%\DriverDirector\tracks.json` adds or corrects entries; an unlisted track
  gets no turn count), about how long a lap is and how long the race is, then qualifying and the grid. A title card (event, track,
  driver) is shown for `direct.title_card_s` seconds (8; 0 = none). The results file has no weather or time of day, so say them
  yourself if you want them spoken: `{"direct":{"conditions":"a dry night race"}}`.
- Reference lap: besides the box, the commentary compares the first lap with it at the end of sectors one and two, and the name card
  shows the live gap to the best qualifying lap and the corner name (`direct.live_delta`, needs the telemetry file).
- Replays of mistakes: `{"direct":{"mistake_replays":2}}` shows the driver's worst mistakes again in slow motion right after they
  happen (tagged REPLAY). Boxes fade in and out, name the cars in them, and slow-motion boxes say HALF SPEED.
- Race clock and standings: the overlay shows `LAP n / total` (the total is known from the results) and the time left, `FINAL LAP`
  on the winner's last lap, gaps over a minute as minutes and seconds, and a tyre letter per car when the race used several
  compounds. Pit stops, personal bests, class-best sectors, penalties and track-limit warnings of the driver are commentated,
  and the video ends with a recap (place against grid, gap to the class winner, best lap, what the mistakes cost).
- Writing the commentary yourself (when the Claude CLI is signed out, or to hand-edit): `dd claude-login` says whether the CLI works.
  If it does not and the user cannot sign in right now, you can write the lines instead of waiting. After `dd plan`:
  1. `GET /api/commentary/scenes` (with curl or a few lines of Python; there is no dd command) lists the scenes: `id`, `t`, `kind`, `facts`,
     `slot` (seconds available) and `max_words`.
  2. Write ONE line per scene, as the writer would be told to: only the facts given (never invent a time, gap, place or name), at most
     `max_words` words, live-TV commentary in present tense with everyday wording, no he/she/his/her/him/man/woman (use the name,
     "the driver" or "they"), and keep the facts' own wording for times, speeds and distances. Numbers written as digits must appear in
     the scene's facts; spelling them out as words is safe.
  3. `POST /api/commentary/script` with `{"lines": {"<scene id>": "<text>", ...}}`. It is checked with the same rules as the writer's
     output; if it answers 422 the message lists the problems, fix those lines and send it again (nothing is saved until it passes).
  4. `dd settings '{"commentary":{"reuse_script":true}}'`, then `dd run`: a saved script that still fits the plan (same scenes at the
     same times) is used instead of calling the CLI. After changing the plan, scene times change, the script no longer fits and a new
     one has to be written (or the CLI used). Say plainly to the user that the lines were written by you and not by the Claude CLI.
- Races without a qualifying results file cannot show the reference lap: `dd plan` then says so. Use
  `{"direct":{"reference_lap":false}}` for that race (the live gap then compares with the driver's best race lap, and says "vs best lap"),
  and turn it back on afterwards.
- Replay ids in the library shift whenever new sessions are played: look the race up by NAME in `dd replays` before every `dd load`.
- Commentary loudness: with `commentary.level_voice` (on) every spoken line is brought to the same level, the whole track is compressed
  and lifted to `commentary.voice_target_lufs` (-13; the game's own sound is about -17 on average), and the game sound is ducked
  `commentary.duck_db` (18) while someone speaks. If it is still too quiet raise `voice_target_lufs` (-11) or `duck_db`, not
  `voice_gain_db` (a plain gain is eaten by the limiter).
- Finishing a video again (`dd redo-commentary`, `dd produce`, `dd rerecord-reference`) uses the raw recordings of the LAST recording.
  OBS numbers its raw files again after they are moved to `_cleanup`, so two races can have files of the same name in different
  batches; the app records the batch in the recording info and refuses to guess when a name is in several. To keep a race's raw
  files safe, move them into their own folder (`_raw/<race>`). If a later recording overwrote the info of an earlier race, load and plan
  that race and run `dd rebuild-recording FOLDER`: it matches the files to the plan's boxes by order and length and refuses a wrong file.
- Louder commentary and music: `commentary.voice_gain_db` (6) raises the commentary, `commentary.duck_db` (14) lowers the game
  sound under it; `{"music":{"source":"off"}}` for no music.
- The voice of a commentary (all of it, one or two voices): live and emotional, not a table of facts. The writer is told to convey the
  tension, the sting of a mistake and the thrill of a pass, to react like a fan, never to turn a feeling into a new fact, to use the full
  name only at first and then mostly the first or the last name, and to pick the one thing that matters in a status update.
- Cinematic film instead of a broadcast: `{"cinematic":{"enabled":true}}`. No commentary, leaderboard, speedo or
  boxes; long TV/trackside shots, cut-aways to big incidents elsewhere, chosen moments played in the game's own
  slow motion, music, film grade, letterbox and an opening title; the video is `..._cinematic.mp4`. `dd plan` shows
  `style`, `slow_moments` and `cutaways`, and the estimate includes the extra length of slow motion. The game's slow
  motion runs at 1/8 speed (`cinematic.slow_rate` 0.125, measured), so each slow moment of 4 replay seconds is 32 s of film. Turn it off afterwards with `{"cinematic":{"enabled":false}}`.
- Two commentators: `{"commentary":{"commentators":2}}` plus a second voice for the engine in use (`elevenlabs_voice_id_b`,
  `fish_voice_id_b`, `edge_voice_b` or `voice_b`) gives a play-by-play voice and an analyst. Without a second voice it stays single. `GET /api/voices/elevenlabs` lists the voices of the user's ElevenLabs account (the app reads the key itself; never ask for it) so a second voice can be chosen that contrasts with the first. The two are written as one conversation: the analyst answers what the play-by-play voice just said and adds something new, in the same breath; both speak as "I", "you" and "we" to each other, and about a driver it is still never he or she. Status lines, incidents, passes, battles, pit stops, bests, penalties and the recap are exchanges; the opening and the flag stay with one voice.
- Long races: the finishing step is done in parts of `compose_chunk_s` (600) seconds that survive a failure and show real progress.
  `dd plan` prints an `estimate` (minutes to record, minutes to finish, GB needed) and the preflight checks the disk space.
- After the video: `dd highlights [VIDEO]` cuts a 4-minute highlights video from it, `dd shorts [VIDEO]` makes one vertical clip per
  box in a folder next to it, and a `<video>.youtube.txt` with the title, description and chapters is written beside every video.
- Standings timing test: `dd sync-test [DELAY ...]` records one short stretch (about 18 seconds) once per overlay delay (0.4, 1.0
  and 1.6 by default) and puts the takes side by side, so the delay is picked by eye in a minute; it needs the replay open like a
  recording. Then `dd settings '{"direct":{"overlay_delay_s":1.0}}'`.
- Leaderboard timing: `{"direct":{"overlay_delay_s":0.3}}` holds the board back behind the picture if it still looks early.
- Music: `{"music":{"source":"ai"}}` (`ai` = local MusicGen, `elevenlabs` = Eleven Music, `folder` with `"folder":"C:\Music"`, or `off`). `elevenlabs` needs a paid ElevenLabs plan and the ELEVENLABS_API_KEY environment variable (same rules as the voice: never ask for or type the key); it sends the music prompt to ElevenLabs, makes one instrumental of `music.elevenlabs_seconds` (default 120) once, caches it, and loops it under the video. The app's `/api/music/test` makes a short sample first.
- Reference lap: `{"direct":{"reference_lap":true}}` shows the focus driver's best qualifying lap (from the qualifying replay of the same event) in the picture-in-picture box during the race's first lap. It needs the qualifying results file and replay, and the video must include the race start; `dd plan` says what is missing. The run opens the qualifying replay first, then returns to the race, which adds about 2 to 4 minutes.
- Car telemetry (the game's speedo): `{"direct":{"telemetry":true}}`
- Leaderboard rows: `{"direct":{"tower_ahead":5,"tower_behind":5}}`
Set the focus driver with `{"direct":{"focus_driver":"<name>"}}`.

## 4. Plan and confirm
Run `dd plan`. Show the user the length, focus driver, the `estimate`, each picture-in-picture moment (`video_at`-`video_until`, label, drivers, camera, and `slow_motion` when it plays in slow motion) and each driving mistake the narrator will call (`driving.mistakes`, with its `video_at`). These times are VIDEO times (the video starts before the green flag); `replay_at` is the replay clock and must not be given to the user as a video time. If `driving.note` says there is no telemetry file, say so.
Tell them what will happen and how long it takes: the game and OBS are driven automatically and the screen must stay free while it records (real time: about the race length), then the voice and music are made and the video composited (about 25 to 40 minutes more for a full race, and the first video also waits for the music). Ask for a clear yes. Do not start without it.

## 5. Run
Run `dd run`. It first runs `dd preflight` (a replay is open, OBS answers, and the game's replay UI can really be hidden) and refuses to start if anything fails: tell the user which check failed and fix it (the game must not be minimised) instead of recording anyway. Then follow it with `dd watch` (it stops after 9 minutes; run it again until it prints FINAL or an error; use run_in_background for the long stretches and report each stage change briefly).
- Tell the user to leave the keyboard and mouse alone while it records (about the race length plus 2 to 4 minutes when a reference lap is used): Windows Task View (Win+Tab), the Start menu or search, or a pop-up taking focus makes the app refuse to click the game ("explorer.exe is holding focus"). Nothing is recorded then; ask the user to press Esc, click the game once, and retry.
- Preflight failure `replay on screen`, or a "UI did not change" failure right after loading: the game reports the replay open while it shows the main menu. Run `dd exit-replay`, wait about 25 seconds, `dd load <id>` again, `dd plan` again (loading clears the plan), then `dd run`.
- `NEEDS: close Le Mans Ultimate ... run dd produce` means memory was too tight to continue: have the user close the game (or `dd exit-replay`), then `dd produce` and `dd watch`.
- Any `WARNING:` line from `dd watch` is shown to the user at once. A "could not hide the game UI" warning means the footage will show the game's toolbar: offer `dd cancel` and a re-run rather than finishing.
- On `error`, show the message ("signed out" / "OAuth session expired" means the commentary writer's Claude login lapsed: use the claude-login skill: the user signs in again in their own terminal, then `dd redo-commentary` or `dd run` again; nothing was lost). The recording is kept; `dd produce` retries the finishing.

## 6. Report
The video is named after the race: `<track>_<date>_<session>_<driver>.mp4` in the OBS output folder. When it is finished and its length checks out, the app moves that job's raw recordings (about 14 GB for a full race) into `_cleanup/` next to it and deletes batches older than 7 days; finished videos are never touched. `dd cleanup` lists leftovers from earlier runs and `dd cleanup --yes` moves them aside (ask the user first).
Give the final file path, the length, and anything that warned. Check a few frames yourself (the picture-in-picture boxes carry no game UI such as the standings panel or the timeline strip, at their VIDEO times) before saying it is good. Remind the user that the first listen is theirs: audio cannot be checked automatically.
If the reference-lap box shows the game's UI, redo only that clip: `dd exit-replay`, `dd load <race id>`, `dd plan` (same settings, so the same times), then `dd rerecord-reference` and `dd watch`. It brings the other clips back from `_cleanup/`, re-voices the commentary only if the voice changed, and writes a new numbered video.
