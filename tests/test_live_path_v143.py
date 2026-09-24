"""Smoke test of the LIVE code path (replay=None) over one synthetic day.

Everything the live run does is exercised - real-time SIP access check, fixed
control day, live output folder, per-minute cockpit with browser file - only
clock, market data and broker are simulated (no network, no orders)."""
import json

import pandas as pd

import shadow_cockpit_v142 as cockpit_ui
import sip_replay_v14 as replay
import sip_shadow_sim_v14 as sim
from test_replay_v14 import C, NY, S, SyntheticMarket, make_store

LIVE_DAY = '2026-09-23'   # after the fixed SIP control day 2026-09-22


class CountingCockpit(cockpit_ui.Cockpit):
    def __init__(self, **kwargs):
        super().__init__(display_enabled=False, **kwargs)
        self.stages = []

    def update(self, *args, **kwargs):
        data = super().update(*args, **kwargs)
        if data is not None:
            self.stages.append(data['stage'])
        return data


def test_live_path_runs_a_full_day(tmp_path):
    market = SyntheticMarket(day=LIVE_DAY)
    store = make_store(tmp_path, market)
    (session,) = store.sessions(LIVE_DAY, LIVE_DAY)
    past = store.sessions('2026-07-01', LIVE_DAY)[:-1][-C.rvol_days:]
    for day in [p['date'] for p in past] + [LIVE_DAY, '2026-09-22']:
        store.day_minutes(['AAA', 'BBB', 'CCC', 'QQQ', 'AAPL', 'MSFT', 'NVDA'], day)
    clock = replay.ReplayClock(session['open'] - pd.Timedelta(minutes=10))
    universe = store.universe()
    ui = CountingCockpit(latest_path=tmp_path / 'cockpit_latest.html')
    settings = sim.DryRunSettings(print_every_minute=False)   # exactly as the notebook's live cell
    out = sim.run_dryrun(replay.ReplayBroker(store, clock), replay.ReplayData(store, clock),
                         tmp_path / 'drive', C, S, settings, clock=clock,
                         universe_loader=lambda broker: universe, replay=None, cockpit=ui)
    assert out.parent.parent.name == 'shadow_sim_v1_4'
    load = lambda name: json.loads((out / name).read_text())
    assert load('manifest.json')['mode'] == 'FLEXIBLE_START_VIRTUAL_PORTFOLIO'
    assert load('sip_access_check.json')['result'] == 'RECENT_SIP_BARS_AND_QUOTES_ACCEPTED'
    assert load('sip_control.json')['bars_per_symbol'] == {'AAPL': 15, 'MSFT': 15, 'NVDA': 15}
    assert load('status.json')['status'] == 'SHADOW_SIM_COMPLETED_NO_ORDERS'
    coverage = load('coverage.json')
    assert coverage['missed_decision_minutes'] == 0 and coverage['cockpit_error'] is None
    (trade,) = load('shadow_trades.json')
    assert trade['symbol'] == 'AAA' and trade['exit_reason'] == 'TRAILING_STOP_PROXY'
    # live: one view per computed minute (09:46-15:14 signals, then held-position minutes)
    assert ui.stages.count('SIGNALPRUEFUNG') == 329
    assert ui.stages[0] == 'WARTE_AUF_SIGNALFENSTER' and ui.stages[-1] == 'FINISHED'
    assert 'GEPLANTER_TAGESABSCHLUSS' in ui.stages
    assert load('cockpit_snapshot.json')['stage'] == 'FINISHED'
    assert 'Start-Snapshot des Alpaca-Paper-Kontos' in (out / 'cockpit.html').read_text()
    assert 'http-equiv="refresh"' in (tmp_path / 'cockpit_latest.html').read_text()
    assert (out / 'session_bars_sip.csv').exists()          # saved every minute live
