import struct

from driverdirector import raceroom_telemetry as tel


def _buffer(drivers):
    buf = bytearray(2008 + tel.RECORD * tel.MAX_DRIVERS)
    struct.pack_into("<8i", buf, 0, 3, 6, 2008, tel.RECORD, 0, 0, 0, 0)
    buf[600:600 + 7] = b"Zolder\0"
    for i, d in enumerate(drivers):
        o = 2008 + i * tel.RECORD
        buf[o + 4:o + 4 + len(d["name"])] = d["name"].encode()
        struct.pack_into("<2i", buf, o + 136, d["place"], d["place"])
        struct.pack_into("<5f", buf, o + 144, d["dist"], d["dist"] / 4000, 0, 0, 0)
        struct.pack_into("<3i", buf, o + 164, 2, d["laps"], 1)
        struct.pack_into("<3f", buf, o + 192, 30.5, 62.0, 91.25)
        struct.pack_into("<2f", buf, o + 216, d["front"], -1)
        struct.pack_into("<f", buf, o + 256, d["speed"])
    return bytes(buf)


def test_decode_reads_drivers_in_place_order_with_units():
    f = tel.decode(_buffer([{"name": "B Slow", "place": 2, "dist": 900.0, "laps": 3, "front": 4.5, "speed": 40.0},
                            {"name": "A Fast", "place": 1, "dist": 1200.0, "laps": 3, "front": -1, "speed": 50.0}]))
    assert f.track == "Zolder"
    assert [d.name for d in f.drivers] == ["A Fast", "B Slow"]
    me = f.by_name("Alex Slow")
    assert me.place == 2 and me.laps == 3 and abs(me.kmh - 144) < 0.01 and me.gap_front == 4.5
    assert me.last_lap == 91.25 and me.best_lap == -1.0


def _frame(*laps):
    return tel.TelemetryFrame("T", "L", [tel.DriverTelemetry(i, f"D{i}", i + 1, i + 1, 0, 0, 0, 0, 0, 1, n, True, [-1] * 3, [-1] * 3,
                                                           -1, -1, False, 0, 0) for i, n in enumerate(laps)])


def test_wrapped_is_the_leader_falling_back_to_lap_zero():
    assert tel.wrapped(_frame(4, 3), _frame(0, 0))
    assert not tel.wrapped(_frame(4, 3), _frame(4, 3))
    assert not tel.wrapped(_frame(4, 3), _frame(5, 3))
    assert not tel.wrapped(_frame(), _frame(0))
