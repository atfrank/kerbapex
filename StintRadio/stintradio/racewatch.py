"""The engineer's race awareness: fuel to the flag and the last lap to box, the fill on pit entry, laps/time to go,
flags and penalties, tyre wear, rain, and faster cars closing from behind. Everything comes from one plain snapshot of
the game's live data per second (LiveReader.race_snapshot), so it is testable without the game; nothing is guessed.

Where the game's header does not say what a number means (the direction of tyre wear), the first reading of each stint
settles it; contact is not called because the header gives no scale for the impact magnitude."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from driverdirector.phrasing import w as words

from .engineer import Msg

UNLIMITED = 1_000_000          # mMaxLaps is LONG_MAX when the race is timed
LAP_FLAGS = {10: "ten", 5: "five", 3: "three", 2: "two"}
TIME_MARKS = (30, 15, 10, 5)   # minutes to go
WHEELS = ["front left", "front right", "rear left", "rear right"]
TYRE_STEPS = (0.5, 0.75)       # fraction of tyre worn at which it is called


def say_litres(x: float) -> str:
    if x >= 10:
        return f"{words(int(round(x)))} litres"
    whole, tenth = int(x), int(round((x - int(x)) * 10)) % 10
    return f"{words(whole)} point {words(tenth)} litres" if tenth else f"{words(whole)} litres"


@dataclass
class RaceWatch:
    talk: str = "normal"                       # quiet | normal | chatty
    _lap: Optional[int] = None
    _lap_t: float = 0.0
    _lap_fuel: Optional[float] = None
    _pitted: bool = False
    _use: list = field(default_factory=list)       # litres per clean lap
    _times: list = field(default_factory=list)     # seconds per clean lap
    _fuel_said: float = -1e9
    plan: object = None                            # the latest stint Plan (plan.Plan)
    _lap_calls: int = 0
    _fired: set = field(default_factory=set)
    _phase: Optional[int] = None
    _yellow: Optional[int] = None
    _sector: tuple = (0, 0, 0)
    _yellow_said: float = -1e9      # session time the last local-yellow call was made
    _flag: int = 0
    _pen: int = 0
    _in_pits: bool = False
    _wear_mode: Optional[bool] = None              # True: the game's wear counts down from 1 (fraction left)
    _wear_steps: set = field(default_factory=set)
    _raining: bool = False
    _wet_said: bool = False
    _gaps: dict = field(default_factory=dict)      # car id -> (gap behind us in metres, time)
    _traffic_said: dict = field(default_factory=dict)

    def update(self, s: dict) -> list[Msg]:
        out: list[Msg] = []
        me, race = s["me"], s["session"] >= 10
        self._flags(s, me, out)
        self._weather(s, out)
        self._tyres(s, me, out)
        self._traffic(s, me, out)
        if race:
            self._progress(s, me, out)
            self._fuel(s, me, out)
        self._in_pits = me["in_pits"]
        if self.talk == "quiet":
            out = [m for m in out if m.priority < 2]
        return out

    # ---- laps and time to go -------------------------------------------------------------------------------------
    def laps_to_go(self, s: dict) -> Optional[float]:
        """Laps (fractions allowed) left in the race: a lap race counts them, a timed one divides the clock by the
        typical lap time."""
        me = s["me"]
        if s["max_laps"] < UNLIMITED:
            return max(0.0, s["max_laps"] - me["laps"] - self._lap_frac(s))
        if s["end_t"] > 0 and self._times:
            typical = sum(self._times[-3:]) / len(self._times[-3:])
            return max(0.0, (s["end_t"] - s["t"]) / typical)
        return None

    @staticmethod
    def _lap_frac(s: dict) -> float:
        return s["me"]["dist"] / s["track_len"] if s["track_len"] else 0.0

    def _progress(self, s: dict, me: dict, out: list) -> None:
        if me["finish"] == 1 and "flag" not in self._fired:
            self._fired.add("flag")
            out.append(Msg(f"That's the chequered flag. P{me['place']}. Well done.", priority=1, expires=30, kind="finish"))
            return
        if s["max_laps"] < UNLIMITED:
            togo = s["max_laps"] - me["laps"]
            key = ("laps", togo)
            if togo in LAP_FLAGS and key not in self._fired:
                self._fired.add(key)
                out.append(Msg(f"{LAP_FLAGS[togo].capitalize()} laps to go.", priority=2, expires=20, kind="progress"))
            elif togo == 1 and "last" not in self._fired:
                self._fired.add("last")
                out.append(Msg("Last lap. Bring it home.", priority=1, expires=20, kind="progress"))
        elif s["end_t"] > 0:
            left = (s["end_t"] - s["t"]) / 60
            for mark in TIME_MARKS:
                if left <= mark and ("time", mark) not in self._fired and left > mark - 2:
                    self._fired.add(("time", mark))
                    out.append(Msg(f"{words(mark).capitalize()} minutes to go.", priority=2, expires=20, kind="progress"))
            for mark in TIME_MARKS:                            # a mark already passed is not called late
                if left < mark - 2:
                    self._fired.add(("time", mark))

    # ---- fuel ----------------------------------------------------------------------------------------------------
    def _fuel(self, s: dict, me: dict, out: list) -> None:
        lap, fuel = s["lap"], s["fuel"]
        if self._lap is None:
            self._lap, self._lap_t, self._lap_fuel = lap, s["t"], fuel
            return
        if me["in_pits"] and not self._in_pits and s["fuel_cap"] > 0:      # just came into the pits: the fill
            m = self._fill(s, me)
            if m:
                out.append(m)
        if me["in_pits"]:
            self._pitted = True
        if lap == self._lap:
            return
        if self._lap_fuel is not None and not self._pitted and 0 < self._lap_fuel - fuel < s["fuel_cap"] / 2 \
                and s["phase"] != 6:
            self._use.append(self._lap_fuel - fuel)
            self._times.append(s["t"] - self._lap_t)
            del self._use[:-4], self._times[:-4]
        self._lap, self._lap_t, self._lap_fuel, self._pitted = lap, s["t"], fuel, me["in_pits"]
        self._lap_calls += 1
        m = self._fuel_call(s, me)
        if m:
            out.append(m)

    def per_lap(self) -> Optional[float]:
        return sum(self._use[-3:]) / len(self._use[-3:]) if self._use else None

    def _fuel_call(self, s: dict, me: dict) -> Optional[Msg]:
        use, togo = self.per_lap(), self.laps_to_go(s)
        if not use or togo is None or len(self._use) < 2:
            return None
        from .plan import make_plan, say_plan
        in_tank = s["fuel"] / use
        need = math.ceil(togo)
        old = self.plan
        self.plan = new = make_plan(me["laps"], togo, s["fuel"], s["fuel_cap"], use)
        if new.stops and new.stops[0].lap == me["laps"] + 1:       # this is the lap to come in
            return Msg(f"Box this lap. Fill {say_litres(new.stops[0].fill)}.", priority=0, expires=30, kind="fuel")
        changed = old is None or [x.lap for x in new.stops] != [x.lap for x in old.stops]
        margin = in_tank - need
        box_by = me["laps"] + int(in_tank) - 1               # keep a lap in hand
        if margin < 0:
            if in_tank < 3:
                return Msg(f"Fuel is critical, {words(max(0, int(in_tank)))} laps left. Box now.", priority=0, expires=15,
                           kind="fuel")
            replanned = changed and old is not None and bool(old.stops)      # the plan moved since it was last said
            if self._lap_calls - self._fuel_said >= 3 or self._fuel_said < 0 or replanned:
                self._fuel_said = self._lap_calls
                if replanned:
                    return Msg("Plan has changed. " + say_plan(new), priority=1, expires=30, kind="fuel")
                extra = f" {say_plan(new)}" if len(new.stops) > 1 else ""
                return Msg(f"Fuel for {words(int(in_tank))} more laps, {words(need)} to go. You'll need a stop, box by "
                           f"the end of lap {words(box_by)}.{extra}", priority=1, expires=30, kind="fuel")
        elif margin < 0.6:
            if self._lap_calls - self._fuel_said >= 3 or self._fuel_said < 0:
                self._fuel_said = self._lap_calls
                return Msg(f"Fuel is tight to the flag. Lift and coast a little, about {say_litres(use * 0.05)} a lap.",
                           priority=1, expires=30, kind="fuel")
        elif self._lap_calls - self._fuel_said >= (2 if self.talk == "chatty" else 6):
            self._fuel_said = self._lap_calls
            return Msg(f"Fuel's good to the flag, about {words(int(margin))} {'lap' if int(margin) == 1 else 'laps'} in hand.", priority=2, expires=25,
                       kind="fuel")
        return None

    def _fill(self, s: dict, me: dict) -> Optional[Msg]:
        use, togo = self.per_lap(), self.laps_to_go(s)
        if not use or togo is None:
            return None
        want = use * (math.ceil(togo) + 1) - s["fuel"]           # to the flag with a lap in hand
        room = s["fuel_cap"] - s["fuel"]
        if want <= 0:
            return Msg("No fuel needed, you can get to the end.", priority=1, expires=40, kind="fuel")
        if want > room:
            return Msg(f"Fill it, {say_litres(room)}. That won't get you to the end, there's another stop.", priority=1,
                       expires=40, kind="fuel")
        return Msg(f"Fuel to the end is {say_litres(want)}.", priority=1, expires=40, kind="fuel")

    # ---- flags, penalties ----------------------------------------------------------------------------------------
    def _flags(self, s: dict, me: dict, out: list) -> None:
        phase, yellow, sector = s["phase"], s["yellow_state"], tuple(s["sector_flags"])
        if self._phase is not None:
            if phase == 6 and self._phase != 6:
                out.append(Msg("Safety car. Full course yellow, keep to the delta.", priority=0, expires=15, kind="flag"))
            elif phase == 5 and self._phase == 6:
                out.append(Msg("Green flag, we're racing again.", priority=0, expires=15, kind="flag"))
            elif phase == 7 and self._phase != 7:
                out.append(Msg("Session stopped.", priority=1, expires=20, kind="flag"))
        if self._yellow is not None and yellow != self._yellow and phase == 6:
            if yellow == 4:
                out.append(Msg("Pits are open.", priority=1, expires=20, kind="flag"))
            elif yellow == 2:
                out.append(Msg("Pits are closed.", priority=1, expires=20, kind="flag"))
        if any(sector[i] and not self._sector[i] for i in range(3)) and phase != 6 and s["t"] - self._yellow_said > 45:
            self._yellow_said = s["t"]
            out.append(Msg("Local yellow ahead, careful.", priority=0, expires=8, kind="flag"))
        if me["flag"] == 6 and self._flag != 6:
            out.append(Msg("Blue flag, faster car behind. Let them by.", priority=0, expires=10, kind="flag"))
        pen = me["penalties"]
        if pen > self._pen:
            out.append(Msg("You have a penalty. Check the screen.", priority=0, expires=20, kind="penalty"))
        elif pen < self._pen and pen == 0:
            out.append(Msg("Penalty cleared.", priority=1, expires=20, kind="penalty"))
        self._phase, self._yellow, self._sector, self._flag, self._pen = phase, yellow, sector, me["flag"], pen

    # ---- weather -------------------------------------------------------------------------------------------------
    def _weather(self, s: dict, out: list) -> None:
        rain, wet = s["raining"], s["wetness"]
        on_wets = "wet" in s["tyre"].lower()
        if rain >= 0.05 and not self._raining:
            self._raining = True
            out.append(Msg("It's started to rain.", priority=1, expires=20, kind="weather"))
        elif rain < 0.02 and self._raining:
            self._raining = False
            out.append(Msg("Rain has stopped, the track will start to dry.", priority=2, expires=25, kind="weather"))
        if wet >= 0.35 and not on_wets and not self._wet_said:
            self._wet_said = True
            out.append(Msg("Track's getting properly wet. Think about wets.", priority=1, expires=30, kind="weather"))
        elif wet < 0.15 and on_wets and self._wet_said:
            self._wet_said = False
            out.append(Msg("Track is drying out, slicks soon.", priority=2, expires=30, kind="weather"))
        elif wet < 0.15 and not on_wets:
            self._wet_said = False

    # ---- tyres ---------------------------------------------------------------------------------------------------
    def tyre_wear(self, s: dict) -> tuple[float, str]:
        """(fraction worn on average, the worst wheel's name)."""
        wear = s["wear"]
        if self._wear_mode is None:
            self._wear_mode = sum(wear) / 4 > 0.5               # new tyres read near 1 when it counts down, near 0 when up
        worn = [1 - x if self._wear_mode else x for x in wear]
        return sum(worn) / 4, WHEELS[max(range(4), key=lambda i: worn[i])]

    def _tyres(self, s: dict, me: dict, out: list) -> None:
        wear = s["wear"]
        avg, _ = self.tyre_wear(s)
        worn = [1 - x if self._wear_mode else x for x in wear]
        if avg < 0.1 and self._wear_steps:                       # fresh tyres after a stop: start over
            self._wear_steps = set()
        if me["in_pits"]:
            return
        for step in TYRE_STEPS:
            if avg >= step and step not in self._wear_steps:
                self._wear_steps.add(step)
                worst = WHEELS[max(range(4), key=lambda i: worn[i])]
                if step == TYRE_STEPS[0]:
                    out.append(Msg(f"Tyres are about half gone. The {worst} is working hardest.", priority=2, expires=25,
                                   kind="tyres"))
                else:
                    out.append(Msg(f"Tyres are well worn, {words(int(round(avg * 100)))} percent. Worst is the {worst}.",
                                   priority=1, expires=25, kind="tyres"))

    # ---- traffic -------------------------------------------------------------------------------------------------
    def _traffic(self, s: dict, me: dict, out: list) -> None:
        """A car of another class that is gaining on us from behind: said once per car, with the seconds to go."""
        n, now = s["track_len"], s["t"]
        if not n or me["in_pits"]:
            return
        for c in s["cars"]:
            if c["me"] or c["in_pits"] or c["cls"] == me["cls"]:
                continue
            gap = (me["dist"] - c["dist"]) % n
            prev = self._gaps.get(c["id"])
            self._gaps[c["id"]] = (gap, now)
            if not prev or now - prev[1] < 0.5:
                continue
            closing = (prev[0] - gap) / (now - prev[1])
            if gap < 450 and closing > 4 and gap / closing < 15 and now - self._traffic_said.get(c["id"], -1e9) > 60:
                self._traffic_said[c["id"]] = now
                secs = max(2, int(round(gap / closing)))
                out.append(Msg(f"{c['cls']} car closing from behind, about {words(secs)} seconds.", priority=0,
                               expires=8, kind="traffic"))
