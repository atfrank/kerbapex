# Third-party notices

StintRadio includes or uses the following open-source components. Each is under its own licence; the full texts ship
with the packages (in the installed `licenses` folder).

| Component | Licence |
|---|---|
| FastAPI 0.162 | MIT |
| Starlette 1.10 | BSD-3-Clause |
| Uvicorn 0.54 | BSD-3-Clause |
| Pydantic 2.14 | MIT |
| AnyIO 4.15 | MIT |
| h11 0.16 | MIT |
| httpx 0.28 | BSD-3-Clause |
| Click 8.5 | BSD-3-Clause |
| httptools 0.9 | MIT |
| websockets 17.3 | BSD-3-Clause |
| watchfiles 1.3 | MIT |
| PyYAML 6.0 | MIT |
| python-dotenv 1.2 | BSD-3-Clause |
| certifi 2026.10 | MPL-2.0 |
| Python 3.11+ runtime | PSF License |
| PyInstaller (bootloader) | GPL-2.0 with the bootloader exception (permits use in commercial programs) |

FFmpeg (used to capture the microphone and process audio) is a separate program under LGPL/GPL depending on the build;
it is not modified. See <https://ffmpeg.org/legal.html>.

Services used only when you enable them: ElevenLabs (voice) and Anthropic's Claude CLI (questions), under your own
accounts and their terms.
