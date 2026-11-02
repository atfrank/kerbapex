"""With the AC Evo replay paused, press each camera key repeatedly and count the distinct views until the cycle repeats.
Usage: python evo_cycle.py OUTDIR KEY [KEY ...]"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evo_keys as k
from imgdiff import diff

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
h = k.find_window()
if not h or not k.front(h):
    raise SystemExit("AC Evo not in front")
MAXN, SAME = 20, 1.5
result = {}
for key in sys.argv[2:]:
    shots = []
    for n in range(MAXN):
        if k.user32.GetForegroundWindow() != h and not k.front(h):
            raise SystemExit("lost focus: something else took the screen")
        k.press(key)
        time.sleep(1.2)
        p = os.path.join(out, f"{key}_{n:02d}.jpg")
        k.shot(p)
        shots.append(p)
        if n >= 1 and diff(p, shots[0]) < SAME:          # back to the first view of this key
            break
        if n >= 1 and diff(p, shots[-2]) < SAME:          # key did nothing (single view or no-op)
            break
    views = shots[:-1] if len(shots) > 1 else shots
    result[key] = {"views": len(views), "cycled": len(shots) < MAXN, "files": [os.path.basename(v) for v in views]}
    print(key, result[key]["views"], "views", "(cycle closed)" if result[key]["cycled"] else "(no repeat seen)", flush=True)
json.dump(result, open(os.path.join(out, "cycles.json"), "w"), indent=1)
