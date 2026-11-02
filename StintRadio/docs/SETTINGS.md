# Settings reference

Generated from `stintradio/config.py` by `packaging/gen_settings_doc.py`. Settings live in `%APPDATA%\StintRadio\settings.json`; restart StintRadio after editing.

## `live`

| Option | Default | Meaning |
|---|---|---|
| `game` | `auto` | auto (whichever installed sim is running) / raceroom / lmu / demo / the id of any sim adapter you added |
| `game_dir` | `""` | Le Mans Ultimate install folder; blank = find it in the Steam libraries |
| `reference` | `leader` | leader: in races, the time to beat is the fastest lap anyone has set (RaceRoom), or the fastest recorded lap of your class leader (LMU) / best: always your own best lap (or a Coach Dave lap) |
| `style` | `engineer` | engineer: race-engineer radio (lap debrief, one focus corner, race gaps), on the straights only / countdown: a countdown before every braking point |
| `countdown` | `focus` | engineer style: focus (only the corner to work on) / all / off |
| `talk` | `normal` | how much the engineer says unprompted: quiet (urgent calls only) / normal / chatty |
| `corner_guide` | `3` | engineer style: name and describe every corner before it ("Turn three, fast right.") for this many laps once a reference lap exists, for a driver who does not know the track yet; 0 = off |
| `briefing` | `True` | engineer style: before each session (practice, qualifying, race) a short briefing from your earlier runs here: the lap to beat and the corners to work on, said as advice for laps not yet driven |
| `coach_laps` | `5` | engineer style: full coaching for this many laps, then only what is needed (a lap time that changed, a big loss, a habit still there) plus a call when a corner is nailed |
| `driver_name` | `""` | what the engineer calls you ("Alex, try to brake a bit later"); blank = your name in the game (RaceRoom) |
| `throttle_cue` | `focus` | "throttle" where the reference gets back on the power: focus (the corner to work on and the guide laps) / all / off |
| `ffb_profile` | `""` | the RaceRoom wheel profile (ControlSet\\NAME.rcs) whose FFB Strength may be changed; blank = the only profile with a real FFB device |
| `record` | `True` | RaceRoom: write the whole live data stream to a session file (%APPDATA%\StintRadio\sessions) |
| `record_hz` | `10.0` | ... this many rows a second |
| `ffb_advice` | `True` | RaceRoom: say if the force feedback strength looks too high (clipping) or too low |
| `ask_tools` | `True` | spoken questions: Claude may query the recorded data stream and saved laps (slower, ~10-25 s) |
| `ptt` | `False` | push-to-talk: hold the button, ask the engineer something, hear the answer |
| `ptt_button` | `rctrl` | a key (f9, rctrl, space...) or a wheel button (joy1:5 = device 1, button 5) |
| `ptt_mode` | `toggle` | toggle: press once to start asking, press again to stop (hands stay on the wheel) / hold |
| `ptt_max_s` | `12.0` | toggle mode stops by itself after this many seconds |
| `ptt_beep` | `True` | a short tone when recording starts (high) and stops (low) |
| `ptt_mic` | `""` | microphone name as Windows lists it; blank = the first one found |
| `ptt_language` | `""` | spoken language code for the question (en, nl); blank = detect |
| `radio_effect` | `True` | team-radio sound on the engineer's calls |
| `engineer_model` | `eleven_multilingual_v2` | ElevenLabs model for the engineer's sentences (truest to a cloned voice) |
| `voice` | `True` | say "brake in four, three, two, one, brake, second gear" before each braking point |
| `countdown_from` | `4` | seconds before the reference's braking point the countdown starts (1..6) |
| `speak_gear` | `True` | after "brake", the gear the reference lap used in the corner |
| `short_shift` | `True` | "short shift" where the reference changes up early out of a corner |
| `voice_engine` | `windows` | windows (offline) / elevenlabs (voice.elevenlabs_voice_id; phrases are made once with ELEVENLABS_API_KEY and cached) |
| `lan` | `False` | let other devices on the home network (a laptop) open the read-only race page /race |
| `radio_engine` | `""` | voice for the engineer's sentences: chatterbox (free, on this PC, cloned from your countdown words) / elevenlabs / windows; blank = the same as voice_engine |
| `chatterbox_voice` | `""` | a reference WAV of your voice for Chatterbox; blank = made from the countdown clips |
| `chatterbox_threads` | `4` | CPU threads Chatterbox may use (low, so the game keeps its frames) |
| `voice_rate` | `3` | Windows voice speed (-10..10) |
| `voice_boost_db` | `12.0` | how much the cues are compressed and lifted (louder over the engine; 0 = as spoken) |
| `voice_drive_db` | `6.0` | a second stage on every cue: hard compression and soft clipping (about 5 dB louder; 0 = off) |
| `countdown_variety` | `True` | vary the countdowns (full, short, just "brake") and the reminders instead of the same every lap |

## `voice`

| Option | Default | Meaning |
|---|---|---|
| `elevenlabs_voice_id` | `JBFqnCBsd6RMkjVDRZzb` | the voice in ElevenLabs' own examples |
| `elevenlabs_model` | `eleven_multilingual_v2` | for the fixed countdown phrases |

## Top level

| Option | Default | Meaning |
|---|---|---|
| `port` | `6420` | The port of the dashboard and API. |
