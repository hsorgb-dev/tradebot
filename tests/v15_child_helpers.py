"""Factories imported by the spawned BOT 2 test process (must be importable by name)."""
import pandas as pd

import orb_replay_v15 as replay
from test_delayed_v143 import FreePlanMarket
from test_live_path_v143 import LIVE_DAY

_WALL = {}


def wall_clock():
    """Real time 16:40 NY on LIVE_DAY: the whole delayed day can be caught up at once."""
    if 'clock' not in _WALL:
        _WALL['clock'] = replay.ReplayClock(
            pd.Timestamp(f'{LIVE_DAY} 16:40', tz='America/New_York').tz_convert('UTC'))
    return _WALL['clock']


def free_plan_market():
    return FreePlanMarket(wall_clock(), day=LIVE_DAY)


def broken_market():
    raise RuntimeError('SIP-Datenquelle nicht erreichbar (Testfall)')
