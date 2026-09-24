"""V1.4.2 cockpit: content, refresh cadence, Colab display, files, failures, isolation.

Runs full synthetic replay days (see test_replay_v14) with the cockpit attached."""
import copy
import json
from types import SimpleNamespace

import pandas as pd
import pytest

import shadow_cockpit_v142 as cockpit_ui
import shadow_portfolio_v14 as shadow
import sip_replay_v14 as replay
import us_orb_test_v02 as base
from test_replay_v14 import C, DAY, NY, S, SyntheticMarket, aaa_path, make_store

OPEN = pd.Timestamp(f'{DAY} 09:30', tz=NY).tz_convert('UTC')


def minute(n):
    return OPEN + pd.Timedelta(minutes=n)


class RecordingCockpit(cockpit_ui.Cockpit):
    """Headless cockpit that keeps a copy of every painted snapshot."""

    def __init__(self):
        super().__init__(display_enabled=False)
        self.painted = []

    def update(self, *args, **kwargs):
        data = super().update(*args, **kwargs)
        if data is not None:
            self.painted.append(json.loads(json.dumps(data, default=str)))
        return data


def run_day(folder, cockpit=None, rs=replay.ReplaySettings(), market=None, data_class=None):
    market = market or SyntheticMarket()
    store = make_store(folder, market)
    if data_class is not None:
        replay.ReplayData, original = data_class, replay.ReplayData
    try:
        out = replay.replay_day(store, DAY, folder / 'drive', C, S,
                                replay.replay_settings_default(), rs, cockpit=cockpit)
    finally:
        if data_class is not None:
            replay.ReplayData = original
    return SimpleNamespace(folder=out, market=market, cockpit=cockpit)


def load(folder, name):
    return json.loads((folder / name).read_text())


@pytest.fixture(scope='module')
def with_cockpit(tmp_path_factory):
    return run_day(tmp_path_factory.mktemp('with'), RecordingCockpit())


@pytest.fixture(scope='module')
def without_cockpit(tmp_path_factory):
    return run_day(tmp_path_factory.mktemp('without'))


def open_views(run):
    return [p for p in run.cockpit.painted if p['open_positions']]


# ---------- isolation: no extra data, no orders, identical results ----------

def test_cockpit_changes_nothing_but_the_view(with_cockpit, without_cockpit):
    assert with_cockpit.market.calls == without_cockpit.market.calls
    assert load(with_cockpit.folder, 'shadow_trades.json') == \
        load(without_cockpit.folder, 'shadow_trades.json')
    kinds = lambda run: [(e['kind'], e.get('symbol')) for e in load(run.folder, 'shadow_events.json')]
    assert kinds(with_cockpit) == kinds(without_cockpit)
    assert load(with_cockpit.folder, 'status.json')['status'] == \
        load(without_cockpit.folder, 'status.json')['status'] == 'SHADOW_SIM_COMPLETED_NO_ORDERS'
    assert all(p['orders_sent'] == 0 for p in with_cockpit.cockpit.painted)


def test_cockpit_module_has_no_data_or_order_calls():
    source = open(cockpit_ui.__file__, encoding='utf-8').read()
    for forbidden in ('get_stock', 'submit_order', 'TradingClient', 'requests', 'alpaca'):
        assert forbidden not in source


# ---------- content of the open-position view ----------

def test_open_position_shows_last_bar_time_and_trailing_stop(with_cockpit):
    views = open_views(with_cockpit)
    assert views, 'no cockpit view while AAA was open'
    (trade,) = load(with_cockpit.folder, 'shadow_trades.json')
    entry_minute = pd.Timestamp(trade['entry_at_utc']).floor('min')
    first = views[0]['open_positions'][0]
    assert first['symbol'] == 'AAA' and first['mark_is_entry_proxy']
    assert first['last_close_proxy'] == pytest.approx(trade['entry'])
    checked = 0
    for view in views:
        (pos,) = view['open_positions']
        if pos['mark_is_entry_proxy']:
            continue
        bar = pd.Timestamp(pos['last_close_minute_utc'])
        # the displayed candle is the last completed minute before the decision
        assert bar == pd.Timestamp(view['checked_decision_utc']) - pd.Timedelta(minutes=1)
        i = int((bar - OPEN) / pd.Timedelta(minutes=1))
        assert pos['last_close_proxy'] == pytest.approx(aaa_path(i))
        highs = [max(aaa_path(k - 1), aaa_path(k))
                 for k in range(int((entry_minute - OPEN) / pd.Timedelta(minutes=1)) + 1, i + 1)]
        expected_stop = max([trade['entry']] + highs) * 0.99
        assert pos['stop'] == pytest.approx(expected_stop)
        assert pos['pnl_open_proxy_usd'] == pytest.approx(pos['qty'] * (aaa_path(i) - trade['entry']))
        checked += 1
    assert checked >= 5


def test_signal_row_shows_ranking_and_virtual_entry(with_cockpit):
    rows = [s for view in with_cockpit.cockpit.painted for s in view['signals']
            if s['symbol'] == 'AAA' and s['time'] == minute(26).isoformat()]
    assert rows
    assert rows[-1]['status'] == 'WATCHLIST_NO_ORDER'
    assert rows[-1]['shadow_status'] == 'VIRTUAL_ENTRY'


# ---------- refresh cadence ----------

def test_replay_paints_every_five_minutes_and_on_events(with_cockpit):
    painted = with_cockpit.cockpit.painted
    closed_before = 0
    for view in painted:
        at = pd.Timestamp(view['updated_at_utc'])
        if view['stage'] in ('SIGNALPRUEFUNG', 'POSITIONEN_UEBERWACHEN') and at.minute % 5:
            assert view['signals'] or view['closed_count'] != closed_before
        closed_before = view['closed_count']
    decisions = {pd.Timestamp(v['checked_decision_utc']) for v in painted if v['checked_decision_utc']}
    for n in range(20, 360, 5):   # 09:50 ... 15:25 NY
        assert minute(n) in decisions, f'no paint for decision {minute(n)}'
    exit_views = [v for v in painted if v['closed_count'] == 1]
    assert exit_views and pd.Timestamp(exit_views[0]['checked_decision_utc']) == minute(91)


# ---------- files and state after the close ----------

def test_files_after_close_match_last_view(with_cockpit):
    folder = with_cockpit.folder
    snapshot = load(folder, 'cockpit_snapshot.json')
    assert snapshot == with_cockpit.cockpit.painted[-1]
    assert snapshot['stage'] == 'FINISHED' and snapshot['open_positions'] == []
    (closed,) = snapshot['closed_trades']
    assert closed['symbol'] == 'AAA' and closed['reason'] == 'TRAILING_STOP_PROXY'
    assert snapshot['realized_pnl_proxy_usd'] == pytest.approx(load(folder, 'shadow_state.json')['realized_pnl_usd'])
    page = (folder / 'cockpit.html').read_text(encoding='utf-8')
    assert 'Lauf beendet' in page and 'TRAILING_STOP_PROXY' in page
    assert 'Kursabruf nur für offene Positionen' in page     # 15:13 bar explained at 16:00
    kinds = [e['kind'] for e in snapshot['recent_events']]
    assert 'BAR_REVIEWED' not in kinds and kinds[-2:] == ['VIRTUAL_ENTRY', 'VIRTUAL_EXIT']
    assert not list(folder.glob('*.incomplete'))
    stages = [v['stage'] for v in with_cockpit.cockpit.painted]
    assert stages.index('GEPLANTER_TAGESABSCHLUSS') < stages.index('FINISHED') == len(stages) - 1


def test_replay_view_does_not_claim_an_alpaca_account(with_cockpit):
    page = (with_cockpit.folder / 'cockpit.html').read_text(encoding='utf-8')
    assert 'Start-Snapshot des Alpaca-Paper-Kontos' not in page


# ---------- failures ----------

def test_failed_start_leaves_readable_failed_cockpit(tmp_path):
    market = SyntheticMarket()
    store = make_store(tmp_path, market)
    store.set_universe(pd.DataFrame([dict(symbol='AAA', company='AAA', sector='Technology',
                                          tradable_now=False, universe_result='NOT_TRADABLE')]),
                       dict(source='synthetic_test'))
    cockpit = RecordingCockpit()
    with pytest.raises(ValueError, match='Keine zulässigen Aktien'):
        replay.replay_day(store, DAY, tmp_path / 'drive', C, S,
                          replay.replay_settings_default(), cockpit=cockpit)
    (folder,) = (tmp_path / 'drive' / 'shadow_replay_v1_4' / DAY).iterdir()
    assert load(folder, 'cockpit_snapshot.json')['stage'] == 'FAILED'
    assert 'Lauf mit Fehler beendet' in (folder / 'cockpit.html').read_text(encoding='utf-8')
    assert (folder / 'failed.json').exists()


class BrokenCockpit(cockpit_ui.Cockpit):
    def update(self, *args, **kwargs):
        raise RuntimeError('display broken')


def test_broken_cockpit_never_changes_the_trading_result(tmp_path, without_cockpit):
    run = run_day(tmp_path, BrokenCockpit(display_enabled=False))
    assert load(run.folder, 'shadow_trades.json') == load(without_cockpit.folder, 'shadow_trades.json')
    assert load(run.folder, 'coverage.json')['cockpit_error'] == 'RuntimeError'
    assert load(run.folder, 'status.json')['status'] == 'SHADOW_SIM_COMPLETED_NO_ORDERS'


# ---------- Colab display integration ----------

def test_colab_display_is_created_once_and_updated_in_place(tmp_path, monkeypatch):
    import IPython.display
    calls = []

    class Handle:
        def update(self, obj):
            calls.append(('update', obj.data))

    def fake_display(obj, display_id=None):
        calls.append(('display', display_id))
        return Handle()

    monkeypatch.setattr(IPython.display, 'display', fake_display)
    portfolio = shadow.ShadowPortfolio(C, OPEN, OPEN + pd.Timedelta(hours=6))
    ui = cockpit_ui.Cockpit(display_enabled=True)
    ui.update(tmp_path / 'a', portfolio, minute(20), minute(20), 'SIGNALPRUEFUNG', 'live')
    ui.update(tmp_path / 'a', portfolio, minute(21), minute(21), 'SIGNALPRUEFUNG', 'live')
    ui.update(tmp_path / 'b', portfolio, minute(22), minute(22), 'SIGNALPRUEFUNG', 'live')
    assert calls[0] == ('display', True)
    assert [c[0] for c in calls] == ['display', 'update', 'update']
    assert 'virtuelles Cockpit' in calls[-1][1]


def test_live_paints_after_every_decision(tmp_path):
    portfolio = shadow.ShadowPortfolio(C, OPEN, OPEN + pd.Timedelta(hours=6))
    ui = RecordingCockpit()
    for n in range(20, 30):
        ui.update(tmp_path, portfolio, minute(n) + pd.Timedelta(seconds=6), minute(n),
                  'SIGNALPRUEFUNG', 'live')
    assert len(ui.painted) == 10


def test_html_escapes_external_text(tmp_path):
    portfolio = shadow.ShadowPortfolio(C, OPEN, OPEN + pd.Timedelta(hours=6))
    ui = RecordingCockpit()
    ui.update(tmp_path, portfolio, minute(20), minute(20), 'SIGNALPRUEFUNG', 'live',
              signals=[dict(symbol='<script>x</script>', status='<b>', decision_time_utc=minute(20))])
    page = (tmp_path / 'cockpit.html').read_text(encoding='utf-8')
    assert '<script>x' not in page and '&lt;script&gt;x' in page


# ---------- late held bars: no backdated fill, unreliable outcome ----------

def held_position():
    p = shadow.ShadowPortfolio(C, OPEN, OPEN + pd.Timedelta(hours=6))
    p.update(minute(20), pd.DataFrame(columns=replay.BAR_COLUMNS + ['feed']), minute(20))
    when = minute(20) + pd.Timedelta(seconds=10)
    quote = dict(quote_result='PASS', quote_time_utc=when.isoformat(), ask=100.0)
    signal = dict(symbol='AAA', sector='Tech', rank=1, score=50.0, rvol=2.0,
                  decision_time_utc=minute(20).isoformat())
    assert p.enter(when, signal, quote, 60) == 'VIRTUAL_ENTRY'
    return p


def bar(i, low, close, feed='sip'):
    return dict(symbol='AAA', timestamp=minute(i), open=close, high=close, low=low,
                close=close, volume=100.0, vwap=close, feed=feed)


def test_late_stop_bar_is_not_backdated_and_marks_the_trade():
    p = held_position()
    p.update(minute(22), pd.DataFrame([], columns=replay.BAR_COLUMNS + ['feed']),
             minute(22) + pd.Timedelta(seconds=5))          # bar 21 still pending
    late = pd.DataFrame([bar(21, 98.0, 99.5)])                # low through the 99.00 stop
    p.update(minute(23), late, minute(23) + pd.Timedelta(seconds=5))
    assert 'AAA' in p.positions                               # no fill from the old low
    assert [e['kind'] for e in p.events].count('LATE_HELD_BAR_STOP_NOT_BACKDATED') == 1
    assert p.gap_symbols() == ['AAA']
    now = minute(23) + pd.Timedelta(seconds=6)
    p.check_gap_exits(now, {'AAA': dict(quote_result='PASS', bid=98.9,
                                        quote_time_utc=now.isoformat())})
    (trade,) = p.closed
    assert trade['exit_reason'] == 'STOP_BID_QUOTE_PROXY' and trade['exit_at_utc'] == now.isoformat()
    assert trade['exit_price_proxy'] == 98.9 and trade['outcome_unreliable'] is True
    report = p.report()
    assert report['unreliable_virtual_trades'] == 1
    view = cockpit_ui.snapshot(p, now, minute(23), 'SIGNALPRUEFUNG', 'live', 1)
    assert view['closed_trades'][0]['outcome_unreliable'] is True
    assert 'unsicher' in cockpit_ui.render_html(view)


def test_open_position_view_shows_data_gaps():
    p = held_position()
    p.update(minute(24), pd.DataFrame([], columns=replay.BAR_COLUMNS + ['feed']),
             minute(24) + pd.Timedelta(seconds=5))              # 21 settled gap, 22-23 pending
    view = cockpit_ui.snapshot(p, minute(24), minute(24), 'SIGNALPRUEFUNG', 'live', 1)
    (pos,) = view['open_positions']
    assert pos['unobserved_minutes'] == 1 and pos['pending_minutes'] == 2
    page = cockpit_ui.render_html(view)
    assert '1 Min. ohne Kerze' in page and '2 Min. ausstehend' in page


class SlowBarData(replay.ReplayData):
    """Minute-bar requests of the replay day take 12 s (e.g. a slow live fetch)."""

    def get_stock_bars(self, request):
        answer = super().get_stock_bars(request)
        if getattr(request.timeframe, 'value', '') == '1Min' and \
                replay.ny_day(request.start) == DAY:
            self.clock.advance(12)
        return answer


def test_slow_bar_request_does_not_make_timely_bars_late(tmp_path, without_cockpit):
    """A bar that was already there when the request started is not 'late'."""
    run = run_day(tmp_path, data_class=SlowBarData)
    (trade,) = load(run.folder, 'shadow_trades.json')
    assert trade['exit_reason'] == 'TRAILING_STOP_PROXY' and trade['outcome_unreliable'] is False
    kinds = [e['kind'] for e in load(run.folder, 'shadow_events.json')]
    assert 'LATE_HELD_BAR_STOP_NOT_BACKDATED' not in kinds
