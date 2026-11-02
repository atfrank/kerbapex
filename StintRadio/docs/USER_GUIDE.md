# StintRadio User Guide

## 0. The KerbApex control panel

Double-click **Start KerbApex.bat** (or run `python -m stintradio gui`). One window to run everything:

- **Control:** live status of StintRadio, the coach, the game, the overlay and the voice; Start and Stop for each, or
  Start everything / Stop everything; Move / resize for the overlay; buttons for the dashboards and the laptop address.
- **Overlay:** the look of the overlay window over the game, all remembered: shape (wide, square, tall, banner), size in pixels or
  bigger / smaller, which corner of the game window and how far in, dark or light, the KerbApex border, how solid it is, and which window
  (the see-through one or the browser one). A button unlocks it for dragging. The hotkeys still work and the tab follows them.
- **Settings:** the common options (game, voice, style, how much the engineer says, countdown, coaching laps, reference,
  push-to-talk, laptop page, recording). Save writes them; Save and restart coach applies them at once.
  When StintRadio is not running the panel edits the settings file directly.
- **Tools:** driving suggestions (brake bias, shift points, minimum speeds), force-feedback advice, the Coach Dave lap import,
  "Check my setup" (what is missing and how to fix it), a two-minute check of which program makes a sound, and the improvement
  rounds (a headless Claude session on its own branch; nothing is merged without you).
- **Logs:** `stintradio.log` and the sound-by-sound `audio.log`, refreshed as you watch.

The panel can be closed at any time: it only talks to StintRadio and never stops it by itself.

## 1. First-time setup

1. **Install** StintRadio and start it from the Start menu. A console window opens; leave it running. The dashboard
   is at <http://127.0.0.1:6420/>.
2. **RaceRoom:** nothing to change. Its live data is always on.
   **Le Mans Ultimate:** in the game's settings turn **Enable Plugins** on, then restart the game once.
3. **Voice (optional but recommended):**
   - Set the environment variable `ELEVENLABS_API_KEY` to your ElevenLabs key (Windows: Settings > System > About >
     Advanced system settings > Environment Variables). The key is read only from there; StintRadio never stores it.
   - Put your voice id in `%APPDATA%\StintRadio\settings.json` under `voice.elevenlabs_voice_id`, and set
     `live.voice_engine` to `elevenlabs`.
   - Without a key the offline Windows voice is used when `voice_engine` is `windows`. If you chose `elevenlabs`
     and it is unavailable, the engineer stays quiet rather than switching to a computer voice.
4. **Your name:** set `live.driver_name`, or leave it blank to use the name from the game (RaceRoom).

## 2. A session

Start the game, go onto the track. When the game's data appears the coach status on the dashboard changes from
*waiting* to *on*.

**References.** The engineer compares each corner with a reference lap:
- in a race, the fastest lap anyone has set so far, once it is faster than your own reference (`live.reference: leader`);
- otherwise your own best lap on that car and track. Laps you drive are stored automatically.

The first laps on a new car and track have no reference. Drive a few clean laps; the engineer starts coaching when it has one.

**What you hear** (only on straights, never close to a braking point):
- After a lap: the time, the gap, and the corner that cost the most with the reason.
- For the first `live.coach_laps` laps (default 5): full coaching. After that it speaks when something is worth
  saying: a new best, a lap that changed by 0.4 s or more, a big loss, a habit that is still there.
- "Nailed it" calls when a corner matches the reference, with what was right.
- In races: your position and gaps, drivers around you by name, battle calls ("Maria is taking two tenths from you in the chicane").
- Braking countdowns for the corner you are working on (`live.countdown`: focus, all or off).

## 2b. Before a session, and in the pits

- **Briefing.** When a new session starts (practice, qualifying, a race, the next race of the weekend, or the same one restarted)
  and you are on the grid or in the pit lane, the engineer gives a short briefing, once: the lap to beat, and the one to three corners
  where your earlier runs here lost the most time, with the habit behind each ("at turn five you tend to brake about ten metres early,
  so go a touch deeper"). It is advice for laps you have not driven yet. With little history here it says so and starts pointers once it
  has seen a few laps. Switch it off with `live.briefing` ("Briefing before a session" in the panel's Settings tab). **Tools > Session
  briefing** shows what it would say now, and `sr briefing race say` speaks it, without driving.
- **A new session starts fresh.** Laps, notes and calls of the last session are forgotten.
- **In the pits.** While the car is in the pit lane the engineer does not judge corners or count down braking points, and a lap that
  went through the pit lane is not judged, saved as a reference or debriefed. In a race it says when you are in ("the tyres are about
  thirty percent worn") and, after a stop, that the next lap is an out-lap. In practice and qualifying it just stays quiet.

## 3. Asking questions

Enable push-to-talk (`live.ptt: true`). Press the button (default right Ctrl, or a wheel button such as `joy1:5`), ask,
press it again. Examples:

- "Do I need to pit?" / "What is my pit strategy?" — fuel and tyres to the flag, the window, a recommendation.
- "Where am I losing time?" — the biggest corner losses.
- "How are my tyres?" / "How much fuel?"
- "Forget that" — removes a note the engineer keeps.

Answers are short, spoken, and use only measured data. Where something is unknown (rival pit stops, compound
choices, pit-lane time loss), it says so.

## 4. Driving tips and setup experiments

- **Tips** (`Suggestions` on the dashboard, or `sr tips`): brake bias direction from lock-ups, shift points per gear,
  minimum speeds per corner, from your recorded laps. Brake bias is advised as a direction only (towards the rear or
  front); the in-game scale is not interpreted.
- **Experiments:** when you change brake bias, ABS, traction control, engine map or engine braking and leave it for
  two seconds, an experiment starts. The engineer compares up to three clean laps before with two after, then says
  keep, revert, mixed or no difference. It can also propose one: "For the next lap, try brake bias a click rearward."

## 5. Dashboards

- `http://127.0.0.1:6420/`: delta, speed and gear, pedals, the next braking point, corner notes, suggestions.
  In exclusive-fullscreen use the Steam overlay: Shift+Tab, web browser, enter the address, pin it.
- The overlay over a windowed or borderless game: the control panel's Overlay Start, or `python -m stintradio overlay`. Tick
  **See-through (native, new)** in the panel, or add `--engine native`, for a window with no background at all: only the map,
  delta, sector chips and border are drawn, and the game shows everywhere else. The hotkeys and saved layout are the same.
- `http://<this PC's address>:6420/race`: a read-only race page for a laptop or phone (gaps, position, fuel, tyres,
  experiment status). Turn on `live.lan` and allow StintRadio through the Windows firewall for private networks.
  Other devices can open only this page.
- Both pages keep the last data on screen when the game closes, with a "last data" banner.

## 6. Your data

Everything is stored in `%APPDATA%\StintRadio`: settings, saved laps, corner maps, session recordings (SQLite, kept
60 days or 5 GB), cached voice clips, and a log. See [Privacy](PRIVACY.md).

## 7. Command line and Claude plugin

`sr` (in the install folder, `plugin\scripts\sr.py`) controls a running app: `health`, `on`, `off`, `state`, `tips`,
`settings`, `import`, `replay`, `quit`. The Claude plugin adds a `radio` skill and read-only data tools; see the
[technical reference](REFERENCE.md).


## Studio: videos of your races

The **Studio** tab lists your RaceRoom replays. Pick one and press **Make the video**: KerbApex restarts the game, plays the replay through
twice (once for the cockpit picture, once for the other camera views shown in a box), records it through OBS and builds the finished video.
A 16-minute race takes about 45 minutes, and **the PC must be left alone** while it runs. The live engineer is stopped for that time.
**Upload as a private draft** sends it to your YouTube channel (connected once in DriverDirector); you make it public yourself.
**With the KerbApex overlay and the engineer in the video** (the tick box, on by default): KerbApex replays what it recorded while you drove that race, in step with the game's replay, so the video shows the overlay (track map, delta, next turn, the corners to work on) and you hear the engineer at the same moments as in the race. This needs the race to have been driven with KerbApex running (its recording is kept in `%APPDATA%\StintRadio\sessions`); a race without a recording is made without them, and the log says so. The box of other views carries neither. **Make the last 5 races** does the newest five in a row (about four hours). Set your name as the driver in DriverDirector's `raceroom.driver` setting so the video is about you. Studio needs OBS and ffmpeg and currently runs
from the source download, not the installer.
