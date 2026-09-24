"""V1.5 modes and orchestration: SIP_ONLY (delivery state), fallback, DUAL_MODE with
BOT 2 in its own spawned process, dual cockpit and the IEX/SIP comparison log."""
import json
from pathlib import Path

import pandas as pd
import pytest

import dual_bot_v15 as dual
import orb_replay_v15 as replay
import sip_shadow_bot_v15 as bot2
from test_iex_paper_bot_v15 import OPEN, FakePaperClient, LiveIexData
from test_live_path_v143 import LIVE_DAY
from test_replay_v14 import C, S, SyntheticMarket, make_store
from v15_child_helpers import free_plan_market


def shared_data(tmp_path, market):
    store = make_store(tmp_path, market)
    calendar = bot2.calendar_payload(market.get_calendar(
        type('R', (), dict(start='2026-06-01', end='2026-12-31'))()))
    return calendar, store.universe()


def test_delivery_state_is_sip_only_without_orders():
    assert dual.DELIVERY_MODE == 'SIP_ONLY'
    assert dual.resolve_mode('SIP_ONLY') == dict(mode='SIP_ONLY', bot1_active=False, bot2_active=True)
    assert dual.resolve_mode('LIVE_MONEY')['mode'] == 'SIP_ONLY'       # unknown -> safe state
    assert dual.resolve_mode(None)['bot1_active'] is False


def test_sip_only_runs_bot2_and_never_touches_the_trading_client(tmp_path):
    market = free_plan_market()
    wall = market.wall
    client = FakePaperClient(wall, market)
    folders = dual.run_session('SIP_ONLY', client, market, ('k', 's'), tmp_path / 'drive', C, S,
                               display=False, serve=False, shared=shared_data(tmp_path, market),
                               clock=wall)
    assert set(folders) == {'SIP_DELAYED_SHADOW'} and client.calls == []
    out = folders['SIP_DELAYED_SHADOW']
    assert json.loads((out / 'status.json').read_text())['orders_sent'] == 0
    page = (tmp_path / 'drive' / 'cockpit_latest.html').read_text(encoding='utf-8')
    assert 'BOT 1 – IEX-Echtzeit-Bot' in page and 'Deaktiviert im Modus SIP_ONLY' in page
    assert 'BOT 2 – SIP-Delayed-Bot' in page and 'SIMULIERT' in page
    assert 'IEX vs. SIP – Vergleich' in page and 'Vergleich erst im DUAL_MODE' in page
    comparison = pd.read_csv(tmp_path / 'drive' / 'v1_5' / 'IEX_SIP_comparison.csv')
    aaa = comparison[comparison.symbol == 'AAA'].iloc[0]
    assert aaa.sip_signal and not aaa.iex_signal and not aaa.comparison_complete


def test_dual_mode_runs_both_bots_isolated(tmp_path):
    # BOT 1: real-time IEX (simulated clock from 09:20 NY); BOT 2: spawned process,
    # real time 16:40 NY -> catches the whole delayed day up.
    clock = replay.ReplayClock(OPEN - pd.Timedelta(minutes=10))
    market = SyntheticMarket(day=LIVE_DAY)
    client = FakePaperClient(clock, market)
    folders = dual.run_session(
        'DUAL_MODE', client, LiveIexData(market, clock), ('k', 's'), tmp_path / 'drive', C, S,
        display=False, serve=False, shared=shared_data(tmp_path, market), clock=clock,
        bot2_process_options=dict(data_factory='v15_child_helpers:free_plan_market',
                                  factory_kwargs={}, clock_factory='v15_child_helpers:wall_clock'))
    assert client.calls == []                                   # no approval -> no order call
    root = tmp_path / 'drive' / 'v1_5'
    iex = json.loads((root / 'IEX_REALTIME_PAPER_SIM' / 'account.json').read_text())
    sip = json.loads((root / 'SIP_DELAYED_SHADOW' / 'account.json').read_text())
    assert list(iex['days']) == [LIVE_DAY] and list(sip['days']) == [LIVE_DAY]
    # separate accounts: each starts at 10,000 and books only its own trade
    assert iex['days'][LIVE_DAY]['start_equity_usd'] == sip['days'][LIVE_DAY]['start_equity_usd'] == 10000
    iex_run = Path(iex['days'][LIVE_DAY]['run_folder'])
    sip_run = Path(sip['days'][LIVE_DAY]['run_folder'])
    assert json.loads((iex_run / 'manifest.json').read_text())['live_feed'] == 'iex'
    assert json.loads((sip_run / 'manifest.json').read_text())['live_feed'] == 'sip'
    comparison = pd.read_csv(root / 'IEX_SIP_comparison.csv')
    aaa = comparison[comparison.symbol == 'AAA'].iloc[0]
    assert aaa.comparison_complete and aaa.same_universe and aaa.iex_signal and aaa.sip_signal
    assert aaa.iex_signal_time_ny == aaa.sip_signal_time_ny == '09:56'
    assert aaa.iex_virtual_entry and aaa.sip_virtual_entry
    assert aaa.iex_or_high == pytest.approx(aaa.sip_or_high)
    series = pd.read_csv(root / 'performance_series.csv')
    assert set(series.series) == {'IEX_SIMULATION', 'SIP_SIMULATION'}
    page = (tmp_path / 'drive' / 'cockpit_latest.html').read_text(encoding='utf-8')
    assert page.count('US-Aktien-Bot · virtuelles Cockpit') == 0
    assert 'BOT 1 – IEX-Echtzeit-Bot' in page and 'BOT 2 – SIP-Delayed-Bot' in page
    assert 'Alpaca-Paper-Orders: AUS (keine Freigabe)' in page


def test_failing_bot2_process_does_not_block_bot1(tmp_path):
    clock = replay.ReplayClock(OPEN - pd.Timedelta(minutes=10))
    market = SyntheticMarket(day=LIVE_DAY)
    client = FakePaperClient(clock, market)
    folders = dual.run_session(
        'DUAL_MODE', client, LiveIexData(market, clock), ('k', 's'), tmp_path / 'drive', C, S,
        display=False, serve=False, shared=shared_data(tmp_path, market), clock=clock,
        bot2_process_options=dict(data_factory='v15_child_helpers:broken_market',
                                  factory_kwargs={}, clock_factory='v15_child_helpers:wall_clock'))
    out = folders['IEX_REALTIME_PAPER']
    assert json.loads((out / 'status.json').read_text())['status'] == 'SHADOW_SIM_COMPLETED_NO_ORDERS'
    root = tmp_path / 'drive' / 'v1_5'
    assert not (root / 'SIP_DELAYED_SHADOW' / 'account.json').exists()
    comparison = pd.read_csv(root / 'IEX_SIP_comparison.csv')
    assert not comparison.comparison_complete.any()        # never compared with a missing SIP day
