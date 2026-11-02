"""audiomon.py [seconds] [out.log]: which program is making sound, and when. Windows keeps a level meter for every program
that has an audio session; this reads them ~20 times a second and logs when each one starts and stops being audible.
Read-only: it never changes any volume or setting."""
import sys
import time

import psutil
from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60
out = open(sys.argv[2], "a", encoding="utf-8", buffering=1) if len(sys.argv) > 2 else sys.stdout
THRESH = 0.004
state: dict = {}                      # pid -> (name, audible, started)
t_end = time.time() + secs


def stamp() -> str:
    t = time.time()
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"


def log(line: str) -> None:
    out.write(f"{stamp()} {line}\n")


log("monitor start")
seen_names = {}
while time.time() < t_end:
    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception as e:                                    # noqa: BLE001
        log(f"enumeration failed: {e}")
        time.sleep(1)
        continue
    now_audible = {}
    for s in sessions:
        pid = s.ProcessId
        if not pid:
            continue
        try:
            name = psutil.Process(pid).name()
        except psutil.Error:
            name = f"pid{pid}"
        seen_names[pid] = name
        try:
            peak = s._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue()
        except Exception:                                     # noqa: BLE001
            peak = 0.0
        if peak > THRESH:
            now_audible[pid] = (name, peak)
    for pid, (name, peak) in now_audible.items():
        if pid not in state:
            state[pid] = (name, time.time())
            log(f"SOUND  {name} (pid {pid}) starts, level {peak:.3f}")
    for pid in list(state):
        if pid not in now_audible:
            name, t0 = state.pop(pid)
            log(f"quiet  {name} (pid {pid}) after {time.time() - t0:.2f}s")
    time.sleep(0.05)
log("sessions seen: " + ", ".join(sorted(set(seen_names.values()))))
log("monitor end")
