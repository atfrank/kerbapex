r"""KerbApex sim adapter template. Copy this file into %APPDATA%\StintRadio\adapters\ (or run
`python -m stintradio.sims new <id>`), fill in the TODOs, then check it:

    python -m stintradio.sims check <id>

Only sample() is required. Delete the capabilities you do not provide; the features that need them switch off by themselves.
The full contract (every field and unit) is in docs/ADAPTERS.md and stintradio/sims/contract.py."""
from stintradio.sims import C, Sample, SimAdapter


class MySimAdapter(SimAdapter):
    id = "mysim"                      # TODO: the value of live.game that selects this sim
    name = "My Sim"                   # TODO: shown to the driver
    capabilities = frozenset({C.SAMPLE})        # add C.SESSION_INFO, C.RACE_GAPS, C.RACE_SNAPSHOT, C.SETUP, ... as you implement them
    TURN_SIGN = 0                     # TODO: +1 or -1 once you know which way a left turn turns x/z (0 = worked out from lap one)

    @classmethod
    def available(cls, cfg=None) -> bool:
        """True while the sim's data is there (the game is running, the port answers, the shared memory exists)."""
        return False                  # TODO

    @classmethod
    def open(cls, cfg=None, **opts):
        adapter = cls()
        # TODO: connect (open the shared memory, bind the UDP port, ...); raise OSError("clear message") if you cannot
        return adapter

    def sample(self, vehicle_id=None):
        """Read the sim ONCE and return the player's car in the contract's units, or None when the car is not on track."""
        # TODO: read the sim's data and convert it: speed in km/h, pedals 0..100, distance in metres, times in seconds
        return None
        # return Sample(t=..., lap=..., lap_start=..., dist=..., speed=..., throttle=..., brake=..., steer=..., gear=...,
        #               rpm=..., x=..., z=..., delta_best=0.0, invalid=False, track="...", car="...", track_len=...,
        #               last_lap=-1.0, best_lap=-1.0)
