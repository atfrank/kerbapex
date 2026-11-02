# Building and packaging StintRadio

Needs Python 3.11+, and DriverDirector checked out next to this folder (StintRadio imports it as a library).

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ..\DriverDirector -e .[dev] pyinstaller
powershell -File packaging\build.ps1
```

`build.ps1` runs the tests, regenerates `docs/SETTINGS.md`, builds `dist\KerbApex` with PyInstaller (`packaging/stintradio.spec`): `KerbApex.exe` (the control panel) and `StintRadio.exe` (the engine,
dashboards and overlay) sharing one set of libraries, both windowless and with the KerbApex icon. It zips that to
`release\KerbApex-<version>-win64.zip` and, if Inno Setup 6 is installed, builds `release\KerbApex-Setup-<version>.exe` from
`packaging/installer.iss`. `python packaging/check_panel.py` presses every button of the panel against a throwaway copy first.
The shortcut and icon: `packaging/make_icon.py`, `packaging/make_shortcut.ps1`.

Version: `stintradio/__init__.py` (also `pyproject.toml` and `plugin/.claude-plugin/plugin.json`). Add a `CHANGELOG.md` entry.

## Before selling

- Code-sign `StintRadio.exe` and the installer, or Windows SmartScreen will warn buyers.
- Decide on FFmpeg: the app finds one on the PC; bundling it needs its licence terms (LGPL build) in the notices.
- Confirm each game publisher's terms allow a paid tool that reads their live data (RaceRoom's and LMU's shared memory).
- The Coach Dave Delta importer reads that product's local files: leave it out of a commercial build unless the owner agrees.
- Packaged builds do not yet support the Claude plugin's `sr start` (it launches the Python install); use the Start menu.

## Before sharing it publicly

Things only the owner can decide or do (everything else is in the repository):

- **Licence:** MIT (`LICENSE` at the top, copied to `StintRadio/LICENSE.txt` for the installer).
- **DriverDirector:** StintRadio imports it as a library, so a source release needs it too. Its test fixtures use made-up names.
- **Coach Dave Delta import:** `sr import` reads another product's local files. Remove it (and `docs/REFERENCE.md`'s mention) unless its
  owner agrees.
- **Game terms:** check that RaceRoom's and Le Mans Ultimate's terms allow a free tool reading their live data (both publish a shared-memory
  interface for exactly this).
- **Windows warnings:** an unsigned program triggers SmartScreen. Sign `KerbApex.exe`, `StintRadio.exe` and the installer, or tell users to
  choose "More info > Run anyway".
- **ffmpeg:** the download does not include it (licence); the setup check tells users how to install it.
- **Voice cloning:** say in the page that users must only clone a voice they have the right to use.
