"""Replay comparison of stop / re-entry variants on synthetic SIP days."""
import json
from pathlib import Path

import pandas as pd
import pytest

import orb_replay_v15 as replay
import orb_variants_v15 as variants
from test_replay_v14 import C, DAY, S, SyntheticMarket, aaa_path, make_store

DAY2 = '2026-09-16'


class ReCrossMarket(SyntheticMarket):
    """AAA: breakout 09:55, stop out ~11:00, second breakout at 11:30 that holds."""

    def price(self, symbol, day, i):
        if day == self.day and symbol == 'AAA' and i >= 120:
            return 100.2, 300.0
        return super().price(symbol, day, i)


@pytest.fixture(scope='module')
def base(tmp_path_factory):
    folder = tmp_path_factory.mktemp('variants')
    store = make_store(folder, ReCrossMarket())
    summary = replay.run_replay(store, folder / 'drive', C, S, DAY, DAY2)
    days = pd.read_csv(summary / 'days.csv')
    return dict(store=store, folder=folder,
                day_folder=Path(days[days.date == DAY].folder.iloc[0]))


def simulate(base, variant):
    trades, report = variants.simulate_day(base['day_folder'], base['store'], C, S, variant)
    return trades


def test_baseline_reproduces_the_base_replay_exactly(base):
    trades = simulate(base, variants.DEFAULT_VARIANTS[0])
    recorded = json.loads((base['day_folder'] / 'shadow_trades.json').read_text())
    # identical except the exit second: the base replay adds simulated request latency
    key = lambda t: (t['symbol'], t['qty'], t['entry'], t['entry_at_utc'],
                     pd.Timestamp(t['exit_at_utc']).floor('min'),
                     round(t['exit_price_proxy'], 6), t['exit_reason'])
    assert [key(t) for t in trades] == [key(t) for t in recorded]
    assert len(trades) == 1 and trades[0]['exit_reason'] == 'TRAILING_STOP_PROXY'


def test_wider_stop_survives_the_pullback(base):
    (trade,) = simulate(base, variants.Variant('A_2.0pct', trailing=0.02))
    assert trade['exit_reason'] == 'PLANNED_CLOSE_BID_PROXY' and trade['trail'] == 0.02
    assert trade['exit_price_proxy'] == pytest.approx(100.19)   # bid at 15:30 NY


def test_reentry_after_stop_takes_the_second_breakout(base):
    trades = simulate(base, variants.Variant('B', max_reentries=1))
    assert [t['entry_number'] for t in trades] == [1, 2]
    first, second = trades
    assert first['exit_reason'] == 'TRAILING_STOP_PROXY'
    assert pd.Timestamp(second['entry_at_utc']) >= pd.Timestamp(first['exit_at_utc']) + pd.Timedelta(minutes=15)
    assert second['exit_reason'] == 'PLANNED_CLOSE_BID_PROXY'


def test_no_reentry_without_the_setting(base):
    assert len(simulate(base, variants.Variant('A', max_reentries=0))) == 1


def test_atr_variant_uses_the_per_symbol_distance(base):
    (trade,) = simulate(base, variants.Variant('A_ATR', atr_multiple=0.5))
    assert trade['trail'] == pytest.approx(0.0075)                # flat daily bars -> clamp minimum


def test_sweep_writes_the_period_comparison(base):
    out = variants.run_sweep(base['store'], base['folder'] / 'drive', C, S, DAY, DAY2, DAY)
    sweep = json.loads((out / 'sweep.json').read_text())
    assert sweep['baseline_matches_base_replay'] is True and sweep['failures'] == []
    comparison = pd.read_csv(out / 'comparison.csv')
    assert len(comparison) == 3 * len(variants.DEFAULT_VARIANTS)
    rows = comparison.set_index(['variant', 'period'])
    learn = rows.loc[('A_1.0pct', f'Festlegen (bis {DAY})')]
    assert learn.days == 1 and learn.trades == 1
    assert rows.loc[('A_1.0pct', f'Pruefen (ab {DAY})')].trades == 0
    assert rows.loc[('B_1.0pct_reentry', 'Gesamt')].reentries == 1
    table = variants.summary_table(out, 'Festlegen')
    assert set(table.variant) == {v.name for v in variants.DEFAULT_VARIANTS}
