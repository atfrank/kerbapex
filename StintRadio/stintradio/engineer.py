"""What a race engineer would say on the radio, and when.

Short, calm, numbers the way engineers say them ("one forty-three four", "two tenths", "gap one point two"), one point
per message, and never in a braking zone: the service only lets a message out on a straight. Everything said comes from
measured data (lap times, the corner analysis, the live gaps); nothing is made up."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from driverdirector.phrasing import say_across, w as words

ORD = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth"]
LOOK_BACK = 3          # a habit is judged over a corner's last few laps
MIN_TIMES = 2          # ... and must show up in at least this many of them


def say_laptime(t: float) -> str:
    """103.424 -> "one forty-three four"; 58.21 -> "fifty-eight two"."""
    if t <= 0:
        return ""
    m, s = divmod(t, 60)
    whole, tenth = int(s), int(round((s - int(s)) * 10)) % 10
    secs = f"oh {words(whole)}" if m and whole < 10 else words(whole)
    return (f"{words(int(m))} " if m else "") + secs + (" flat" if tenth == 0 else f" {words(tenth)}")


def say_delta(d: float) -> str:
    """Time to a reference, the way engineers say it: "a tenth", "two tenths", "half a second", "one point two"."""
    a = abs(d)
    if a < 0.05:
        return "bang on"
    if a < 0.15:
        return "a tenth"
    if a < 0.95:
        n = int(round(a * 10))
        return "half a second" if n == 5 else f"{words(n)} tenths"
    whole, tenth = int(a), int(round((a - int(a)) * 10)) % 10
    return f"{words(whole)} point {words(tenth)}" if tenth else (f"{words(whole)} second" + ("s" if whole > 1 else ""))


def rpm_say(rpm: float) -> str:
    """6900 -> "sixty-nine hundred", 7000 -> "seven thousand"."""
    h = int(round(rpm / 100.0))
    return f"{words(h // 10)} thousand" if h % 10 == 0 else f"{words(h)} hundred"


def first_name(full: Optional[str]) -> str:
    """The driver's first name for the radio: "Ian Atkinson" -> "Ian", "IAN ATKINSON" -> "Ian"; a one-word handle as is."""
    word = (full or "").split("#")[0].strip().split(" ")[0]
    return word.capitalize() if word.isupper() else word


def say_gap(g: float) -> str:
    if g < 0.95:
        return say_delta(g)
    whole, tenth = int(g), int(round((g - int(g)) * 10)) % 10
    return f"{words(whole)} point {words(tenth)}"


@dataclass
class Msg:
    text: str
    priority: int = 1          # 0 = urgent (gap closing), 1 = normal, 2 = nice to have
    expires: float = 25.0      # seconds after which it is no longer worth saying
    kind: str = ""

    def __post_init__(self):
        self.text = self.text[:1].upper() + self.text[1:]


@dataclass
class Engineer:
    seed: int = 0
    driver: str = ""                      # the driver's first name: spoken in front of some calls ("Alex, try to brake a bit later")
    focus: Optional[int] = None            # the corner number to work on this lap
    focus_name: str = ""
    focus_tip: str = ""
    _last_race: float = -1e9
    _last_alert: float = -1e9       # when the car-behind alert was last made: not again for a while
    _last_gap: dict = field(default_factory=dict)
    _laps: int = 0
    history: dict = field(default_factory=dict)      # corner number -> the last LOOK_BACK laps' notes for it
    announced: dict = field(default_factory=dict)    # (corner, issue) -> lap it was first called
    names: dict = field(default_factory=dict)        # corner number -> name
    _last: dict = field(default_factory=dict)        # the wording used last time, per call
    _tip_lap: dict = field(default_factory=dict)     # corner number -> lap its reminder was last said
    settle: int = 5                       # laps of full coaching; after that only what is needed
    focus_kind: Optional[str] = None      # the habit behind the focus corner (None: it is only the biggest loss)
    _prev_time: Optional[float] = None
    _praised: dict = field(default_factory=dict)     # corner number -> lap it was last praised
    _praise_lap: int = -1
    _praise_t: float = -1e9
    _said_lap: dict = field(default_factory=dict)    # (kind, corner number) -> lap it was last said

    def __post_init__(self):
        self.rng = random.Random(self.seed)

    ADDRESSED = {"focus": 0.7, "habit": 0.6, "debrief": 0.5, "praise": 0.5, "gap": 0.6}

    def address(self, m: "Msg") -> "Msg":
        """Put the driver's name in front of some calls, how often depending on the kind of call."""
        p = self.ADDRESSED.get(m.kind, 0.0)
        if not self.driver or not p or self.rng.random() >= p or m.text.lower().startswith(self.driver.lower()):
            return m
        body = m.text[:1].lower() + m.text[1:] if not m.text[:2].isupper() else m.text    # keep "P3", "I'm" as written
        return Msg(f"{self.driver}, {body}", priority=m.priority, expires=m.expires, kind=m.kind)

    def pick(self, *options: str) -> str:
        """One of several ways to say it, never the one used last time for the same call."""
        key = tuple(o[:12] for o in options)
        last = self._last.get(key)
        choices = [o for o in options if o != last] or list(options)
        out = self.rng.choice(choices)
        self._last[key] = out
        return out

    # ---- after each lap ------------------------------------------------------------------------------------------
    def lap_done(self, lap_time: float, ref_time: Optional[float], ref_source: str, best_before: Optional[float],
                 notes: list) -> list[Msg]:
        """The lap debrief: time, the delta, then the one corner that cost the most, and what to try next lap."""
        self._laps += 1
        out: list[Msg] = []
        lt = say_laptime(lap_time)
        new_best = best_before is not None and lap_time < best_before - 0.005
        who = {"leader": "the leader", "coachdave": "your best", "live": "your best",
               "fastest": "the fastest lap"}.get(ref_source, "the reference")
        if ref_source == "fastest" and ref_time and lap_time <= ref_time + 0.05:
            out.append(Msg(self.pick(f"{lt}. That's the fastest lap of the race!", f"Fastest lap in the race, {lt}. Brilliant.",
                                     f"{lt}, you've set the fastest lap. Well done."), kind="lap"))
        elif new_best:
            out.append(Msg(self.pick(f"Good lap. {lt}, new personal best.", f"That's a new best, {lt}. Nice.",
                                     f"{lt}. New best, keep it coming."), kind="lap"))
        elif ref_time:
            d = lap_time - ref_time
            if d < -0.05:
                out.append(Msg(self.pick(f"{lt}. That's {say_delta(d)} quicker than {who}.",
                                         f"{lt}, {say_delta(d)} up on {who}. Good.",
                                         f"Nice one, {lt}. {say_delta(d).capitalize()} faster than {who}."), kind="lap"))
            elif d < 0.05:
                out.append(Msg(self.pick(f"{lt}. Right on {who}'s pace.", f"{lt}, spot on {who}'s pace.",
                                         f"{lt}. That's level with {who}."), kind="lap"))
            else:
                out.append(Msg(self.pick(f"{lt}. {say_delta(d).capitalize()} off {who}.",
                                         f"Lap time {lt}, {say_delta(d)} down on {who}.",
                                         f"{lt}, that's {say_delta(d)} slower than {who}.",
                                         f"{say_delta(d).capitalize()} to find on {who}. {lt}."), kind="lap"))
        else:
            out.append(Msg(f"Lap time {lt}.", kind="lap"))
        if self._laps > self.settle:      # settled in: the time is only worth saying when it is news
            news = (new_best or self._prev_time is None or abs(lap_time - self._prev_time) >= 0.4
                    or self._laps % 3 == 0 or (ref_time is not None and lap_time < ref_time - 0.05))
            if not news:
                out.clear()
        self._prev_time = lap_time
        for n in notes:                                 # remember each corner's last few laps
            num = n.n or _corner_no(n.corner)
            if num:
                self.names[num] = n.corner.replace("corner", "turn")
                h = self.history.setdefault(num, [])
                h.append(n)
                del h[:-LOOK_BACK]
        issues = self.patterns()
        for (num, kind) in list(self.announced):           # a habit that is gone: say so once
            if not any(i["corner"] == num and i["kind"] == kind for i in issues) and self._recent_ok(num, kind):
                out.append(Msg(self.pick(f"Good, {self.names.get(num, 'that corner')} is sorted.",
                                         f"That's better in {self.names.get(num, 'that corner')}.",
                                         f"Nice, you've fixed {self.names.get(num, 'that corner')}.",
                                         f"{self.names.get(num, 'that corner').capitalize()} looks right now, well done."), priority=2,
                               kind="praise"))
                del self.announced[(num, kind)]
        if issues:
            top = issues[0]                                # the habit that costs the most
            self.focus, self.focus_name, self.focus_tip = top["corner"], top["name"], top["tip"]
            self.focus_kind = top["kind"]
            if (top["corner"], top["kind"]) not in self.announced:
                self.announced[(top["corner"], top["kind"])] = self._laps
                out.append(Msg(top["debrief"], priority=1, kind="habit"))
            return out
        line = self.line_comment(notes)
        if line is not None:
            out.append(line)
        lost = [n for n in notes if n.delta >= 0.08]
        if lost:
            worst = max(lost, key=lambda n: n.delta)
            why = _reason(worst)
            corner = worst.corner.replace("corner", "turn")
            self.focus = _corner_no(worst.corner)
            self.focus_name = corner
            self.focus_tip = _tip(worst)
            self.focus_kind = None
            num = self.focus or 0
            # settled in: only a big loss, and not the same corner again within three laps
            if self._laps <= self.settle or (worst.delta >= BIG_LOSS and self._laps - self._said_lap.get(("debrief", num), -9) >= 3):
                self._said_lap[("debrief", num)] = self._laps
                out.append(Msg(self.pick(f"Most of it in {corner}, {say_delta(worst.delta)}. {why}",
                                         f"Biggest loss is {corner}, {say_delta(worst.delta)}. {why}",
                                         f"{corner.capitalize()} is where it went, {say_delta(worst.delta)}. {why}",
                                         f"The time is in {corner}, {say_delta(worst.delta)}. {why}"), priority=2,
                               kind="debrief"))
        else:
            self.focus, self.focus_kind = None, None
        return out

    # ---- habits: the same thing in the same corner, lap after lap -------------------------------------------------
    def patterns(self) -> list[dict]:
        """Issues seen in at least MIN_TIMES of a corner's last LOOK_BACK laps, the costliest first. Each has the
        debrief sentence (said once) and the short tip said before the corner on later laps."""
        found = []
        for num, h in self.history.items():
            if len(h) < MIN_TIMES:
                continue
            name = self.names.get(num, f"turn {words(num)}")
            ref_gear = next((n.ref_gear for n in reversed(h) if n.ref_gear), None)
            short_to = next((n.short_to for n in reversed(h) if n.short_to), None)
            checks = CHECKS
            for kind, hit in checks.items():
                hits = [n for n in h if hit(n)]
                if len(hits) < MIN_TIMES:
                    continue
                cost = sum(max(0.0, n.delta) for n in hits) / len(hits)
                times = f"{words(len(hits))} laps running" if len(hits) == len(h) else f"{words(len(hits))} of the last {words(len(h))} laps"
                mine = max((n.my_gear for n in hits if n.my_gear), default=None)
                if kind == "gear_high" and ref_gear:
                    tip = f"try {ORD[ref_gear]} gear"
                    deb = f"You've been a gear high in {name}, {times}. Try {ORD[ref_gear]}, it'll help the exit."
                elif kind == "gear_low" and ref_gear:
                    tip = f"stay in {ORD[ref_gear]}"
                    deb = (f"You're dropping to {ORD[min(n.my_gear for n in hits)]} in {name}, {times}. "
                           f"Stay in {ORD[ref_gear]}, it's smoother on the exit.")
                elif kind == "short_shift":
                    to = f" to {ORD[short_to]}" if short_to else ""
                    tip = f"short-shift{to} on the exit"
                    deb = f"In {name} you're revving it out, {times}. Try short-shifting{to} on the exit, for traction."
                elif kind == "brake_early":
                    m = sum(-n.brake_diff for n in hits) / len(hits)
                    tip = self.pick("try to brake a bit later", "you can brake later", "carry it a little deeper",
                                    "hold off the brakes a touch longer")
                    d5 = words(int(round(m / 5) * 5))
                    deb = self.pick(f"You're braking early into {name}, about {d5} metres, {times}. You can go deeper.",
                                    f"Into {name} you're on the brakes about {d5} metres too soon, {times}. "
                                    "There's time in carrying it deeper.",
                                    f"{name.capitalize()}: braking about {d5} metres early, {times}. "
                                    "Trust the car and go in a bit further.")
                elif kind == "brake_late":
                    tip = self.pick("try to brake a touch earlier", "brake a little sooner", "get on the brakes a bit earlier")
                    deb = self.pick(f"You're braking too late into {name}, {times}, and it's costing you the exit.",
                                    f"You're carrying too much into {name}, {times}. Brake a touch earlier and you'll get the exit back.",
                                    f"{name.capitalize()}: too deep on the brakes, {times}. It's costing you on the way out.")
                elif kind == "brake_soft":
                    n0 = hits[-1]
                    mine5, ref5 = words(int(round(n0.brake_peak / 5) * 5)), words(int(round(n0.ref_peak / 5) * 5))
                    tip = self.pick(f"brake harder, about {ref5} percent", f"stamp on it, up to {ref5} percent",
                                    f"firmer on the pedal, {ref5} percent")
                    deb = self.pick(f"You're not braking hard enough into {name}, {times}. You're peaking around {mine5} "
                                    f"percent, the reference goes to {ref5}. Stamp on it, then ease off as you turn in.",
                                    f"In {name} the pedal only gets to about {mine5} percent, {times}. Your best lap is at "
                                    f"{ref5}. Hit it harder up front and release as you turn in.",
                                    f"More brake pressure for {name}: {mine5} percent now, {ref5} on the reference, {times}.")
                elif kind == "apex":
                    off = next((abs(n.apex_off) for n in reversed(hits) if n.apex_off is not None and n.apex_off < 0), None)
                    wide = f" by {say_across(off)}" if off else ""
                    tip = self.pick("hit the apex", "tuck it in to the apex", "turn in a touch later, clip the apex",
                                    "aim for the kerb")
                    deb = self.pick(f"You're missing the apex in {name}{wide}, {times}. Turn in a touch later and clip the "
                                    "inside kerb, it'll carry more speed through the middle.",
                                    f"You're running wide of the apex in {name}{wide}, {times}. "
                                    "Be patient on turn-in, then tuck in to the kerb.",
                                    f"{name.capitalize()}: not getting to the apex{wide}, {times}. "
                                    "Later turn-in, and aim right at the kerb.")
                elif kind == "early_turn":
                    tip = self.pick("turn in later", "wait for it, then turn in", "later turn-in here")
                    deb = self.pick(f"You're turning in early for {name}, {times}, and running out wide at the apex. "
                                    "Wait a little longer, then turn in.",
                                    f"In {name} you're inside the line on entry and then wide at the apex, {times}. "
                                    "That's turning in too early. Hold off a touch.",
                                    f"Too early on the turn-in at {name}, {times}. Later and tighter, and the apex will come to you.")
                elif kind == "throttle":
                    late = max((n for n in hits if n.throttle_diff is not None), key=lambda n: n.throttle_diff, default=None)
                    when = ""
                    if late is not None:
                        when = f"about {words(int(round(late.throttle_diff / 5) * 5))} metres"
                        if late.throttle_s and late.throttle_s >= 0.1:
                            when += f", roughly {say_tenths(late.throttle_s)}"
                    tip = self.pick("get on the throttle a bit earlier", "power down earlier", "back on the power sooner",
                                    "throttle on at the apex")
                    behind = f", {when} after the reference" if when else ""
                    deb = self.pick(f"In {name} you're late back on the power{behind}, {times}. Pick up the throttle earlier.",
                                    f"You're waiting too long for the throttle in {name}{behind}, {times}. "
                                    "Start feeding it in at the apex.",
                                    f"{name.capitalize()}: the power comes in late{behind}, {times}. Get on it as you pass the apex.")
                else:
                    continue
                found.append({"corner": num, "kind": kind, "name": name, "tip": tip, "debrief": deb,
                              "cost": cost, "laps": len(hits), "gear": mine})
        return sorted(found, key=lambda i: (-i["cost"], -i["laps"]))

    def _recent_ok(self, num: int, kind: str) -> bool:
        """The last two laps of that corner were clean of that issue."""
        h = self.history.get(num, [])[-2:]
        if len(h) < 2:
            return False
        bad = CHECKS[kind]
        return not any(bad(n) for n in h)

    def approaching(self, corner_no: int, name: str) -> list[Msg]:
        """A few seconds before the focus corner: the one thing to do differently."""
        if corner_no != self.focus or not self.focus_tip:
            return []
        settled = self._laps > self.settle
        if self._laps - self._tip_lap.get(corner_no, -9) < (3 if settled else 2):    # every other lap at most: every lap is noise
            return []
        if settled:                       # as needed: only while the last lap still showed the problem
            h = self.history.get(corner_no, [])
            last = h[-1] if h else None
            if last is None or not (CHECKS[self.focus_kind](last) if self.focus_kind in CHECKS else last.delta >= 0.1):
                return []
        self._tip_lap[corner_no] = self._laps
        where = name.replace("corner", "Turn").capitalize()
        return [Msg(self.pick(f"{where}, {self.focus_tip}.", f"Coming up, {where.lower()}. {self.focus_tip.capitalize()}.",
                              f"Remember, {self.focus_tip} in {where.lower()}.", f"{self.focus_tip.capitalize()} here.",
                              f"{where} next, {self.focus_tip}."), priority=0, expires=4.0, kind="focus")]

    def guide(self, num: int, look: str, brake_peak: Optional[float], tip: str = "") -> "Msg":
        """The call before a corner for a driver who does not know the track yet: which turn, what it is like, how hard
        the braking is, and (for the corner being worked on) the tip."""
        n = words(num)
        braking = ("" if brake_peak is None else "heavy" if brake_peak >= 85 else "medium" if brake_peak >= 50 else "light")
        shape = f"a {look}" if look and look[0] not in "aeiou" else (f"an {look}" if look else "")
        if look and braking:
            text = self.pick(f"Turn {n}, {look}, {braking} braking.",
                             f"Next, turn {n}. {shape.capitalize()}, {braking} on the brakes.",
                             f"Turn {n} coming up, {shape}. {braking.capitalize()} braking.",
                             f"{braking.capitalize()} braking for turn {n}, it's {shape}.",
                             f"Here's turn {n}: {look}, {braking} brake.")
        elif look:
            text = self.pick(f"Turn {n}, {look}.", f"Next up, turn {n}, {shape}.", f"Turn {n} coming, {shape}.")
        else:
            text = self.pick(f"Turn {n}.", f"Turn {n} next.", f"Coming up, turn {n}.")
        if tip:
            text += " " + tip[:1].upper() + tip[1:] + "."
        return Msg(text, priority=1, expires=8.0, kind="guide")

    def line_comment(self, notes: list) -> Optional["Msg"]:
        """After a lap: the one corner where the car was furthest off the reference line, said once, with the numbers.
        Not said for a corner the habit finder is already talking about."""
        worst, off = None, 0.0
        for n in notes:
            o = n.apex_off
            if o is not None and abs(o) > off and abs(o) >= WIDE_M and (n.n, "apex") not in self.announced:
                worst, off = n, abs(o)
        if worst is None:
            return None
        if self._laps > self.settle and self._laps - self._said_lap.get(("line", worst.n), -9) < 3:
            return None                   # settled in: not the same corner's line again so soon
        self._said_lap[("line", worst.n)] = self._laps
        name = self.names.get(worst.n) or worst.corner.replace("corner", "turn")
        across = say_across(off)
        if worst.apex_off < 0:
            text = self.pick(f"{name.capitalize()}, you were {across} wide of your line at the apex.",
                             f"You ran {across} wide at the apex in {name}.",
                             f"Wide at the apex in {name}, {across} off your best line.")
        else:
            text = self.pick(f"{name.capitalize()}, you cut inside your line by {across} at the apex.",
                             f"You were {across} tighter than your best line at the apex in {name}.")
        return Msg(text, priority=2, expires=25.0, kind="line")

    def nailed(self, note, now: float) -> Optional["Msg"]:
        """Right after a corner: a short "that's the one" when it matched or beat the reference with none of the known
        habits, naming what was right, so the driver learns what good feels like. At most one per lap, not the same corner
        again within three laps, and not within half a minute of the last."""
        num = note.n or _corner_no(note.corner)
        if not num or note.delta > NAILED_DELTA or any(hit(note) for hit in CHECKS.values()):
            return None
        if note.lap == self._praise_lap or now - self._praise_t < 30 or note.lap - self._praised.get(num, -9) < 3:
            return None
        right = []
        if note.brake_diff is not None and abs(note.brake_diff) <= 5:
            right.append("braking point spot on")
        if note.apex_off is not None and abs(note.apex_off) <= 1.0:
            right.append("right on the line")
        if note.throttle_diff is not None and note.throttle_diff <= 4:
            right.append("on the power at just the right moment")
        if note.my_gear and note.ref_gear and note.my_gear == note.ref_gear:
            right.append("right gear")
        pace = (f"{say_delta(note.delta)} up" if note.delta <= -0.05 else "right on the pace")
        detail = ", ".join([pace] + right[:2])
        self._praise_lap, self._praise_t, self._praised[num] = note.lap, now, note.lap
        name = self.names.get(num) or note.corner.replace("corner", "turn")
        return Msg(self.pick(f"That's the one, {name}. {detail.capitalize()}.",
                             f"Nailed {name}. {detail.capitalize()}.",
                             f"{name.capitalize()}, that's what good looks like. {detail.capitalize()}.",
                             f"Yes, {name} was spot on. {detail.capitalize()}. Remember how that felt."),
                   priority=2, expires=8.0, kind="praise")

    def propose(self, direction: str) -> "Msg":
        """An experiment to try on the next lap, from what the lock-ups show."""
        why = ("the front wheels are locking more than the rears" if direction == "rear"
               else "the rear wheels are stepping out under braking more than the fronts")
        return Msg(self.pick(f"For the next lap, try the brake bias a click towards the {direction}. {why.capitalize()}. I'll compare the laps.",
                             f"Experiment for the next lap: brake bias one click {direction}ward. {why.capitalize()}. "
                             "Tell me nothing, I'll see the change and report back.",
                             f"Try moving the brake bias a click towards the {direction}, {why}. I'll tell you how it goes."),
                   priority=1, expires=30.0, kind="experiment")

    def experiment_started(self, label: str) -> "Msg":
        return Msg(self.pick(f"Got it, {label} changed. Two clean laps and I'll tell you how it went.",
                             f"{label.capitalize()} changed, noted. I'll compare the next two laps.",
                             f"Okay, new {label}. Give me two clean laps."), priority=1, expires=20.0, kind="experiment")

    def experiment_result(self, label: str, r: dict, v: str) -> "Msg":
        d = r["d_avg"]
        pace = ("lap times are about the same" if abs(d) < 0.05
                else f"laps are {say_delta(d)} {'quicker' if d < 0 else 'slower'} on average")
        parts = [f"On {label}, {pace}"]
        if r.get("locks_known"):
            fb, fa, rb, ra = r["front_before"], r["front_after"], r["rear_before"], r["rear_after"]
            if abs(fa - fb) >= 0.5:
                parts.append(f"front lock-ups {'down' if fa < fb else 'up'} to {words(int(round(fa)))} a lap from {words(int(round(fb)))}")
            if abs(ra - rb) >= 0.5:
                parts.append(f"rear {'down' if ra < rb else 'up'} to {words(int(round(ra)))} from {words(int(round(rb)))}")
        end = {"keep": "Keep it.", "revert": "I'd go back.", "mixed": "Mixed result, so it's your call.",
               "same": "No real difference, so go on feel."}[v]
        text = ". ".join([", ".join(parts)]) + f". {end}"
        if r.get("noisy"):
            text += " Traffic makes it rough, so take it with a pinch of salt."
        return Msg(text, priority=1, expires=40.0, kind="experiment")

    def shift_advice(self, gear: int, best: float, yours: float) -> "Msg":
        """When to change up in one gear, from where the car pulls hardest."""
        ordinal = ORD[gear] if gear < len(ORD) else str(gear)
        b, y = rpm_say(best), rpm_say(yours)
        if yours < best:
            return Msg(self.pick(f"In {ordinal}, hold it longer. The car pulls until about {b}, and you're changing at {y}.",
                                 f"Shift timing: {ordinal} gear, wait for {b}. You're going up at {y}."), priority=2, expires=30.0, kind="shift")
        return Msg(self.pick(f"In {ordinal}, change up sooner. The next gear takes over around {b}, you're holding to {y}.",
                             f"Shift timing: {ordinal} gear, go up at {b}. You're staying in it to {y}."), priority=2, expires=30.0, kind="shift")

    def battle(self, name: str, loss_where: str, loss: float, gain_where: str, gain: float, net: float) -> Optional["Msg"]:
        """Against the car ahead: where it is taking time, or (when nowhere stands out) where you are quicker."""
        who = first_name(name)
        if loss >= BATTLE_LOSS:
            return Msg(self.pick(f"You're losing most to {who} in {loss_where}, about {say_delta(loss)} a lap.",
                                 f"{who} is taking {say_delta(loss)} a lap off you through {loss_where}.",
                                 f"{loss_where.capitalize()} is where {who} is getting away, {say_delta(loss)} a lap."),
                       priority=1, expires=20.0, kind="gap")
        if gain >= BATTLE_LOSS and net < 0:
            return Msg(self.pick(f"You're the quicker through {gain_where}, {say_delta(gain)} a lap on {who}.",
                                 f"You're hunting {who} down, best in {gain_where}."), priority=2, expires=20.0, kind="gap")
        return None

    # ---- races -----------------------------------------------------------------------------------------------------
    def race(self, now: float, place: int, gap_ahead: Optional[float], gap_behind: Optional[float],
             lap_frac: float, ahead: Optional[str] = None, behind: Optional[str] = None) -> list[Msg]:
        """Position and gaps every couple of minutes, sooner when a car behind gets close or the car ahead comes back."""
        out: list[Msg] = []
        prev_b, prev_a = self._last_gap.get("behind"), self._last_gap.get("ahead")
        if (gap_behind is not None and gap_behind < 0.6 and (prev_b is None or prev_b >= 0.6)
                and now - self._last_alert > 25):
            self._last_alert = now
            who = first_name(behind)
            out.append(Msg(self.pick(*((f"{who} is right behind you, within half a second.", f"Pressure from {who}, half a second back.",
                                       f"{who} is all over you, defend.") if who else
                                      ("Car behind, within half a second.", "Pressure behind, they're close.",
                                       "Car behind is right with you, defend."))),
                           priority=0, expires=6.0, kind="gap"))
        if now - self._last_race >= 120 and 0.15 < lap_frac < 0.6:
            parts = [f"P{place}" if place else ""]
            if gap_ahead is not None and gap_ahead < 30:
                trend = ""
                if prev_a is not None:
                    trend = ", closing" if gap_ahead < prev_a - 0.2 else (", they're pulling away"
                                                                          if gap_ahead > prev_a + 0.2 else "")
                parts.append(f"{first_name(ahead)} {say_gap(gap_ahead)} ahead{trend}" if ahead
                             else f"gap ahead {say_gap(gap_ahead)}{trend}")
            if gap_behind is not None and gap_behind < 30:
                parts.append(f"{first_name(behind)} {say_gap(gap_behind)} behind" if behind
                             else f"gap behind {say_gap(gap_behind)}")
            text = ", ".join(p for p in parts if p)
            if text:
                out.append(Msg(text[0].upper() + text[1:] + ".", priority=1, expires=15.0, kind="race"))
                self._last_race = now
                self._last_gap["ahead"] = gap_ahead
        if gap_behind is not None:
            self._last_gap["behind"] = gap_behind
        return out


# one test per habit, on a Note (the corner's result on one lap)
CHECKS = {
    "gear_high": lambda n: bool(n.my_gear and n.ref_gear and n.my_gear > n.ref_gear),
    "gear_low": lambda n: bool(n.my_gear and n.ref_gear and n.my_gear < n.ref_gear),
    "short_shift": lambda n: n.held is True,
    "brake_early": lambda n: n.brake_diff is not None and n.brake_diff <= -10,
    "brake_late": lambda n: n.brake_diff is not None and n.brake_diff >= 10 and n.delta > 0,
    # braking with much less pedal than the reference, in a corner that cost time: more pressure, then ease off
    "brake_soft": lambda n: (n.brake_peak is not None and n.ref_peak is not None and n.ref_peak >= 40
                             and n.ref_peak - n.brake_peak >= SOFT_BRAKE_PCT and n.delta > 0),
    "apex": lambda n: any("missed the apex" in c for c in n.causes) or (n.apex_off is not None and n.apex_off <= -WIDE_M),
    "early_turn": lambda n: (n.entry_off is not None and n.apex_off is not None
                             and n.entry_off >= EARLY_TURN_M and n.apex_off <= -1.0),
    "throttle": lambda n: any("late on the throttle" in c for c in n.causes) or (
        n.throttle_diff is not None and n.throttle_diff >= LATE_THROTTLE_M),
}
SOFT_BRAKE_PCT = 15.0
NAILED_DELTA = 0.03        # a corner this close to the reference (or quicker), with no habit showing, is "nailed"
BATTLE_LOSS = 0.06         # seconds a lap in one segment before the car ahead's gain is worth a call
BIG_LOSS = 0.25            # settled in: the biggest-loss debrief is only said for a corner that cost at least this
WIDE_M = 2.0               # metres outside the reference line at the apex
EARLY_TURN_M = 2.5         # metres inside the reference line before the corner, then wide: turned in early
LATE_THROTTLE_M = 12.0     # metres later than the reference onto the power


def say_tenths(t: float) -> str:
    """A short time the way it is said, never as a decimal."""
    if t < 0.2:
        return "a tenth of a second"
    if t < 0.4:
        return "a quarter of a second"
    if t < 0.65:
        return "half a second"
    return "most of a second"


def _corner_no(name: str) -> Optional[int]:
    from driverdirector.phrasing import w as _w
    for n in range(1, 40):
        if name.endswith(_w(n)):
            return n
    return None


def _reason(note) -> str:
    c = " ".join(note.causes)
    if note.brake_diff is not None and note.brake_diff <= -8:
        return f"You're braking about {words(int(round(-note.brake_diff / 5) * 5))} metres early."
    if note.brake_diff is not None and note.brake_diff >= 8 and "deep" in c:
        return "Braking too late, you're overshooting the apex."
    if "deep" in c:
        return "Carrying too much speed in, it's costing you the exit."
    if "apex" in c:
        return "Missing the apex there."
    if "short-shift" in note.text or "held" in note.text:
        return "Short shift on the exit."
    if "late on the throttle" in c:
        return "Get on the power earlier."
    if "poor exit" in c:
        return "Exit's the problem, focus on the run out."
    return ""


def _tip(note) -> str:
    c = " ".join(note.causes)
    if note.brake_diff is not None and note.brake_diff <= -8:
        return "try to brake a bit later"
    if "deep" in c or (note.brake_diff is not None and note.brake_diff >= 8):
        return "try to brake a touch earlier, get it turned"
    if "late on the throttle" in c or "poor exit" in c:
        return "focus on the exit, early throttle"
    if "apex" in c:
        return "hit the apex"
    return "smooth through here"
