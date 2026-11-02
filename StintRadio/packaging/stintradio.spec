# PyInstaller spec: one folder, dist/KerbApex, holding two windowless programs that share one set of libraries:
#   KerbApex.exe     the control panel (what you double-click)
#   StintRadio.exe   the engineer, the dashboards and the overlay (the panel starts it; `StintRadio.exe overlay` is the overlay)
# Run from the StintRadio folder via packaging/build.ps1.
from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("stintradio.sims") + collect_submodules("uvicorn") + collect_submodules("driverdirector") + ["pydantic.deprecated.decorator"]
common = dict(
    pathex=["..", "../../DriverDirector"],
    hiddenimports=hidden,
    excludes=["pytest"],
)
datas = [("../stintradio/web", "stintradio/web"),
         ("../assets", "assets"),
         ("../plugin", "plugin"),
         ("../docs", "docs"),
         ("../README.md", "."), ("../LICENSE.txt", "."), ("../THIRD_PARTY_NOTICES.md", "."), ("../CHANGELOG.md", ".")]
icon = "../assets/kerbapex.ico"

engine = Analysis(["launcher.py"], datas=datas, **common)
panel = Analysis(["panel_launcher.py"], datas=datas, **common)
MERGE((engine, "StintRadio", "StintRadio"), (panel, "KerbApex", "KerbApex"))

exe_engine = EXE(PYZ(engine.pure), engine.scripts, [], exclude_binaries=True, name="StintRadio", console=False, icon=icon)
exe_panel = EXE(PYZ(panel.pure), panel.scripts, [], exclude_binaries=True, name="KerbApex", console=False, icon=icon)
coll = COLLECT(exe_panel, panel.binaries, panel.datas, exe_engine, engine.binaries, engine.datas, name="KerbApex")
