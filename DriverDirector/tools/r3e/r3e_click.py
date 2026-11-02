"""Usage: python r3e_click.py OUTDIR X Y [X Y ...]  (coordinates from a screenshot, scale 3.5) - clicks, then screenshots after each."""
import os, sys, time
import r3e_keys as k
out = sys.argv[1]; os.makedirs(out, exist_ok=True)
h = k.find_window()
if not h or not k.front(h):
    raise SystemExit("RaceRoom not found / not in front")
a = sys.argv[2:]
for i in range(0, len(a), 2):
    k.click(float(a[i]) * 3.5, float(a[i + 1]) * 3.5)
    time.sleep(2)
    k.shot(os.path.join(out, f"c{i//2}.jpg"))
