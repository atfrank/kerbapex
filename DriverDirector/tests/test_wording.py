"""Event descriptions are said the way people say them: no digits, no units, and a lone-car impact named for its strength."""
import re

from driverdirector.history import Driver, Incident, RaceHistory, incident_events
from driverdirector.phrasing import say_duration

from .test_record import make_director


def test_durations_are_said_in_everyday_terms():
    assert say_duration(4) == "a few seconds"
    assert say_duration(28) == "about thirty seconds"
    assert say_duration(40) == "about forty seconds"
    assert say_duration(50) == "about a minute"
    assert say_duration(98) == "about a minute and a half"
    assert say_duration(125) == "about two minutes"
    assert say_duration(200) == "about three and a half minutes"


def test_battles_are_described_without_figures(tmp_path, monkeypatch):
    d, clock = make_director(tmp_path, monkeypatch)
    battles = [e for e in d.events if e.kind == "battle"]
    assert battles
    for e in battles:
        assert " stuck to the back of " in e.desc and ", getting as close as " in e.desc
        assert not re.search(r"\d", e.desc) and "closest gap" not in e.desc, e.desc


def test_a_lone_cars_contact_is_named_for_how_hard_it_was():
    drivers = {1: Driver(1, "Alex Hartley"), 2: Driver(2, "Someone Else")}
    laps = {1: [], 2: []}

    def desc(mag):
        h = RaceHistory(drivers, laps, [Incident(100.0, "Alex Hartley", None, mag)], 0.0)
        h.exact = True
        return incident_events(h)[0].desc
    assert desc(109.0) == "Alex Hartley brushes the barrier"           # the sign in the real Spa race: strength 109
    assert desc(400.0) == "Alex Hartley hits the barrier"
    assert desc(855.9) == "Alex Hartley hits the barrier hard"


def test_times_past_a_minute_are_said_in_minutes():
    from driverdirector.phrasing import say_gap, say_seconds
    assert say_gap(45) == "about forty-five seconds"
    assert say_gap(60) == "about a minute"
    assert say_gap(90) == "about a minute and a half"
    assert say_gap(106) == "about two minutes"
    assert say_gap(150) == "about two and a half minutes"
    assert say_seconds(75) == "about a minute"
