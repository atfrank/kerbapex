"""Ask the engineer something: the spoken question is transcribed (ElevenLabs speech-to-text, with the same key as the
voice), Claude answers from a list of facts taken from the live data, and the answer goes out on the radio.

Privacy: the recording is sent to ElevenLabs and deleted afterwards; only the question and the facts list below (no
audio, no account details) go to Claude, through the signed-in Claude CLI, so no API key is needed for that part."""
from __future__ import annotations

import json
import re
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import jobs

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
STT_MODEL = "scribe_v1"
MAX_WORDS = 60

SYSTEM = (
    "You are the driver's race engineer, talking to them on the team radio while they drive. Answer the question in one "
    "or two short spoken sentences, calm and direct, like a real engineer. Use ONLY the facts below. If the facts do not "
    "contain the answer, say you do not have that. Never invent numbers. Write numbers the way they are spoken (lap times "
    "like 'one forty-three four'). No lists, no markdown, no emoji.\n\n"
    "Answer with ONE JSON object and nothing else: "
    '{"reply": "<what you say>", "job": null, "remember": null, "forget": null}.\n'
    "job: only when the driver asks you to do something later or to watch for something; one of "
    '{"when":"laps","n":3,"topic":"tyres"} (report in n laps), {"when":"minutes","n":10,"topic":"fuel"} (report in n '
    "minutes), topics: " + ", ".join(jobs.TOPICS) + '; or {"when":"when","cond":"rain"} (tell me when it happens), '
    'conditions: "rain", "yellow", or with a value: "fuel_laps_below" (laps of fuel), "tyres_worn_above" (percent), '
    '"gap_behind_below" / "gap_ahead_below" (seconds). If the request fits none of these, say so in reply and use null. '
    "reply then confirms the job in a few words.\n"
    "remember: a short note, only when the driver explicitly asks you to remember something. forget: words from a "
    "note the driver asks you to forget. Otherwise null.\n"
    "PIT STRATEGY: when the driver asks about a pit stop, strategy, tyres or fuel, use the PIT STRATEGY facts. Say whether "
    "a stop is needed (fuel and tyres), which lap to box, what to do at the stop (litres, tyres), and, in a few words, what "
    "you do not know. Always give a recommendation when the facts allow it."
)


@dataclass
class Answer:
    reply: str
    job: Optional[dict] = None
    remember: Optional[str] = None
    forget: Optional[str] = None
    question: str = ""
    command: Optional[str] = None     # "coaching_on" | "coaching_off": done by the program, not by Claude


OFF = re.compile(r"\b(stop|pause|no more|turn off|switch off|disable|mute|cancel|end|quit)\b.{0,25}\bcoach", re.I)
ON = re.compile(r"\b(start|resume|restart|turn on|switch on|enable|begin|continue|carry on|bring back)\b.{0,25}\bcoach"
                r"|\bcoach me\b", re.I)


def parse_command(text: str) -> Optional[str]:
    """"stop coaching" / "start coaching" and the like, recognised here so it works at once and without Claude. The
    first of the two words in the sentence wins ("stop coaching, start the race" is off)."""
    off, on = OFF.search(text), ON.search(text)
    if off and on:
        return "coaching_off" if off.start() < on.start() else "coaching_on"
    return "coaching_off" if off else ("coaching_on" if on else None)


def transcribe(path: Path, key: str, language: str = "", post=None) -> str:
    """The words in the clip. `post(url, headers, body)` returns the response bytes (replaceable in tests)."""
    boundary = uuid.uuid4().hex
    fields = {"model_id": STT_MODEL, **({"language_code": language} if language else {})}
    body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                    for k, v in fields.items())
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="ask.wav"\r\n'
             f"Content-Type: audio/wav\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    headers = {"xi-api-key": key, "Content-Type": f"multipart/form-data; boundary={boundary}"}
    if post is None:
        def post(url, hdrs, data):
            with urllib.request.urlopen(urllib.request.Request(url, data, hdrs, method="POST"), timeout=15) as r:
                return r.read()
    return str(json.loads(post(STT_URL, headers, body)).get("text", "")).strip()


def facts(svc) -> str:
    """What the engineer knows right now, as plain lines, from the service's last readings."""
    from .engineer import say_laptime
    lines = []
    s, snap, w = svc.sample, getattr(svc, "snap", None), getattr(svc, "watch", None)
    if s is not None:
        lines.append(f"Track {s.track}, car {s.car}, lap {s.lap}, speed {s.speed:.0f} km/h, gear {s.gear}.")
        if s.last_lap > 0:
            lines.append(f"Last lap {say_laptime(s.last_lap)}.")
        if s.best_lap > 0:
            lines.append(f"Best lap {say_laptime(s.best_lap)}.")
    ref = svc.ref_info or {}
    if ref.get("time"):
        lines.append(f"Reference lap {say_laptime(ref['time'])} ({ref.get('source', 'reference')}"
                     f"{', ' + ref['driver'] if ref.get('driver') else ''}).")
    if snap:
        me = snap["me"]
        lines.append(f"Position {me['place']} of {len(snap['cars'])}, class {me['cls']}, {me['laps']} laps completed, "
                     f"{'in the pits' if me['in_pits'] else 'on track'}, penalties {me['penalties']}.")
        lines.append(f"Fuel {snap['fuel']:.1f} litres of {snap['fuel_cap']:.0f}. Tyres {snap['tyre'] or 'unknown'}.")
        lines.append(f"Rain {snap['raining']:.0%}, track wetness {snap['wetness']:.0%}. Session phase {snap['phase']}"
                     f" (5 green, 6 full course yellow).")
        if w is not None:
            use, togo = w.per_lap(), w.laps_to_go(snap)
            if use:
                lines.append(f"Fuel use {use:.2f} litres a lap, so {snap['fuel'] / use:.1f} laps in the tank.")
            if togo is not None:
                lines.append(f"About {togo:.1f} laps to go.")
            if use and togo is not None:
                from .plan import make_plan, say_plan
                lines.append("Stint plan: " + say_plan(make_plan(snap["me"]["laps"], togo, snap["fuel"], snap["fuel_cap"], use)))
    pit = pit_facts(svc, snap, w)
    if pit:
        lines.append(pit)
    g = getattr(svc, "gaps", None)
    if g:
        a, b = g[1], g[2]
        lines.append(f"Live gaps: place {g[0]}; car ahead {'%.1f s' % a if a is not None else 'none close'}; "
                     f"car behind {'%.1f s' % b if b is not None else 'none close'}.")
    if snap and snap.get("end_t", -1) > 0:
        lines.append(f"Time left in the session: {max(0, (snap['end_t'] - snap['t']) / 60):.0f} minutes.")
    coach = getattr(svc, "coach", None)
    if coach is not None and coach.corners:
        lines.append("CORNERS (numbered from the start line, slow corners only; the reference is the driver's best lap):")
        hist = getattr(getattr(svc, "engineer", None), "history", None) or {}
        for c in coach.corners:
            lines.append(corner_line(coach, c, hist.get(c.n) or []))
    if s is not None:
        extra = []
        if getattr(s, "fuel", -1) >= 0:
            extra.append(f"fuel {s.fuel:.1f} litres")
        if getattr(s, "tyre_temp", ()):
            extra.append("tyre temperatures front left %d, front right %d, rear left %d, rear right %d degrees" % tuple(s.tyre_temp))
        if s.rpm:
            extra.append(f"{s.rpm:.0f} rpm")
        extra.append(f"throttle {s.throttle:.0f} percent, brake {s.brake:.0f} percent, steering {s.steer:+.2f}")
        lines.append("Car right now: " + ", ".join(extra) + ".")
    lap_log = list(getattr(svc, "lap_log", []))
    if lap_log:
        lines.append("Laps this session (lap, time): " + ", ".join(f"{n}: {say_laptime(t)}" for n, t in lap_log[-8:]) + ".")
    if getattr(svc, "last_sectors", None):
        sec = svc.last_sectors
        lines.append("Last lap sector times: " + ", ".join(f"S{i + 1} {v:.2f} s" for i, v in enumerate(
            [sec[0], sec[1] - sec[0], sec[2] - sec[1]])) + ".")
    notes = list(getattr(svc, "notes", []))[:4]
    if notes:
        lines.append("Corner notes from the last lap: " + " | ".join(n.text for n in notes))
    ffb = getattr(svc, "ffb", None)
    if ffb is not None and ffb.stats():
        st = ffb.stats()
        lines.append(f"Force feedback: verdict {st['verdict'].replace('_', ' ')}, clipping {st['clipping_percent']} percent "
                     f"of the time, strongest forces {st['strongest_percent']} percent of full scale.")
    eng = svc.engineer
    if eng is not None and getattr(eng, "driver", ""):
        lines.append(f"The driver's name is {eng.driver}.")
    mem = getattr(svc, "memory", None)
    if mem is not None:
        extra = mem.facts(s.track if s else "", s.car if s else "")
        if extra:
            lines.append(extra)
    if eng is not None and eng.focus_name:
        lines.append(f"Corner to work on: {eng.focus_name}, {eng.focus_tip}.")
    if svc.said_text:
        lines.append("Recently said on the radio: " + " | ".join(svc.said_text[-3:]))
    return "\n".join(lines) or "No live data right now (the car is not on track)."


def pit_facts(svc, snap, w) -> str:
    """One PIT STRATEGY block: stops made, the mandatory window, fuel and tyres against the laps to go, the last stop's
    length. Only what is measured; whatever is missing is said to be missing."""
    if not snap:
        return ""
    me = snap["me"]
    out = [f"PIT STRATEGY: {snap.get('stops', 0)} stops made so far."]
    pw = snap.get("pit_window")
    if pw and pw[1] >= 0 and pw[2] >= 0:
        unit = "minutes" if snap.get("end_t", -1) > 0 else "laps"
        out.append(f"Mandatory pit window from {pw[1]} to {pw[2]} {unit} (the game's status code {pw[0]}).")
    elif pw and pw[0] in (-1, 0):
        out.append("No mandatory pit stop in this session.")
    if snap.get("pit_min_s", -1) > 0:
        out.append(f"Minimum stationary time for the mandatory stop {snap['pit_min_s']:.0f} seconds.")
    if snap.get("last_stop_s", -1) > 0 and snap.get("stops", 0) > 0:
        out.append(f"Last stop took {snap['last_stop_s']:.0f} seconds in the pit lane.")
    togo = w.laps_to_go(snap) if w is not None else None
    use = w.per_lap() if w is not None else None
    if not use and snap.get("game_fuel_per_lap", -1) > 0:
        use = snap["game_fuel_per_lap"]
        out.append("Fuel use per lap is the game's own estimate, no lap measured yet.")
    if togo is None:
        out.append("Laps to go are not known yet (no lap time measured, or an open-ended session).")
    else:
        out.append(f"About {togo:.1f} laps to go.")
    if use:
        laps_fuel = snap["fuel"] / use
        out.append(f"Fuel {snap['fuel']:.1f} litres, {use:.2f} litres a lap, so {laps_fuel:.1f} laps of fuel"
                   + (f"; that is {'enough' if laps_fuel >= togo else 'not enough'} to the flag, "
                      f"{abs(laps_fuel - togo):.1f} laps {'spare' if laps_fuel >= togo else 'short'}." if togo is not None else "."))
        out.append(f"Tank capacity {snap['fuel_cap']:.0f} litres.")
    worn = svc.worn_pct(snap) if hasattr(svc, "worn_pct") else None
    if worn:
        avg = sum(worn) / len(worn)
        out.append(f"Tyres {avg:.0f} percent worn on average, worst {max(worn):.0f}, "
                   f"front left {worn[0]:.0f}, front right {worn[1]:.0f}, rear left {worn[2]:.0f}, rear right {worn[3]:.0f}.")
        rate = svc.wear_rate() if hasattr(svc, "wear_rate") else None
        if rate and rate > 0:
            out.append(f"Tyre wear is {rate:.1f} percent a lap on this set"
                       + (f", so about {avg + rate * togo:.0f} percent worn at the flag." if togo is not None else "."))
        else:
            out.append("Tyre wear rate is not measured yet.")
    out.append("Not known to the engineer: the time a stop costs in the pit lane, tyre compound choices and which stops rivals make.")
    return " ".join(out)


def corner_line(coach, c, history: list) -> str:
    """Everything known about one corner: the reference lap's numbers, then the driver's last laps there."""
    rt = getattr(coach, "rt", None)
    parts = [f"Turn {c.n} is a {c.look or 'corner'} at {c.apex:.0f} m into the lap"]
    if c.brake is not None:
        parts.append(f"the reference starts braking {c.apex - c.brake:.0f} m before the apex (at {c.brake:.0f} m) "
                     + (f"with the pedal peaking at {c.brake_peak:.0f} percent" if c.brake_peak else ""))
    if rt is not None:
        parts.append(f"reference minimum speed {rt.at(c.apex)[1]:.0f} km/h")
    if c.gear:
        parts.append(f"reference gear at the slowest point {c.gear}")
    if c.throttle_at is not None:
        parts.append(f"reference gets back on the throttle at {c.throttle_at:.0f} m, {c.throttle_at - c.apex:+.0f} m from the apex")
    if history:
        n = history[-1]
        mine = []
        if n.my_gear:
            mine.append(f"gear {n.my_gear}")
        if n.brake_diff is not None:
            mine.append(f"braked {abs(n.brake_diff):.0f} m {'later' if n.brake_diff > 0 else 'earlier'} than the reference")
        if n.brake_peak is not None:
            mine.append(f"brake peak {n.brake_peak:.0f} percent")
        if n.throttle_diff is not None:
            mine.append(f"back on the throttle {abs(n.throttle_diff):.0f} m {'later' if n.throttle_diff > 0 else 'earlier'}")
        if n.apex_off is not None:
            mine.append(f"{abs(n.apex_off):.1f} m {'inside' if n.apex_off > 0 else 'outside'} the reference line at the apex")
        mine.append(f"{'lost' if n.delta >= 0 else 'gained'} {abs(n.delta):.2f} s through the corner")
        parts.append("driver's last lap: " + ", ".join(mine))
    return "; ".join(p for p in parts if p) + "."


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    words = text.split()
    if len(words) > MAX_WORDS:
        text = " ".join(words[:MAX_WORDS]).rstrip(",;:") + "."
    return text


def answer(question: str, fact_text: str, client, model: str = "", lookup=None, native_tools: bool = False) -> Answer:
    """Claude's answer: the spoken reply (trimmed to MAX_WORDS) and, if asked for, a job or a note. If Claude did not
    return the JSON, its text is the reply and nothing else happens. With `lookup(request) -> text`, Claude may ask for
    data (tools.py) up to tools.MAX_ROUNDS times before it replies."""
    from . import tools
    from . import ask_tools
    head = [SYSTEM] + ([tools.DESCRIPTION] if lookup is not None else [ask_tools.NATIVE_TOOLS_TEXT] if native_tools else [])
    prompt = "\n\n".join(head + ["Facts:\n" + fact_text, "Driver asks: " + question, "Your reply (JSON):"])
    rounds = 0
    while True:
        r = client.messages.create(model=model, messages=[{"role": "user", "content": prompt}])
        raw = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
        req = tools.parse_request(raw) if lookup is not None else None
        if req is None:
            break
        rounds += 1
        result = lookup(req) if rounds <= tools.MAX_ROUNDS else "No more lookups: reply now with what you have."
        prompt += f"\n\nYou asked: {json.dumps(req)}\nResult: {result}\n\nYour reply (JSON):"
        if rounds > tools.MAX_ROUNDS:
            r = client.messages.create(model=model, messages=[{"role": "user", "content": prompt}])
            raw = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
            break
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except ValueError:
        d = None
    if not isinstance(d, dict) or not str(d.get("reply", "")).strip():
        return Answer(_clip(raw), question=question)
    pick = lambda k: d.get(k) if isinstance(d.get(k), str) and d.get(k).strip() else None
    return Answer(_clip(d["reply"]), d.get("job") if isinstance(d.get("job"), dict) else None, pick("remember"),
                  pick("forget"), question)


def handle(path: Path, svc, key: str, client, language: str = "", post=None, model: str = "",
           native_tools: bool = False) -> Optional[Answer]:
    """clip -> question -> Answer (None when nothing was understood). The clip is always deleted."""
    try:
        question = transcribe(path, key, language, post)
    finally:
        try:
            path.unlink(missing_ok=True)
            path.parent.rmdir()
        except OSError:
            pass
    if len(question.split()) < 2:
        return None
    cmd = parse_command(question)
    if cmd:
        say = "Corner coaching off." if cmd == "coaching_off" else "Corner coaching on."
        return Answer(say, question=question, command=cmd)
    from . import tools
    return answer(question, facts(svc), client, model, lookup=None if native_tools else (lambda req: tools.run(svc, req)),
                  native_tools=native_tools)
