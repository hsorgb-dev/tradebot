"""V1.6.1 performance changes must not change a single decision.

The fast signal scan is compared with the kept reference implementation (and with
the V1.5 module) on randomized minute data with gaps, invalid and duplicate bars,
off-grid timestamps and other symbols mixed in."""
import numpy as np
import pandas as pd
import pytest

import orb_sim_v15 as sim15
import orb_sim_v16 as sim
from test_replay_v14 import C

NY = 'America/New_York'
OPEN = pd.Timestamp('2026-09-15 09:30', tz=NY).tz_convert('UTC')


def random_day(rng, symbol='AAA', minutes=390):
    base = 50 + 100 * rng.random()
    steps = rng.normal(0, 0.002, minutes)
    if rng.random() < 0.6:
        steps[rng.integers(15, 60):] += 0.0015 * rng.random()
    close = base * np.exp(np.cumsum(steps))
    opened = np.concatenate([[close[0]], close[:-1]])
    frame = pd.DataFrame(dict(symbol=symbol, timestamp=OPEN + pd.to_timedelta(np.arange(minutes), unit='min'),
                              open=opened, high=np.maximum(opened, close) * (1 + 0.001 * rng.random(minutes)),
                              low=np.minimum(opened, close) * (1 - 0.001 * rng.random(minutes)),
                              close=close, volume=rng.integers(0, 50_000, minutes).astype(float), vwap=close))
    keep = rng.random(minutes) > rng.choice([0.0, 0.02, 0.1, 0.3])     # missing minutes
    if rng.random() < 0.2:
        keep[0] = False                                                   # first OR minute missing
    frame = frame[keep].copy()
    if len(frame) > 5 and rng.random() < 0.5:                             # contradictory bar
        i = frame.index[rng.integers(0, len(frame))]
        frame.loc[i, 'high'] = frame.loc[i, 'low'] - 0.5
    if len(frame) > 5 and rng.random() < 0.3:                             # NaN
        frame.loc[frame.index[rng.integers(0, len(frame))], 'close'] = np.nan
    if len(frame) > 5 and rng.random() < 0.3:                             # duplicate minute
        frame = pd.concat([frame, frame.iloc[[rng.integers(0, len(frame))]]])
    if len(frame) > 5 and rng.random() < 0.3:                             # off-grid timestamp
        extra = frame.iloc[[rng.integers(0, len(frame))]].copy()
        extra['timestamp'] = extra.timestamp + pd.Timedelta(seconds=30)
        frame = pd.concat([frame, extra])
    other = frame.copy()
    other['symbol'] = 'ZZZ'
    frame = pd.concat([frame, other.iloc[:20]]).sample(frac=1, random_state=int(rng.integers(0, 1000)))
    frame['feed'] = 'sip'
    return frame.reset_index(drop=True)


@pytest.mark.parametrize('seed', range(60))
def test_fast_scan_equals_reference(seed):
    rng = np.random.default_rng(seed)
    frame = random_day(rng)
    settings = sim.DryRunSettings()
    for minute in list(range(10, 18)) + list(rng.integers(18, 390, 12)):
        decision = OPEN + pd.Timedelta(minutes=int(minute))
        visible = frame[frame.timestamp < decision]
        fast_rejected, slow_rejected = [], []
        fast = sim.price_candidates_until(visible, 'AAA', OPEN, decision, C, settings, rejected=fast_rejected)
        slow = sim.price_candidates_until_slow(visible, 'AAA', OPEN, decision, C, settings,
                                               rejected=slow_rejected)
        assert fast == slow
        assert fast_rejected == slow_rejected
        v15 = sim15.price_candidates_until(visible, 'AAA', OPEN, decision, C, sim15.DryRunSettings())
        assert fast == v15


def test_fast_scan_handles_empty_and_foreign_frames():
    decision = OPEN + pd.Timedelta(minutes=40)
    empty = pd.DataFrame(columns=['symbol', 'timestamp', 'open', 'high', 'low', 'close', 'volume', 'vwap', 'feed'])
    assert sim.price_candidates_until(empty, 'AAA', OPEN, decision, C) == \
        sim.price_candidates_until_slow(empty, 'AAA', OPEN, decision, C)
    other = random_day(np.random.default_rng(1), symbol='BBB')
    assert sim.price_candidates_until(other, 'AAA', OPEN, decision, C) == ([], True, False)


class NoTrades:
    """Missing reference minutes: no trades at all -> odd-lot volume 0 (or an error)."""

    def get_stock_trades(self, request):
        return type('A', (), dict(data={}))()


def outcome(function, *args):
    try:
        return ('ok', function(*args))
    except Exception as exc:          # the exact error type and text are part of the result
        return ('error', type(exc).__name__, str(exc))


@pytest.mark.parametrize('seed', range(40))
def test_fast_reference_volume_equals_reference(seed, tmp_path):
    rng = np.random.default_rng(1000 + seed)
    frame = random_day(rng)
    frame = frame[frame.symbol == 'AAA']
    session = dict(date='2026-09-15', open=OPEN, close=OPEN + pd.Timedelta(minutes=390))
    history = sim.ReferenceHistory(frame, [session])
    for minutes in [1, 2, 15, 16] + list(rng.integers(3, 390, 10)):
        end = OPEN + pd.Timedelta(minutes=int(minutes))
        fast = outcome(history.checked_volume, NoTrades(), session, 'AAA', OPEN, end, tmp_path, 'sip')
        slow = outcome(sim.checked_reference_volume, NoTrades(), history.by_day['2026-09-15'], 'AAA',
                       OPEN, end, tmp_path, 'sip')
        assert fast == slow, (minutes, fast, slow)
