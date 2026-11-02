"""Create (or refresh) an 'AC Evo' scene in OBS: a game capture of the AC Evo window scaled to the canvas, plus the
existing desktop-audio source shared from the LMU setup. Existing LMU scenes/sources are not modified."""
import obsws_python as obs

SCENE, CAP, AUDIO = "AC Evo", "AC Evo Capture", "LMU Game Audio"
c = obs.ReqClient(host="127.0.0.1", port=4455, timeout=5)
scenes = [s["sceneName"] for s in c.get_scene_list().scenes]
print("scenes before:", scenes, "| program:", c.get_current_program_scene().current_program_scene_name)
if SCENE not in scenes:
    c.create_scene(SCENE)
inputs = [i["inputName"] for i in c.get_input_list().inputs]
items = [i["sourceName"] for i in c.get_scene_item_list(SCENE).scene_items]
if CAP not in inputs:
    c.create_input(SCENE, CAP, "game_capture",
                   {"capture_mode": "window", "window": "Assetto Corsa EVO:acevo:AssettoCorsaEVO.exe", "priority": 2,
                    "capture_cursor": False, "capture_overlays": False}, True)
elif CAP not in items:
    c.create_scene_item(SCENE, CAP, True)
if AUDIO in inputs and AUDIO not in items:
    c.create_scene_item(SCENE, AUDIO, True)
v = c.get_video_settings()
item = c.get_scene_item_id(SCENE, CAP).scene_item_id
c.set_scene_item_transform(SCENE, item, {"boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0,
                                         "boundsWidth": float(v.base_width), "boundsHeight": float(v.base_height),
                                         "positionX": v.base_width / 2, "positionY": v.base_height / 2, "alignment": 0})
print("scene items now:", [i["sourceName"] for i in c.get_scene_item_list(SCENE).scene_items], "canvas", v.base_width, v.base_height)
