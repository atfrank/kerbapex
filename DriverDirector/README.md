# DriverDirector

Driver-focused automated replay director for Le Mans Ultimate, modelled on LMU Director:
camera direction, OBS recording, browser-source overlays, PiP battle/incident inlays, and AI
commentary in one app.

## Pipeline

1. **Scan** – capture telemetry (rF2 shared-memory plugin) while the replay plays; saved as JSONL.
2. **Detect** – battles, overtakes, incidents (spin/contact), penalties, pit stops, each scored.
3. **Plan** – camera cues for the focus driver (weighted trackside/onboard/driving, shorter cuts
   during action, start/finish coverage) plus up to N PiP inlays for *other* drivers' action.
4. **Record** – OBS records a main pass, then one clip per inlay (overlay in `pip` mode).
5. **Commentary** – scenes built from the plan, Claude writes lines (validated: word budget, no
   invented numbers, retry), edge-tts voices them, one timed track.
6. **Compose** – ffmpeg overlays the clips as timed PiP boxes and mixes ducked commentary.

## Run

```
.venv\Scripts\python -m driverdirector --demo      # simulated race, no game needed
.venv\Scripts\python -m driverdirector             # real game (LMU: Settings > Gameplay > Enable Plugins)
.venv\Scripts\python -m driverdirector doctor      # checks struct layout, shared memory, OBS
```

Control panel: http://127.0.0.1:6410/ · OBS Browser Source: `http://127.0.0.1:6410/overlay`
(1920x1080). Themes: copy `driverdirector/web/overlay/themes/default` to
`%APPDATA%\DriverDirector\themes\<name>` and edit `style.css`. Needs `ANTHROPIC_API_KEY`
for commentary and ffmpeg on PATH. Tests: `.venv\Scripts\python -m pytest`.

## Game control (REST API, port 6397)

LMU's built-in REST API (swagger at `http://127.0.0.1:6397/swagger/`) provides replay library,
loading, driver focus by slot, camera by name, seek and transport. Verified live: replay list/load,
focus, camera groups. Camera names: Trackside `CycleGroup1-4`, Onboard `Dashcam/RoofCam/FrontBumper/
CockpitSide/Mirror/RearBumper/ReverseRoofCamera`, Driving `COCKPIT/BonnetCamera/NoseCamera`.
Transport commands are `VCRCOMMAND_PLAY` / `VCRCOMMAND_STOP`.

## Reference lap (best qualifying lap beside lap one)

`direct.reference_lap = true` adds a picture-in-picture box that opens at the green flag and plays the focus
driver's best qualifying lap for that lap's length. The qualifying results XML gives each lap's start time
(`<Lap et=...>`, the same convention as the race laps), so the lap is located on the qualifying replay's clock.
The qualifying replay is the library entry of the same event (same track and timestamp, session Qualify).
Recording opens it first (exit to menu, load, find the driver's slot by name in that replay's standings), records
the lap in `driving` view with the overlay in `pip` mode and no race data, then reloads the race replay. Other
boxes keep clear of it. A missing results file, replay, timed lap, or a video that starts after the green flag is
reported when planning, not mid-recording.

## Hiding LMU's replay UI

The REST HUD endpoints (`hud/toggleAllComponents`) change the game's HUD *state* but not what the replay
screen draws. The game's own UI hides everything on a **middle mouse click** (the toolbar's "Toggle UI"
label is just a hint with a mouse-middle icon), so DriverDirector sends that click to the game window
(only after verifying the game window is in front) and confirms via `GET /rest/hud`. Setting "Hide LMU's
replay UI/HUD while recording": inlay clips only (default), whole recording, or never. The UI is always
restored afterwards. Found by reading the game's `Bin/UI.zip` (its web interface source).

## Commentary and voice

Lines are written by the Claude CLI using your Claude Code login (no API key), validated (word budget,
no invented numbers), then voiced. The voice engine is **Chatterbox**, the same open-source model LMU
Director uses: DriverDirector reuses LMU Director's installed runtime (`%LOCALAPPDATA%\LMUTools\LMUDirector\Runtime`
and its `Voices` folder), so there is nothing extra to download. Pick the voice in the panel (e.g.
`british-female-normal` on every line, or `british-female` to match normal/excited/overexcited to the
moment). Edge (online, instant) is the fallback. Chatterbox runs on the CPU here, about 4x slower than
real time, in a separate process that exits afterwards.
Log the CLI in once: `claude.exe auth login --claudeai` (the desktop app's copy lives under
`%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code\<version>\`).

## Leaderboard, telemetry and music

- **Leaderboard**: centred on the focus driver, five cars ahead and five behind (the window slides at the front and
  back of the field). Each row shows the exact time gap to the focus car (the time between the two cars passing the
  same point, from the results timing lines; whole laps for lapped cars) and three sector blocks in timing-screen
  colours: purple = class best, green = own best, orange = slower. Blocks fill in as sectors are crossed; the
  previous lap's colours stay dimmed until then, and hatched blocks are estimates (invalid laps). Rows slide
  smoothly when places change. Rows ahead/behind are set in the panel.
- **Telemetry**: LMU publishes no telemetry while a replay plays (the shared memory is frozen), so DriverDirector
  keeps the game's own speedo on (gear, speed, rpm ring, pedals; `POST hud/toggle/speedo` after the rest of the HUD is
  hidden) for the main pass only, never in the PiP clips. It is the real data for the focus car, in the game's styling.
- **Music**: `music.source = ai` generates an instrumental locally with MusicGen (`facebook/musicgen-small`, run in
  LMU Director's Python, no API key). About 3 minutes per 30 s clip on CPU, done once: the loop is cached by prompt in
  `%APPDATA%\DriverDirector\music` and reused. Use "Prepare music now" to make it ahead of time. `folder` uses your own
  tracks; `off` disables it. The music is looped under the whole video, faded in/out, and dips while the commentator
  speaks (the game sound dips too). Generation needs about 4 GB free RAM, so it runs in the Produce step.

## Cinematic mode (a film of the race, no commentary)

`dd settings '{"cinematic":{"enabled":true}}'` turns the next video into a film instead of a broadcast:
- **Shots:** long TV and trackside shots of the focus driver (`cinematic.shot_s` 14 s on average, `cinematic.weights`
  over the camera groups), with cut-aways to the biggest incidents elsewhere in the field (`cinematic.cutaways`).
- **Slow motion:** the best moments (the driver's own incidents and passes first) are played in the game's own slow
  motion while recording (`VCRCOMMAND_SLOW`, the replay bar's slow button), so they are real frames, not stretched
  video. `cinematic.slow_moments`, `slow_before_s` and `slow_after_s` choose them. The game reports no replay clock,
  so the recorder keeps time with `cinematic.slow_rate`, the speed of that slow motion: 1/8 (0.125), measured by
  matching frames of a slow and a normal-speed recording of the same stretch.
- **No overlays:** no commentary, leaderboard, name card, speedo or picture-in-picture boxes. The overlay source
  (`obs.overlay_source`) is switched off in OBS while recording and back on afterwards.
- **Finishing:** music leads (`cinematic.music_source`, `music_prompt`, `music_level_db` -20) over the game sound
  (`game_level_db` -6). The picture gets a gentle film grade (`grade`: film | none), letterbox bars (`letterbox`
  2.76:1, 0 = none) and fades from and to black (`fade_s`). An opening title shows the event, track and driver
  (`title`, `title_s`).
- **Output:** the video is named `..._cinematic.mp4`.

Turn it off again with `{"cinematic":{"enabled":false}}`.

## Live coaching (moved)

The live overlay and race-engineer coach is now its own app, **StintRadio** (`..\StintRadio`), with its own plugin. It uses this package as a library.

## YouTube

The panel's YouTube section (and `/driverdirector:publish`) uploads a finished video to your channel with the YouTube
Data API: resumable chunked upload (it resumes if the connection drops), title/description drafted from the race with
chapters at each picture-in-picture moment, and the AI voice/music disclosed.

### One-time setup (in your own Google account)

Do every step in **one** Google Cloud project. A client file from another project is the usual cause of
"Access blocked".

1. Open [console.cloud.google.com](https://console.cloud.google.com), then pick or create a project and note its name.
2. **APIs & Services > Library**: enable **YouTube Data API v3**.
3. **Google Auth Platform** (OAuth consent screen): set it up as External, give it any app name, and use your email as
   the contact.
4. **Audience > Test users > Add users**: add the Google account that owns your YouTube channel. This is the step that
   is easy to miss. Without it, Google shows "Access blocked" / "Error 403: access_denied" after you approve.
5. **Clients** (or Credentials) **> Create client > OAuth client ID**, type **Desktop app**, then download the JSON. An
   API key (`AIza...`) does not work: it cannot upload or edit videos.
6. Run `dd youtube setup`. It takes the newest `client_secret*.json` from Downloads, or you can pass a path, and prints
   the file's project id so you can check it matches step 1.
7. Run `dd youtube connect`, or press "Connect YouTube", and approve in your browser within 5 minutes. Google warns
   that the app is unverified; "Continue" is fine for your own test app. `dd youtube` then shows the channel name, and
   `can_edit: true` when DriverDirector may also change descriptions and visibility.

DriverDirector never sees your Google password. It only keeps a refresh token in
`%APPDATA%\DriverDirector\youtube_token.json`; delete it, or press Disconnect, to remove access. If you change the
client file later, connect again.
Uploads are **private** unless you pick otherwise, and nothing is sent without a confirmation (the server refuses an
upload request that lacks `confirm: true`). Google keeps uploads from API projects that have not passed its audit
private whatever is requested; if that happens, set the video public in YouTube Studio. Quota: an upload costs 1,600
of the 10,000 free daily units, a change to an existing video 50; the quota resets at midnight US Pacific time.

## Claude Code plugin

`claude-plugin/` is a Claude Code plugin that sets up and runs the whole process by conversation. It has three skills
and a standard-library helper (`scripts/dd.py`) that talks to the running app, so nothing extra is installed:

- `/driverdirector:setup` checks the app, LMU, OBS, ffmpeg, the voice runtime, the music model and the Claude CLI, and
  starts what is missing.
- `/driverdirector:direct` picks the replay and focus driver, sets options, shows the plan (length and each
  picture-in-picture moment), asks for your OK, then records and produces the video while reporting progress. It never
  starts on its own.
- `/driverdirector:publish` connects YouTube, drafts the title, description and chapters, and uploads only after you
  confirm the channel, title and visibility.
- `/driverdirector:manage` follows or cancels a job, changes settings, finishes a recording that stopped early, and
  prepares the music.

Try it without installing: `claude --plugin-dir "<this folder>\claude-plugin"`. Check it with
`claude plugin validate claude-plugin`. The helper finds the install through `%APPDATA%\DriverDirector\home.txt`
(written each time the app starts) or `DRIVERDIRECTOR_HOME`.

## Memory: record first, produce after

Running the game, OBS, the voice model and the video encode together exhausts 15 GB of RAM. So the run
is two phases: **Record** (game open; the commentary script is written first because that is light) and
**Produce** (voice + video; the game can be closed). If memory is tight when recording ends, the app
stops and tells you to close LMU (there is a Close LMU button) and press **Produce video**. Produce works
from the saved recording, even after restarting the app, and never overwrites an existing video.

## How the data works

During a replay LMU publishes no live telemetry. The plan comes from the race result XML
(`UserData/Log/Results`, exact lap/sector/incident times), matched to the loaded replay by track and
incident-time fingerprint; REST history is the fallback. Seek, focus and cameras use LMU's REST API.
Never send guessed request bodies to that API: a malformed one crashed the game once.

## Known limits

- The game's own replay controls/toolbar stay in the footage (the API can't hide them); switch off
  Standings, Car HUD and Track Map in the game toolbar so they don't clash with our overlay.
- Gaps on the overlay are estimates (+-0.3 s) interpolated from sector crossing times.
- Voicing a whole race on the CPU takes a while (about 4x real time: ~30 lines is roughly 15 minutes).
- OBS-live compositing (boxes shown by OBS during the main pass, no video re-encode) is experimental
  and untested on a real OBS.
- Only a 60 s slice with one incident box has been run end to end so far.
