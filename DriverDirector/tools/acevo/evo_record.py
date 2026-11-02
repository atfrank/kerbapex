"""Record a test clip of the AC Evo replay in the 'AC Evo' OBS scene while cycling camera views with F-keys."""
import base64, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obsws_python as obs
import evo_keys as k

PLAN = [("F3", 8), ("F3", 7), ("F2", 7), ("F1", 7), ("F1", 7), ("F6", 6), ("F3", 8), ("F2", 7)]   # ~57 s
OUT = "D:/Videos/DriverDirector/_tests"
c = obs.ReqClient(host="127.0.0.1", port=4455, timeout=5)
prev_scene = c.get_current_program_scene().current_program_scene_name
prev_dir = c.get_record_directory().record_directory
h = k.find_window()
if not h or not k.front(h):
    raise SystemExit("AC Evo window not found or could not be brought to the front")
c.set_current_program_scene("AC Evo")
time.sleep(1.5)
img = c.get_source_screenshot("AC Evo Capture", "jpg", 640, 268, -1).image_data
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "obs_evo.jpg"), "wb").write(base64.b64decode(img.split(",", 1)[1]))
os.makedirs(OUT, exist_ok=True)
path = ""
try:
    c.set_record_directory(OUT)
    c.start_record()
    time.sleep(2)
    for key, hold in PLAN:
        if k.user32.GetForegroundWindow() != h and not k.front(h):
            print("lost focus; stopping early"); break
        k.press(key)
        print("pressed", key, flush=True)
        time.sleep(hold)
finally:
    try:
        path = c.stop_record().output_path
    except Exception as e:
        print("stop failed:", e)
    c.set_record_directory(prev_dir)
    c.set_current_program_scene(prev_scene)
print("recorded:", path, "| restored scene:", prev_scene, "| record dir:", prev_dir)
