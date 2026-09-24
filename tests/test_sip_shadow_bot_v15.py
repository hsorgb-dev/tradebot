"""BOT 2 – SIP_DELAYED_SHADOW: virtual only, delayed SIP, catch-up restart, idempotent booking."""
import ast
import json
from pathlib import Path

import pandas as pd
import pytest

import bot_accounts_v15 as accounts
import orb_replay_v15 as replay
import sip_shadow_bot_v15 as bot2
from test_delayed_v143 import FreePlanMarket
from test_live_path_v143 import LIVE_DAY, CountingCockpit
from test_replay_v14 import C, S, make_store

OPEN = pd.Timestamp(f'{LIVE_DAY} 09:30', tz='America/New_York').tz_convert('UTC')


def setup(tmp_path, wall_start):
    wall = replay.ReplayClock(wall_start)
    market = FreePlanMarket(wall, day=LIVE_DAY)
    store = make_store(tmp_path, market)
    calendar = bot2.calendar_payload(market.get_calendar(
        type('R', (), dict(start='2026-06-01', end='2026-12-31'))()))
    return wall, market, calendar, store.universe()


def run(tmp_path, wall, market, calendar, universe, cockpit=None):
    return bot2.run_sip_shadow_day(market, tmp_path / 'drive', C, S, calendar, universe,
                                   cockpit=cockpit, base_clock=wall)


def load(folder, name):
    return json.loads((Path(folder) / name).read_text())


def test_full_day_books_the_virtual_account(tmp_path):
    wall, market, calendar, universe = setup(tmp_path, OPEN + pd.Timedelta(minutes=6))
    ui = CountingCockpit()
    out = run(tmp_path, wall, market, calendar, universe, ui)
    assert out.parent.parent.name == 'runs' and out.parents[2].name == 'SIP_DELAYED_SHADOW'
    assert load(out, 'status.json')['status'] == 'SHADOW_SIM_COMPLETED_NO_ORDERS'
    assert load(out, 'status.json')['orders_sent'] == 0
    (trade,) = load(out, 'shadow_trades.json')
    assert trade['symbol'] == 'AAA' and trade['mfe_usd'] > 0 and trade['mae_usd'] <= 0
    assert min(market.latest_end) >= pd.Timedelta(minutes=16)          # never recent SIP
    ledger = accounts.BotLedger(tmp_path / 'drive' / 'v1_5', 'SIP_DELAYED_SHADOW')
    assert ledger.booked_days() == [LIVE_DAY]
    assert ledger.equity() == pytest.approx(10000 + trade['pnl_usd'])
    folder = tmp_path / 'drive' / 'v1_5' / 'SIP_DELAYED_SHADOW'
    trades = pd.read_csv(folder / 'SIP_DELAYED_SHADOW_trades.csv')
    assert list(trades.symbol) == ['AAA'] and trades.trade_id.is_unique
    signals = pd.read_csv(folder / 'SIP_DELAYED_SHADOW_signals.csv')
    assert {'AAA', 'CCC'} <= set(signals.symbol) and signals.processing_utc.notna().all()
    daily = pd.read_csv(folder / 'SIP_DELAYED_SHADOW_daily_performance.csv')
    assert daily.pnl_usd.iloc[0] == pytest.approx(trade['pnl_usd'])
    events = load(out, 'shadow_events.json')
    assert all('processing_utc' in e and 'at_utc' in e for e in events)
    assert 'SIMULIERT' in (out / 'cockpit.html').read_text(encoding='utf-8')


def test_restart_mid_day_catches_up_chronologically_and_never_books_twice(tmp_path):
    # Real time 13:00 NY: the bot re-simulates 09:20-12:44 quickly, then follows with 16 min lag.
    wall, market, calendar, universe = setup(tmp_path, OPEN + pd.Timedelta(hours=3, minutes=30))
    out = run(tmp_path, wall, market, calendar, universe)
    manifest = load(out, 'manifest.json')
    assert manifest['replay']['catch_up_from_open'] is True
    assert load(out, 'coverage.json')['session_scope'] == 'FULL_SIGNAL_WINDOW'
    (trade,) = load(out, 'shadow_trades.json')
    assert trade['symbol'] == 'AAA' and trade['exit_reason'] == 'TRAILING_STOP_PROXY'
    assert min(market.latest_end) >= pd.Timedelta(minutes=16)
    # a second start the same day: already booked -> same folder, no new booking
    again = run(tmp_path, wall, market, calendar, universe)
    assert again == out
    ledger = accounts.BotLedger(tmp_path / 'drive' / 'v1_5', 'SIP_DELAYED_SHADOW')
    assert ledger.booked_days() == [LIVE_DAY] and ledger.equity() == pytest.approx(10000 + trade['pnl_usd'])


def test_incomplete_run_is_not_booked(tmp_path):
    ledger = accounts.BotLedger(tmp_path, 'SIP_DELAYED_SHADOW')
    folder = tmp_path / 'run'
    folder.mkdir()
    (folder / 'failed.json').write_text('{}')
    assert ledger.book(LIVE_DAY, folder) is False
    assert ledger.booked_days() == [] and ledger.state['rejected_bookings'][0]['reason'] == 'RUN_NOT_COMPLETE'


def test_bot2_cannot_reach_any_order_method():
    source = Path(bot2.__file__).read_text(encoding='utf-8')
    imports = {n.module for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ImportFrom)} | \
        {a.name for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m and ('alpaca.trading' in m or 'paper_orders' in m or 'iex_paper_bot' in m)
                   for m in imports)
    public = {n for n in dir(bot2.VirtualBroker) if not n.startswith('_')}
    assert public == {'get_clock', 'get_calendar', 'get_account', 'get_all_positions', 'get_orders'}
    for name in ('submit_order', 'cancel_orders', 'close_position', 'close_all_positions',
                 'replace_order_by_id', 'cancel_order_by_id'):
        assert name not in source
