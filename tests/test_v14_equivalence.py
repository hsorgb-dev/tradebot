"""V1.4 speeds up minute_series/clean_bars/price_candidates_until with numpy.

The results must stay identical to V1.3.1 on random data with gaps, duplicates,
contradictory and off-grid bars, and prices exactly at or next to the threshold."""
import numpy as np
import pandas as pd
import pytest

import sip_shadow_sim_v13 as old
import sip_shadow_sim_v14 as new
import us_orb_test_v02 as base

OPEN = pd.Timestamp('2026-09-15 13:30', tz='UTC')
C = base.Config()
THRESHOLD = 100.060005               # opening-range high 100.01 * 1.0005
CAP = 100.01 * 1.003


def random_frame(rng, near_threshold):
    n = int(rng.integers(20, 120))
    if near_threshold:
        levels = [100.0, THRESHOLD, THRESHOLD + 1e-12, np.nextafter(THRESHOLD, 0),
                  np.nextafter(THRESHOLD, 200), 100.2, CAP, np.nextafter(CAP, 200), 99.9]
        prices = np.array([100.0] * 15 + list(rng.choice(levels, n)))[:n]
    else:
        prices = 100 + np.cumsum(rng.normal(0, .08, n))
    rows = []
    for i in range(n):
        if rng.random() < .1:
            continue
        stamp = OPEN + pd.Timedelta(minutes=i)
        if rng.random() < .02:
            stamp += pd.Timedelta(seconds=30)
        o, c = (prices[i - 1] if i else prices[i]), prices[i]
        high, low = max(o, c) + .01, min(o, c) - .01
        if rng.random() < .03:
            low = high + 1
        rows.append(dict(symbol='AAA', timestamp=stamp, open=o, high=high, low=low, close=c,
                         volume=float(rng.integers(0, 500)), vwap=c, feed='sip'))
        if rng.random() < .02:
            rows.append(dict(rows[-1]))
    return pd.DataFrame(rows), OPEN + pd.Timedelta(minutes=n)


@pytest.mark.parametrize('near_threshold', [False, True])
def test_numpy_version_matches_v1_3_1(near_threshold):
    rng = np.random.default_rng(11 if near_threshold else 7)
    columns = ['open', 'high', 'low', 'close', 'volume', 'observed', 'invalid', 'last_close', 'coverage']
    crossings = 0
    for _ in range(150):
        frame, decision = random_frame(rng, near_threshold)
        a = old.minute_series(frame, 'AAA', OPEN, decision)
        b = new.minute_series(frame, 'AAA', OPEN, decision)
        pd.testing.assert_frame_equal(a[columns].astype(float), b[columns].astype(float),
                                      check_freq=False)
        assert a.last_seen.isna().tolist() == b.last_seen.isna().tolist()
        assert (a.last_seen.dropna() == b.last_seen.dropna()).all()
        settings = new.DryRunSettings()
        expected = old.price_candidates_until(frame, 'AAA', OPEN, decision, C, settings)
        assert new.price_candidates_until(frame, 'AAA', OPEN, decision, C, settings) == expected
        crossings += len(expected[0])
    assert crossings > 50
