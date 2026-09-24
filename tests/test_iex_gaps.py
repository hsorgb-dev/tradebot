"""Missing IEX minutes: signal rules (grid, opening range, crossings) and held positions."""
import pandas as pd
import pytest

import iex_shadow_sim_v12 as sim
import shadow_portfolio_v12 as shadow
import us_orb_scanner_v03 as scanner
import us_orb_test_v02 as base

OPEN = pd.Timestamp('2026-09-23 13:30', tz='UTC')
C = base.Config(test_date='2026-09-23')
S = sim.DryRunSettings()
COLUMNS = ['symbol', 'timestamp', 'open', 'high', 'low', 'close', 'volume', 'vwap', 'feed']


def minute(n):
    return OPEN + pd.Timedelta(minutes=n)


def bars(symbol, closes, skip=(), extra=()):
    """One flat bar per minute index; `skip` leaves minutes without a bar."""
    rows = [dict(symbol=symbol, timestamp=minute(i), open=p, high=p, low=p, close=p,
                 volume=100.0, vwap=p, feed='iex')
            for i, p in enumerate(closes) if i not in skip]
    rows.extend(extra)
    return pd.DataFrame(rows, columns=COLUMNS)


def breakout_path(length=40, cross_at=25):
    """Opening range high 100 (threshold 100.05, cap 100.3); crossing to 100.2."""
    return [100.0] * cross_at + [100.2] * (length - cross_at)


def candidates(frame, decision_minute, settings=S):
    return sim.price_candidates_until(frame, 'AAA', OPEN, minute(decision_minute), C, settings)


def test_reference_suites_still_pass():
    tests, _ = base.self_tests()
    scanner_tests, _ = scanner.scanner_tests()
    assert tests.Bestanden.all() and scanner_tests.Bestanden.all()


def test_complete_data_signals_at_crossing():
    found, opening_bad, later_bad = candidates(bars('AAA', breakout_path()), 40)
    assert [c['decision_time_utc'] for c in found] == [minute(26).isoformat()]
    assert not opening_bad and not later_bad
    assert found[0]['coverage'] == 1 and found[0]['previous_close_age_minutes'] == 1


def test_missing_minute_before_crossing_no_longer_blocks_symbol():
    found, opening_bad, later_bad = candidates(bars('AAA', breakout_path(), skip={18, 22}), 40)
    assert [c['decision_time_utc'] for c in found] == [minute(26).isoformat()]
    assert not opening_bad and not later_bad


def test_crossing_right_after_gap_uses_last_real_close():
    # Minutes 23 and 24 without IEX trade; previous real close is minute 22 (3 min).
    found, _, _ = candidates(bars('AAA', breakout_path(), skip={23, 24}), 40)
    assert len(found) == 1 and found[0]['previous_close_age_minutes'] == 3


def test_stale_previous_close_rejects_crossing():
    # Last real close before the crossing is 7 minutes old.
    found, _, later_bad = candidates(bars('AAA', breakout_path(), skip=set(range(19, 25))), 40)
    assert found == [] and later_bad


def test_missing_signal_bar_gives_no_signal():
    found, _, _ = candidates(bars('AAA', breakout_path(), skip={25}), 40)
    # Minute 26 is the first real bar above the threshold, previous close minute 24.
    assert [c['decision_time_utc'] for c in found] == [minute(27).isoformat()]


@pytest.mark.parametrize('skip, valid', [
    ({3, 7, 11}, True),          # 12 of 15 bars
    ({2, 3, 7, 11}, False),      # 11 of 15 bars
    ({0}, False),                # first opening bar missing
])
def test_opening_range_coverage(skip, valid):
    found, opening_bad, _ = candidates(bars('AAA', breakout_path(), skip=skip), 40)
    assert opening_bad is (not valid)
    assert bool(found) is valid


def test_low_coverage_rejects_crossing():
    # Every other minute after the opening range missing: coverage < 85%.
    skip = {i for i in range(15, 25) if i % 2}
    found, _, later_bad = candidates(bars('AAA', breakout_path(), skip=skip), 40)
    assert found == [] and later_bad


def test_invalid_bar_drops_only_that_minute():
    duplicate = dict(symbol='AAA', timestamp=minute(20), open=100.0, high=100.0, low=100.0,
                     close=100.0, volume=1.0, vwap=100.0, feed='iex')
    found, opening_bad, later_bad = candidates(
        bars('AAA', breakout_path(), extra=[duplicate]), 40)
    assert len(found) == 1 and not opening_bad and later_bad


def test_series_endpoints_allow_short_benchmark_gap():
    qqq = bars('QQQ', [400.0] * 30 + [404.0] * 8, skip={36, 37})
    first, last, missing = sim.checked_qqq_endpoints(qqq, OPEN, minute(38), 2)
    assert (first, last, missing) == (400.0, 404.0, 2)
    with pytest.raises(ValueError):
        sim.checked_qqq_endpoints(qqq, OPEN, minute(39), 2)


def test_current_volume_counts_missing_minutes_as_zero():
    series = sim.minute_series(bars('AAA', breakout_path(), skip={18, 22}), 'AAA', OPEN, minute(30))
    assert series.volume.sum() == 28 * 100.0
    assert int((~series.observed).sum()) == 2


# ---------------- held positions ----------------

def portfolio_with_position(entry_minute=20, price=100.0):
    portfolio = shadow.ShadowPortfolio(C, OPEN, OPEN + pd.Timedelta(hours=6),
                                       settle_seconds=120, max_gap_minutes=3)
    portfolio.update(minute(entry_minute), bars('AAA', []), minute(entry_minute))
    when = minute(entry_minute) + pd.Timedelta(seconds=10)
    quote = dict(quote_result='PASS', quote_time_utc=when.isoformat(), ask=price)
    signal = dict(symbol='AAA', sector='Tech', rank=1, score=50.0, rvol=2.0,
                  decision_time_utc=minute(entry_minute).isoformat())
    assert portfolio.enter(when, signal, quote, 60) == 'VIRTUAL_ENTRY'
    return portfolio


def held_bars(prices):
    """{minute index: (low, close)} for symbol AAA."""
    return pd.DataFrame([dict(symbol='AAA', timestamp=minute(i), open=close, high=close,
                              low=low, close=close, volume=100.0, vwap=close, feed='iex')
                         for i, (low, close) in prices.items()], columns=COLUMNS)


def step(portfolio, upto, frame, seconds=5):
    """Update once per minute, like the live loop, `seconds` after each minute."""
    decision = portfolio.last_decision + pd.Timedelta(minutes=1)
    while decision <= minute(upto):
        portfolio.update(decision, frame, decision + pd.Timedelta(seconds=seconds))
        decision += pd.Timedelta(minutes=1)


def kinds(portfolio, kind):
    return [e for e in portfolio.events if e['kind'] == kind]


def test_late_bar_is_still_stop_checked():
    p = portfolio_with_position()
    # Decisions 21 and 22: bar 21 not delivered yet -> pending, not "missing".
    step(p, 22, held_bars({}))
    pos = p.positions['AAA']
    assert pos['last_bar'] == minute(20) and pos['pending_minutes'] == 1
    assert kinds(p, 'NO_IEX_BAR_WHILE_HELD') == []
    # Bar 21 arrives late with a low through the stop (99).
    step(p, 23, held_bars({21: (98.9, 99.5), 22: (99.5, 99.6)}))
    assert 'AAA' not in p.positions
    assert p.closed[0]['exit_reason'] == 'TRAILING_STOP_PROXY'
    assert p.closed[0]['exit_at_utc'] == minute(21).isoformat()


def test_gap_before_later_bar_counts_as_no_trade_without_degrading():
    p = portfolio_with_position()
    step(p, 22, held_bars({}))
    step(p, 24, held_bars({22: (100.4, 100.5), 23: (100.5, 100.6)}))
    pos = p.positions['AAA']
    assert pos['unobserved_minutes'] == 1 and pos['gap_run'] == 0
    assert pos['last_bar'] == minute(23) and not p.data_degraded
    assert p.gap_symbols() == []


def test_settled_gap_checks_stop_against_fresh_bid():
    p = portfolio_with_position()
    now = minute(24) + pd.Timedelta(seconds=5)   # minute 21 settled (>= 21+1+2 min)
    step(p, 23, held_bars({}))
    assert p.gap_symbols() == []                 # still pending at decision 23
    step(p, 24, held_bars({}))
    pos = p.positions['AAA']
    assert pos['gap_run'] == 1 and pos['last_bar'] == minute(21)
    assert p.gap_symbols() == ['AAA']
    above = dict(quote_result='PASS', bid=99.5, quote_time_utc=now.isoformat())
    p.check_gap_exits(now, {'AAA': above})
    assert 'AAA' in p.positions
    below = dict(quote_result='PASS', bid=98.8, quote_time_utc=now.isoformat())
    p.check_gap_exits(now, {'AAA': below})
    assert p.closed[0]['exit_reason'] == 'STOP_BID_QUOTE_PROXY'
    assert p.closed[0]['exit_price_proxy'] == 98.8


def test_long_gap_leaves_position_at_fresh_bid():
    p = portfolio_with_position()
    now = minute(27) + pd.Timedelta(seconds=5)   # minutes 21-24 settled: 4 > 3
    step(p, 27, held_bars({}))
    assert p.positions['AAA']['gap_run'] == 4
    p.check_gap_exits(now, {'AAA': dict(quote_result='MISSING')})
    assert 'AAA' in p.positions and kinds(p, 'DATA_GAP_EXIT_PRICE_UNAVAILABLE')
    assert p.data_degraded
    quote = dict(quote_result='PASS', bid=100.1, quote_time_utc=now.isoformat())
    p.check_gap_exits(now, {'AAA': quote})
    assert p.closed[0]['exit_reason'] == 'EXIT_DATA_GAP_BID_PROXY'


def test_stale_bid_is_not_used():
    p = portfolio_with_position()
    now = minute(24) + pd.Timedelta(seconds=5)
    step(p, 24, held_bars({}))
    old = dict(quote_result='PASS', bid=98.0,
               quote_time_utc=(now - pd.Timedelta(seconds=5)).isoformat())
    p.check_gap_exits(now, {'AAA': old})
    assert 'AAA' in p.positions


def test_invalid_held_bar_is_logged_once():
    p = portfolio_with_position()
    broken = held_bars({21: (100.0, 100.0)})
    broken.loc[0, 'low'] = 101.0   # low above high
    frame = pd.concat([broken, held_bars({22: (100.1, 100.2)})], ignore_index=True)
    step(p, 24, frame)
    assert len(kinds(p, 'INVALID_HELD_BAR')) == 1 and p.data_degraded
