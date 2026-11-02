# Privacy

StintRadio runs on your PC. It has no account, no telemetry and no analytics.

**Stays on your PC** (`%APPDATA%\StintRadio`): settings, saved laps, recordings of the game's data, cached voice
clips, logs. Delete the folder to remove everything.

**Sent to other services only if you turn them on:**

| Service | When | What is sent |
|---|---|---|
| ElevenLabs | `voice_engine` or `radio_engine` is `elevenlabs` | The text of each sentence to be spoken (once per sentence; clips are cached). Uses your own key from `ELEVENLABS_API_KEY`, under your ElevenLabs terms. |
| Claude (Anthropic) CLI | Spoken free-form questions (`live.ask_tools`) | Your transcribed question and the relevant session facts or query results, via the Claude CLI you are signed in to, under your Anthropic terms. |

The dashboards are served only to this PC, unless you set `live.lan`; then the read-only `/race` page is reachable by
devices on your network. It contains no personal data beyond driver names the game reports.

Microphone audio is used for a question and is not saved.
