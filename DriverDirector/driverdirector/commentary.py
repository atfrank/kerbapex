"""Integrated AI commentary: facts -> Claude script -> validation -> TTS -> timed audio track.

No copy/paste round trip: scenes are built from the Plan, sent to Claude with a strict JSON
contract, validated against the facts (names, numbers, word budget), then voiced and mixed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Sequence

from .compose import ffmpeg_bin, probe_duration
from .config import CommentarySettings
from . import facts as race_facts
from .context import around_facts, gap_effect
from .models import Car, Event, Frame, Plan


@dataclass
class Scene:
    id: int
    t: float                 # replay time
    slot: float              # seconds available before the next scene
    kind: str
    facts: str
    excite: int = 0         # 0 normal | 1 excited | 2 overexcited (picks the voice clip)


def _fmt_lap(sec: float) -> str:
    if sec <= 0:
        return "n/a"
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:06.3f}"


def _frame_at(frames: Sequence[Frame], t: float) -> Frame:
    return min(frames, key=lambda f: abs(f.t - t))


def _intro_scenes(plan: Plan, f0: Frame, me0: Car, history, tag, at: float = 0.3) -> list[tuple[float, str, str, int]]:
    """Pre-race scenes for a race covered from its start: qualifying and grid, spoken as the lights go out.
    Nothing on track has happened yet, so the facts say so and carry only qualifying/grid information."""
    q = history.quali.get(plan.focus_id) if history else None
    grid = history.drivers[plan.focus_id].grid if history and plan.focus_id in history.drivers else 0
    if not q and not grid:
        return []
    rule = ("PRE-RACE: the race is only just starting and nothing has happened on track yet. Talk about "
            "qualifying and the grid only; do not describe any racing, overtakes or progress. ")
    parts = []
    if q:
        txt = f"Our driver {tag(plan.focus_id)} qualified P{q.position} overall, P{q.class_position} in {me0.car_class}"
        txt += f", with a {_fmt_lap(q.best_lap)}." if q.best_lap > 0 else "."
        pole = min((s for s, v in history.quali.items()
                    if history.drivers[s].car_class == me0.car_class and v.best_lap > 0),
                   key=lambda s: history.quali[s].best_lap, default=None)
        if pole is not None and q.best_lap > 0:
            pq = history.quali[pole]
            if pole == plan.focus_id:
                txt += " That was the fastest in class: pole position."
            else:
                txt += (f" Class pole: {tag(pole).split(' (')[0]} with a {_fmt_lap(pq.best_lap)}, "
                        f"so our driver was +{q.best_lap - pq.best_lap:.3f} off pole.")
        parts.append(txt)
    if grid:
        parts.append(f"Our driver starts from P{grid} on the grid.")
    out = [(plan.t0 + at, "intro", rule + " ".join(parts), 0)]
    ahead = next((c for c in f0.cars if c.place == me0.place - 1), None)
    behind = next((c for c in f0.cars if c.place == me0.place + 1), None)
    around = []
    for label, c in (("Directly ahead on the grid", ahead), ("Directly behind on the grid", behind)):
        if c is None:
            continue
        cq = history.quali.get(c.id) if history else None
        around.append(f"{label}: {tag(c.id)}" + (f", who qualified with a {_fmt_lap(cq.best_lap)}" if cq and cq.best_lap > 0 else "") + ".")
    if around:
        out.append((plan.t0 + at + 9.7, "grid", "PRE-RACE, THE GRID IS SET: " + " ".join(around)
                    + " Say nothing about any action yet.", 0))
    return out


def build_scenes(plan: Plan, frames: Sequence[Frame], quiet_every: float = 75.0, history=None, ctx=None,
                 locate=None, conditions: str = "", mistakes=()) -> list[Scene]:
    """`ctx` (context.RaceContext) adds exact gaps to the cars around and which way they are moving; `locate(event)`
    says where on track an event happened ("at the Bus Stop", "at corner five", "in sector two"). Both are optional."""
    roster = {c.id: c for c in frames[0].cars}
    tag = lambda i: f"{roster[i].driver} (#{roster[i].number})" if i in roster else f"car {i}"
    items: list[tuple[float, str, str, int]] = []

    f0 = _frame_at(frames, max(plan.t0, frames[0].t))
    me0 = next(c for c in f0.cars if c.id == plan.focus_id)
    slice_start = plan.t0 > frames[0].t + 30        # coverage of part of the race, not the start
    slice_end = plan.t1 < frames[-1].t - 30
    green = frames[0].t                                # the frames begin at the green flag
    intro_at = 0.3
    track_txt = race_facts.track_intro(history, conditions) if history is not None and not slice_start else ""
    if track_txt and green - plan.t0 >= 24:           # room before the lights for the track, qualifying and the grid
        items.append((plan.t0 + 0.3, "track", "PRE-RACE: " + track_txt + " Nothing has happened on track yet. Open the "
                      "broadcast by naming the track and where it is, then say how many turns and how long the race is.", 0))
        intro_at = 10.0
    intro = [] if slice_start else _intro_scenes(plan, f0, me0, history, tag, intro_at)
    items += intro or [(plan.t0 + 0.5, "start",
                  (f"Coverage joins the race in progress. Our driver: {tag(plan.focus_id)}, currently P{me0.place} "
                   f"in {me0.car_class}." if slice_start else
                   f"Race coverage begins at the start. Our driver: {tag(plan.focus_id)}, on the grid in P{me0.place} "
                   f"in {me0.car_class}; the race is only just starting."), 0)]
    if intro and green - plan.t0 >= 14:               # the video opens before the start: say when it goes green
        items.append((green + 0.3, "start",
                      f"GREEN FLAG: the race has just started. Our driver {tag(plan.focus_id)} is away from "
                      f"P{me0.place} on the grid.", 1))
        first_action = green + 8
    else:
        first_action = plan.t0 + (18 if intro else 6)     # keep the opening seconds for the pre-race story

    for e in plan.events:
        if e.t_end < plan.t0 or e.t_start > plan.t1:
            continue
        involved = e.involves(plan.focus_id)
        showcased = involved or any(i.event is e for i in plan.inlays)
        if not showcased or e.score < (3.0 if involved and e.kind in ("penalty", "tracklimits") else 4):
            continue
        if e.kind == "mistake":                        # measured from the driver's own telemetry: say it as an analyst
            effect = gap_effect(ctx, e.t_start, e.t_end, plan.focus_id) if ctx else ""
            items.append((max(e.t_end + 0.5, first_action), "mistake",
                          f"DRIVING ERROR by our driver {tag(plan.focus_id)}, just seen on track: {e.desc}"
                          + (f" {effect}" if effect else "")
                          + " Tell it vividly and fairly, like an excited TV commentator talking to casual fans; use "
                          "only these facts and keep their wording for times, speeds and distances.",
                          2 if e.score >= 6.5 else 1))
            continue
        who = " and ".join(tag(i) for i in e.drivers)
        box = next((i for i in plan.inlays if i.event is e), None)
        # speak when the viewer sees it: the picture-in-picture box if there is one, else the key moment
        moment = box.show_at + 1 if box else (e.peak if e.peak is not None else e.t_start) + 1
        fr = _frame_at(frames, moment)
        pl = {c.id: c.place for c in fr.cars}
        places = ", ".join(f"{tag(i).split(' (')[0]} P{pl.get(i, '?')}" for i in e.drivers)
        where = locate(e) if locate else ""
        items.append((max(moment, first_action), e.kind,
                      f"{e.kind.upper()}: {who}. {e.desc}. Positions: {places}."
                      + (f" Where on track: {where}." if where else "")
                      + ("" if involved else " (Shown as a picture-in-picture box; not our driver.)"),
                      2 if e.score >= 8 else 1 if e.score >= 5 else 0))

    for i in plan.inlays:                              # the box is not a race event, so it is not in plan.events
        if i.event.kind == "reference":
            items.append((max(i.show_at + 1, first_action), "reference",
                          f"REFERENCE LAP: the picture-in-picture box shows our driver's best qualifying lap, "
                          f"{i.event.desc}, played alongside the race's first lap for comparison.", 1))
            if history is not None:                    # how the race lap compares, sector by sector, as it is driven
                for t, txt, ex in race_facts.reference_sector_facts(history, plan.focus_id, i.show_until):
                    items.append((max(t, first_action), "reference", txt, ex))
    if history is not None and history.exact:          # the driver's own stops and bests, from the lap data
        for t, txt, ex in (race_facts.pit_facts(history, plan.focus_id, plan.t0, plan.t1)
                           + race_facts.milestone_facts(history, plan.focus_id, plan.t0, plan.t1)):
            kind = "pit" if txt.startswith("PIT") else "best"
            items.append((max(t, first_action), kind, txt, ex))

    t = plan.t0 + quiet_every
    while t < plan.t1 - 30:
        if all(abs(t - x[0]) > 20 for x in items):
            fr = _frame_at(frames, t)
            me = next(c for c in fr.cars if c.id == plan.focus_id)
            ahead = next((c for c in fr.cars if c.car_class == me.car_class and c.class_place == me.class_place - 1), None)
            behind = next((c for c in fr.cars if c.car_class == me.car_class and c.class_place == me.class_place + 1), None)
            txt = f"STATUS: {tag(plan.focus_id)} running P{me.class_place} in class, last lap {_fmt_lap(me.last_lap)}."
            around = around_facts(ctx, t, plan.focus_id) if ctx else ""
            if around:                                 # exact gaps and which way they are moving
                txt += " " + around
            else:
                if ahead:
                    txt += f" Car ahead: {ahead.driver}."
                if behind:
                    txt += f" Car behind: {behind.driver}."
            items.append((t, "status", txt, 0))
        t += quiet_every

    f1 = _frame_at(frames, plan.t1)
    me1 = next(c for c in f1.cars if c.id == plan.focus_id)
    recap = race_facts.recap_facts(history, plan.focus_id, mistakes) if history is not None and history.exact         and not slice_end else ""
    if recap and plan.t1 - 26 > plan.t0 + 60:          # the flag, then a short recap before the video ends
        items.append((plan.t1 - 12, "recap", recap, 1))
        finish_at = plan.t1 - 26
    else:
        finish_at = plan.t1 - 12
    overall, in_class = (race_facts.final_places(history, plan.focus_id)
                         if history is not None and history.exact and not slice_end else (0, 0))
    overall, in_class = overall or me1.place, in_class or me1.class_place      # the results, else the last frame
    items.append((max(plan.t0, finish_at), "finish" if not slice_end else "end",
                  (f"FINISH: {tag(plan.focus_id)} takes the flag in P{overall} overall, P{in_class} in class."
                   if not slice_end else
                   f"END OF COVERAGE (the race continues): {tag(plan.focus_id)} is P{me1.place} overall, "
                   f"P{me1.class_place} in class."), 0 if slice_end else 1))

    items.sort()
    scenes = []
    for n, (t, kind, facts, excite) in enumerate(items):
        nxt = items[n + 1][0] if n + 1 < len(items) else plan.t1 + 10
        scenes.append(Scene(n, round(t, 2), round(min(nxt - t - 0.5, 14.0), 2), kind, facts, excite))
    return scenes


PROMPT = """You are writing live commentary for a sim-racing replay video. Style: {style}.
Write ONE commentary line per scene. Hard rules:
- Use ONLY facts given in the scene. Never invent lap times, gaps, positions, causes or names.
- Each line must fit its word budget (words <= slot_seconds * {wps:.1f}).
- Refer to drivers by the names given, the way TV commentators do: the full name when a driver is first brought up, and after that
  mostly just the first name or just the last name, mixing the two from line to line (say "Alex" in one line and "Hartley" in the
  next, "Sam" or "Reyes"), with the full name only now and then for clarity. Do not repeat the same form in every line. Never say anything that assumes a driver's gender: no he, she, him, her, his,
  hers, himself, herself, and no "the man", "the woman", "the guy", "the lady", "the boy" or "the girl". Use the name,
  "the driver", or they/their. No emojis, no stage directions.
- This is spoken aloud to casual fans: vivid, punchy, descriptive, short sentences, an exclamation mark when the moment
  earns one. Describe what happened as a sequence a viewer can picture, not as a list of figures.
- Sound like live television race commentary, not a report: present tense and in the moment; fragments and quick
  reactions are fine ("Oh, that is hot!"); a rhetorical question now and then ("Will there be room?"); build up into
  the big moments; and once something has gone wrong switch into replay-analysis mode ("Let's have another look..."):
  what the driver did and why it cost time. Bring in the stakes (position, the gap, who is close) only when the facts
  give them. Vary the rhythm from line to line and never begin two lines the same way.
- Convey the experience and the emotion of the moment, not just the facts: the tension of a gap closing, the sting of a mistake, the
  relief of a clean lap, the thrill of a pass, the weight of the final laps, the hush before the lights go out. React like a fan who
  cares, and say what it must feel like for the driver ("that will sting", "you can feel the pressure building", "heart in mouth
  stuff"). Feelings and atmosphere are yours to give; facts are not. Never turn a feeling into a new fact: no invented causes, thoughts,
  quotes, crowd, weather or sounds that the scene does not give. A status update is not a table: do not read out every number, pick
  the ONE thing that matters (a closing gap, a lead that is slipping, a quiet stretch) and make it human.
- Facts about a driver's own mistake already give times, speeds and distances the way people say them aloud ("about
  a second and a half", "about thirty kilometres an hour", "about six car lengths"). Keep those words as given: never
  turn them back into digits, decimals or technical units.
{two_voice_rule}Return ONLY a JSON array: [{{"id": <scene id>, "text": "<line>"{two_voice_field}}}, ...] covering every scene.

Scenes:
{scenes}
"""


LINE_LUFS = -18.0                # every voiced line is brought to this loudness before the lines are laid on the track
SEP = "|||"                      # in a stored line: the play-by-play text, then the analyst's, when a scene has two voices
TWO_VOICE_KINDS = {"mistake", "incident", "overtake", "battle", "penalty", "tracklimits", "pit", "best", "recap",
                   "reference", "status", "track", "intro", "grid", "start", "finish"}
TWO_VOICE_MIN_SLOT = 7.0         # seconds a scene needs before two people can both speak in it


def two_voices(scene: Scene, cfg: CommentarySettings) -> bool:
    from . import tts
    return scene.kind in TWO_VOICE_KINDS and scene.slot >= TWO_VOICE_MIN_SLOT and tts.has_second_voice(cfg)


TWO_VOICE_RULE = """- Scenes with "two_voices": true are an exchange between TWO commentators in the same booth, side by side, facing each other, and
  it must sound like one live conversation, not two scripts. "text" is the play-by-play commentator: they call what is happening
  and leave the other an opening. "analyst" is the colour commentator, who answers what the play-by-play commentator has JUST said,
  in the very next breath, and adds something the play-by-play commentator did NOT say: what it means for the race, why it matters,
  what to watch for next, or a comparison of two of the facts given.
  Let the booth show: they turn to each other ("you", "mate", "my friend"; never a name, they have none and never guess a gender),
  react to each other's points ("good shout", "ha, you're not wrong", "I thought exactly the same", "oh, don't say that!"), finish
  each other's thought, groan or laugh together, hand over ("over to you", "what did you make of that?") and sometimes cut in with a
  quick reaction. Do it in some exchanges, not all, and never forced; vary it. Keep the chemistry going from one scene to the next:
  the play-by-play voice can pick up something the analyst said a moment earlier, and the analyst can pick up an earlier line of the
  play-by-play voice. Use I, you, we, that, it and they naturally between yourselves (about a driver it is still never he or she: use
  the name, "the driver" or they). Share the feeling of the moment too: one can be gripped while the other stays calm, or you can
  agree on how big it is. Never repeat the play-by-play line, never just list names or numbers again. The analyst's point must still
  rest only on this scene's facts (no new numbers, causes or names).
  The opening ("track" scene) and the sign-off ("finish" scene) are the exception to the "just said" rule: in the opening the
  play-by-play commentator welcomes the audience and brings in their co-commentator ("and alongside me in the booth..."), who answers
  warmly; at the finish the play-by-play voice calls the flag and hands over, and the analyst gives the last word and a joint goodbye.
  Both lines together must fit the word budget: play-by-play about a third, analyst about two thirds.
"""


def build_prompt(scenes: Sequence[Scene], cfg: CommentarySettings) -> str:
    rows = []
    for s in scenes:
        row = {"id": s.id, "slot_seconds": s.slot, "max_words": int(s.slot * cfg.words_per_second),
               "kind": s.kind, "facts": s.facts}
        if two_voices(s, cfg):
            row["two_voices"] = True
        rows.append(row)
    any_two = any(r.get("two_voices") for r in rows)
    return PROMPT.format(style=cfg.style, wps=cfg.words_per_second, scenes=json.dumps(rows, indent=1),
                         two_voice_rule=TWO_VOICE_RULE if any_two else "",
                         two_voice_field=', "analyst": "<line, only for two_voices scenes>"' if any_two else "")


# Words that assume a person's gender. A real driver's gender is never guessed from a name or a car, so a line using one is
# rejected and rewritten (unless the word is part of the scene's own facts, e.g. a driver actually called Guy).
GENDERED = re.compile(r"\b(he|she|him|her|his|hers|himself|herself|man|men|woman|women|guy|guys|lady|ladies|boy|girl|"
                      r"gentleman|chap|lad|lass|mr|mrs|ms|miss)\b", re.I)


def validate(lines: dict[int, str], scenes: Sequence[Scene], cfg: CommentarySettings) -> list[str]:
    errs = []
    by_id = {s.id: s for s in scenes}
    for sid, s in by_id.items():
        text = lines.get(sid)
        if not text:
            errs.append(f"scene {sid}: missing line")
            continue
        if SEP in text and not all(p.strip() for p in text.split(SEP)):
            errs.append(f"scene {sid}: the analyst line is empty")
        text = text.replace(SEP, " ")                      # both commentators share the scene's word budget
        words = len(text.split())
        if words > int(s.slot * cfg.words_per_second) + 1:
            errs.append(f"scene {sid}: {words} words exceeds budget {int(s.slot * cfg.words_per_second)}")
        for num in re.findall(r"\d+(?:[.:]\d+)*", text):
            if num not in s.facts:
                errs.append(f"scene {sid}: number '{num}' not in facts")
        facts = s.facts.lower()
        for g in sorted({m.group(0).lower() for m in GENDERED.finditer(text)}):
            if not re.search(rf"\b{g}\b", facts):
                errs.append(f"scene {sid}: '{g}' assumes a driver's gender; use the name, \"the driver\" or they/their")
    return errs


def parse_lines(raw: str) -> dict[int, str]:
    m = re.search(r"\[.*\]", raw, re.S)
    arr = json.loads(m.group(0) if m else raw)
    out = {}
    for x in arr:
        text = str(x["text"]).strip()
        if str(x.get("analyst") or "").strip():
            text += SEP + str(x["analyst"]).strip()
        out[int(x["id"])] = text
    return out


def find_claude_cli(configured: str = "") -> str | None:
    """The Claude Code CLI: configured path, PATH, or the copy bundled with the desktop app."""
    import glob
    import os
    import shutil
    if configured and os.path.isfile(configured):
        return configured
    found = shutil.which("claude")
    if found:
        return found
    # The desktop app is a packaged (MSIX) app: processes outside it see its AppData under Packages\...\LocalCache.
    roots = [os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code")]
    roots += glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages", "Claude_*", "LocalCache",
                                    "Roaming", "Claude", "claude-code"))
    # Newer desktop app builds keep it under Local\Claude-Data\claude_*\claude-code (old versions are removed on update).
    roots += glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-Data", "claude_*", "claude-code"))
    hits = []
    for base in roots:
        hits += glob.glob(os.path.join(base, "*", "*", "claude.exe")) + glob.glob(os.path.join(base, "*", "claude.exe"))
    hits = [h for h in hits if os.path.isfile(h)]
    return max(hits, key=lambda h: [int(x) if x.isdigit() else 0 for x in h.replace("\\", "/").split("/")[-3].split(".")]) if hits else None


def terminal_path(exe: str) -> str:
    """The path of the Claude CLI as the USER's own terminal sees it, with forward slashes (works in Git Bash and, with `&`
    in front, in PowerShell). The desktop app is a packaged app: its AppData\\Roaming\\Claude folder is really stored under
    AppData\\Local\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude, and only processes inside the app see the short path."""
    import glob
    appdata, local = os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")
    norm = os.path.normpath(exe)
    root = os.path.normpath(os.path.join(appdata, "Claude", "claude-code"))
    if appdata and local and norm.lower().startswith(root.lower() + os.sep):
        rel = norm[len(root) + 1:]
        for base in glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude", "claude-code")):
            cand = os.path.join(base, rel)
            if os.path.isfile(cand):
                return cand.replace("\\", "/")
    return norm.replace("\\", "/")


def claude_logged_in(exe: str, timeout: float = 20.0):
    """True/False from `claude auth status`, None if it cannot be told. The login is the user's own: this only reads it."""
    import subprocess
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=timeout,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return bool(json.loads(r.stdout).get("loggedIn"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class ClaudeCliClient:
    """Drop-in for the Anthropic client's `messages.create`, backed by `claude -p` (headless).
    Uses the user's Claude Code login, so no API key is needed. Tools are disabled: it only writes text."""

    def __init__(self, exe: str, runner=None, timeout: float = 240.0):
        import subprocess
        self.exe, self.timeout = exe, timeout
        self._run = runner or (lambda cmd, text: subprocess.run(
            cmd, input=text, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
            creationflags=0x08000000 if os.name == "nt" else 0))
        self.messages = self

    @staticmethod
    def _flatten(messages) -> str:
        if len(messages) == 1:
            return messages[0]["content"]
        out = []
        for m in messages:                     # the retry loop is multi-turn; the CLI call is single-shot
            out.append(("Previous answer of yours:\n" if m["role"] == "assistant" else "Request:\n") + m["content"])
        return "\n\n".join(out)

    def create(self, model: str = "", messages=(), **_):
        from types import SimpleNamespace
        base = [self.exe, "-p", "--output-format", "text", "--tools", "", "--no-session-persistence"]
        text = self._flatten(list(messages))
        r = self._run(base + (["--model", model] if model else []), text)
        if r.returncode and model:             # unknown model name for this install: use the default
            r = self._run(base, text)
        if r.returncode:
            out = (r.stderr or r.stdout or "")
            low = out.lower()
            if any(k in low for k in ("not logged in", "failed to authenticate", "oauth session expired", "/login")):
                raise RuntimeError(f'The Claude CLI that writes the commentary is signed out ({out.strip()[:120]}). '
                                   f'Sign in once, in your own terminal, then retry (PowerShell: put & in front): '
                                   f'"{terminal_path(self.exe)}" auth login --claudeai')
            raise RuntimeError(f"claude CLI failed ({r.returncode}): {out[-400:]}")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=r.stdout)])


def make_client(cfg: CommentarySettings):
    """Client for the configured backend. Raises with a clear message if it isn't usable."""
    import os
    if cfg.backend == "api":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("commentary backend is 'api' but ANTHROPIC_API_KEY is not set")
        import anthropic
        return anthropic.Anthropic()
    exe = find_claude_cli(cfg.cli_path)
    if not exe:
        raise RuntimeError("Claude CLI not found; set commentary.cli_path or install Claude Code")
    return ClaudeCliClient(exe)


def generate_script(scenes: Sequence[Scene], cfg: CommentarySettings, client=None, retries: int = 2) -> dict[int, str]:
    if client is None:
        client = make_client(cfg)
    prompt = build_prompt(scenes, cfg)
    messages = [{"role": "user", "content": prompt}]
    lines: dict[int, str] = {}
    for _ in range(retries + 1):
        resp = client.messages.create(model=cfg.model, max_tokens=4096, messages=messages)
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            lines = parse_lines(raw)
        except (ValueError, KeyError):
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": "That was not valid JSON. Return only the JSON array."}]
            continue
        errs = validate(lines, scenes, cfg)
        if not errs:
            return lines
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": "Fix these problems and return the full JSON array again:\n" + "\n".join(errs)}]
    # Final fallback: drop lines that still fail rather than shipping invented numbers.
    bad = {int(e.split()[1].rstrip(":")) for e in validate(lines, scenes, cfg)}
    return {k: v for k, v in lines.items() if k not in bad}


def render_track(scenes: Sequence[Scene], lines: dict[int, str], cfg: CommentarySettings,
                 t0: float, total: float, out_wav: str, workdir: str, progress=None) -> str:
    """Voice each line (engine per `cfg`), then place it at (scene.t - t0) on one silent track.
    A line longer than its slot is sped up (at most 1.3x) so it doesn't run into the next one."""
    from . import tts
    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    B = 1_000_000                       # an analyst line has its scene's id plus this
    todo, share = [], {}
    for s in scenes:
        text = lines.get(s.id)
        if not text:
            continue
        parts = [p.strip() for p in text.split(SEP)] if tts.has_second_voice(cfg) else [text.replace(SEP, " ").strip()]
        todo.append(tts.Line(s.id, parts[0], s.excite))
        if len(parts) > 1 and parts[1]:
            todo.append(tts.Line(s.id + B, parts[1], s.excite, voice=1))
        words = [len(p.split()) for p in parts[:2] if p]
        share[s.id] = [x / max(1, sum(words)) for x in words]
    if not todo:
        raise RuntimeError("no commentary lines to render")
    files, _engine = tts.synthesize(todo, cfg, wd, progress)
    by_id = {s.id: s for s in scenes}
    # (start seconds, audio file, seconds it may take): an analyst line follows its scene's first line
    placed: list[tuple[float, str, float]] = []
    for sid in sorted((i for i in files if i < B), key=lambda i: by_id[i].t):
        s = by_id[sid]
        a_share = share[sid][0]
        a_dur = probe_duration(str(files[sid]))
        a_slot = s.slot * a_share if s.slot > 0 else 0.0
        a_tempo = min(1.3, a_dur / a_slot) if a_slot > 0 and a_dur > a_slot else 1.0     # sped up to fit, at most 1.3x
        start = max(0.0, s.t - t0)
        placed.append((start, str(files[sid]), a_slot))
        if sid + B in files:
            gap = 0.2                                 # an answer comes straight after the line it answers
            b_slot = max(1.0, s.slot * share[sid][1] - gap)
            placed.append((start + a_dur / a_tempo + gap, str(files[sid + B]), b_slot))
    cmd = [ffmpeg_bin(), "-y", "-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=stereo"]
    for _, f, _ in placed:
        cmd += ["-i", f]
    fx = []
    from .compose import measure_lufs
    for n, (start, f, slot) in enumerate(placed, start=1):
        delay = int(start * 1000)
        dur = probe_duration(f)
        tempo = f",atempo={min(1.3, dur / slot):.3f}" if slot > 0 and dur > slot else ""
        lufs = measure_lufs(f)                      # every line to the same level, whichever voice it is
        even = f",volume={max(-12.0, min(12.0, LINE_LUFS - lufs)):.2f}dB" if lufs is not None else ""
        fx.append(f"[{n}:a]aresample=44100,aformat=channel_layouts=stereo{tempo}{even},adelay={delay}|{delay}[l{n}]")
    order = placed
    mix = "[0:a]" + "".join(f"[l{n}]" for n in range(1, len(order) + 1))
    fx.append(f"{mix}amix=inputs={len(order) + 1}:duration=first:normalize=0[out]")
    cmd += ["-filter_complex", ";".join(fx), "-map", "[out]", out_wav]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-1500:])
    return out_wav


def scenes_to_json(scenes: Sequence[Scene]) -> list[dict]:
    return [asdict(s) for s in scenes]
