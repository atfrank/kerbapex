---
name: radio
description: Start, stop or check StintRadio, the race engineer on the team radio while the user drives Le Mans Ultimate (braking countdowns, lap debriefs, the corner losing most time and why, habit coaching on gears, short-shifting and braking points, race gaps), change how it talks, or import Coach Dave laps as references. Use when the user wants to be coached or talked to while racing, or asks to see brake traces live.
---

# StintRadio: the race engineer

Helper: `python "${CLAUDE_PLUGIN_ROOT}/scripts/sr.py" <command>` (called `sr` below). It only READS the game's live
data, so it is safe while the user is driving; never load replays or drive the game from here.

1. `sr health`. If the app is not running: `sr start --wait` (the coach then starts by itself as soon as the game's
   live data appears, so it can be started before the game). If it is running: `sr on`.
   For RaceRoom nothing needs switching on (its live data is always there; `live.game` auto picks the running game, and the reference is the user's own best lap). For Le Mans Ultimate the game needs **Enable Plugins** on in its settings (restart the game once after turning it on), or there is no
   live data. The user can also double-click `Start StintRadio.bat` in the StintRadio folder.
2. While driving, `sr state` shows what the coach sees (status, reference lap, delta, latest corner notes).
3. `sr off` stops the coach; `sr quit` closes the app (frees memory).

References: `sr import` stores the fastest whole laps of every car and track from Coach Dave Delta's local session
files (the user's own laps; never Coach Dave's paid or pro content). Laps the user drives are stored too
(`%APPDATA%\StintRadio\laps`). `live.reference`: `leader` (default) uses, in a race, the fastest lap recorded from
the leader of the user's class (recording starts at their next start-line crossing, so about two laps in); otherwise,
and until then, the fastest stored lap of the user's car on that track. `best` always uses that.

What it says (`live.style` `engineer`, the default), only on straights and never close to a braking point:
- after each lap: time and delta ("One forty-three four, two tenths off the leader."), then the costliest corner and
  why ("Most of it in turn five, three tenths. You're braking about fifteen metres early.");
- habits seen in at least two of the last three laps of a corner: wrong gear ("Try second, it'll help the exit"),
  revving out where the reference short-shifts, braking early or late, late throttle; explained once, then a reminder
  a few seconds before that corner, and praise when it is fixed;
- a countdown "brake in four, three, two, one, brake, second gear" for the focus corner (`live.countdown`
  focus | all | off) and "short shift" cues;
- in races: position and gaps every couple of minutes, at once when a car behind gets within about half a second.
`countdown` style is a countdown before every braking point instead.

Race awareness (engineer style, all from the game's live data): fuel to the flag, the last lap to box, the fill when
entering the pits, laps/minutes to go, flags and penalties, tyre wear, rain, and other-class cars closing from behind.
`live.talk` quiet (urgent only) | normal | chatty.
Stint plan: when the fuel will not reach the flag, the engineer says the stops ("Plan: two stops. First at the end of
lap fourteen, thirty-eight litres, then lap thirty-three"), replans every lap from measured use ("Plan has changed"), and
calls "Box this lap. Fill ..." on the lap to come in. Ask "what's the plan?" or set the job topic `plan`. Fuel only: it
does not know pit-lane time, tyre changes or mandatory stops.

Push-to-talk (off by default; `live.ptt` true, `live.ptt_button` a key like rctrl/f9 or a wheel button joy1:5,
`live.ptt_mic` blank = first microphone, `live.ptt_language` en/nl): hold the button, ask ("how much fuel do I have?"),
let go. The recording goes to ElevenLabs speech-to-text (same ELEVENLABS_API_KEY as the voice) and is deleted; only the
question and a list of live facts go to the Claude CLI, which answers on the radio. Say so when enabling it.

Jobs by voice (needs push-to-talk): "check my tyres in three laps" (topics tyres, fuel, position, weather, time,
penalties; in n laps or n minutes) and "tell me when it rains / yellow / fuel under five laps / tyres past seventy percent /
the gap behind drops under a second". Only these fixed shapes are accepted (max 8 at once); anything else is refused
aloud. Memory: "remember that I want no tyre talk before lap five" / "forget that" keeps short notes in
%APPDATA%/StintRadio/memory.json, with each car and track's last best lap; both are given to Claude with the live facts.

Voice: `live.voice_engine` `elevenlabs` (the user's own clone, `voice.elevenlabs_voice_id`; sentences made with
`live.engineer_model`, default `eleven_multilingual_v2`, and cached; key only from ELEVENLABS_API_KEY in the
environment, never ask for it or type it) or `windows` (offline). `live.radio_effect` adds the team-radio sound;
`live.voice_boost_db` sets loudness. Change with e.g. `sr settings '{"live": {"countdown": "all"}}'`; settings apply
from the next `sr on`. Never use he/she about the user or other drivers.

Page: `http://127.0.0.1:6420/` shows the delta, the next ~450 m with the reference's brake and throttle, the last
10 seconds of pedals and the corner notes. In exclusive fullscreen only the Steam overlay (Shift+Tab > web browser >
pin) can show it; the user mostly relies on the voice.

Try it without driving: `sr replay "<track>" "<car>" 1 <track length m>` plays the second-fastest stored lap through
the coach in real time (Coach Dave laps need the track length, e.g. Laguna Seca 3602).

Force feedback (RaceRoom): `sr ffb` shows the verdict (clipping, too light) and the FFB Strength change it would make. Only after the user agrees, with RaceRoom closed, `sr ffb apply --yes` writes it to the wheel profile (backup first). Never run apply on your own.
