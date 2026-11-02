# Troubleshooting

First look: the dashboard at <http://127.0.0.1:6420/> and the log at `%APPDATA%\StintRadio\stintradio.log`.

| Symptom | Cause and fix |
|---|---|
| Coach status stays **waiting** | The game's live data is not there. RaceRoom: be on track (not in the menu). Le Mans Ultimate: turn **Enable Plugins** on and restart the game. |
| **Nothing is said** | No reference lap yet (drive a few clean laps); or `live.voice_engine` is `elevenlabs` and the key is missing, which keeps the engineer silent on purpose. Check `ELEVENLABS_API_KEY` and the log. Restart StintRadio after setting it. |
| **A different voice** than your clone | The settings file was not read (look for "settings.json could not be read" in the log), so defaults were used. Fix the JSON, delete the `eleven_*` folders in `%APPDATA%\StintRadio\live_voice`, restart. |
| **A computer voice** speaks | Only when `voice_engine` is `windows`. Set it to `elevenlabs` in `settings.json` and restart. |
| Spoken questions do nothing | Turn on `live.ptt`; check the button name and the microphone (`live.ptt_mic`). Free-form answers need the Claude CLI signed in; otherwise built-in answers are used. |
| Laptop cannot open `/race` | `live.lan` must be true; allow StintRadio (Python) through the Windows Defender firewall on private networks; use the PC's address, not `127.0.0.1`. |
| Dashboard says "last data" | The game is closed or paused; the page shows the final snapshot on purpose. |
| Tyre wear looks inverted | The page shows percent **worn**. If a car reports wear the other way round, tell us the car. |
| Stops mid-session / high CPU | Close other heavy tools; lower `live.record_hz` (default 10). |
| Port 6420 in use | Another StintRadio is running (`sr quit`), or change `port` in `settings.json`. |

Settings changes apply after restart. To collect information for support, send `stintradio.log` and the output of
`sr health` (neither contains your ElevenLabs key).
