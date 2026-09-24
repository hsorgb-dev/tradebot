"""MODE='delayed': the live loop on today's SIP data, 16 minutes behind real time.

The free Alpaca plan serves SIP only when it is at least 15 minutes old: no
request may ever end later than (real now - 15 min)."""
import json

import pandas as pd
import pytest

import shadow_cockpit_v142 as cockpit_ui
import sip_replay_v14 as replay
from test_live_path_v143 import LIVE_DAY, CountingCockpit
from test_replay_v14 import C, S, SyntheticMarket, make_store


class FreePlanMarket(SyntheticMarket):
    """Rejects recent SIP exactly like Alpaca's free plan and records requests."""

    def __init__(self, wall, **kwargs):
        super().__init__(**kwargs)
        self.wall, self.latest_end = wall, []

    def _check(self, request):
        end = replay.as_utc(request.end)
        self.latest_end.append(self.wall.now() - end)
        if end > self.wall.now() - pd.Timedelta(minutes=15):
            raise RuntimeError('subscription does not permit querying recent SIP data')

    def get_stock_bars(self, request):
        self._check(request)
        return super().get_stock_bars(request)

    def get_stock_quotes(self, request):
        self._check(request)
        return super().get_stock_quotes(request)

    def get_stock_trades(self, request):
        self._check(request)
        return super().get_stock_trades(request)


def test_delayed_clock_requires_more_than_fifteen_minutes():
    with pytest.raises(ValueError):
        replay.DelayedClock(15)


def test_delayed_data_never_asks_for_recent_sip(tmp_path):
    from alpaca.data.requests import StockBarsRequest, StockQuotesRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.common.enums import Sort
    opening = pd.Timestamp(f'{LIVE_DAY} 09:30', tz='America/New_York').tz_convert('UTC')
    wall = replay.ReplayClock(opening + pd.Timedelta(minutes=46, seconds=7))
    market = FreePlanMarket(wall, day=LIVE_DAY)
    clock = replay.DelayedClock(16, base=wall)
    data = replay.DelayedData(market, clock)
    answer = data.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=['AAA'], timeframe=TimeFrame.Minute, start=opening.to_pydatetime(),
        end=wall.now().to_pydatetime()))                     # asks up to real now
    # delayed now 30:07; minute 29 closed at 30:00 and was published at 30:02
    assert answer.df.timestamp.max() == opening + pd.Timedelta(minutes=29)
    quotes = data.get_stock_quotes(StockQuotesRequest(
        symbol_or_symbols='AAA', start=(clock.now() - pd.Timedelta(seconds=10)).to_pydatetime(),
        end=wall.now().to_pydatetime(), limit=1, sort=Sort.DESC))
    assert all(replay.as_utc(q.timestamp) <= clock.now() for q in quotes.data['AAA'])
    assert min(market.latest_end) >= pd.Timedelta(minutes=16)


def test_delayed_run_full_day(tmp_path):
    session_open = pd.Timestamp(f'{LIVE_DAY} 09:30', tz='America/New_York').tz_convert('UTC')
    wall = replay.ReplayClock(session_open + pd.Timedelta(minutes=16 - 10))   # 10 min before delayed open
    market = FreePlanMarket(wall, day=LIVE_DAY)
    store = make_store(tmp_path, market)
    universe = store.universe()
    paper = replay.ReplayBroker(store, wall)          # stands in for the real paper account
    ui = CountingCockpit(latest_path=tmp_path / 'cockpit_latest.html')
    out = replay.run_delayed(paper, market, tmp_path / 'drive', C, S, cockpit=ui,
                             base_clock=wall, universe_loader=lambda broker: universe)
    load = lambda name: json.loads((out / name).read_text())
    assert out.parent.parent.name == 'shadow_delayed_v1_4'
    manifest = load('manifest.json')
    assert manifest['mode'] == 'DELAYED_SIP_VIRTUAL_PORTFOLIO' and manifest['replay']['lag_minutes'] == 16
    assert load('sip_access_check.json')['result'] == 'DELAYED_HISTORICAL_SIP'
    assert load('status.json')['status'] == 'SHADOW_SIM_COMPLETED_NO_ORDERS'
    assert load('coverage.json')['missed_decision_minutes'] == 0
    (trade,) = load('shadow_trades.json')
    assert trade['symbol'] == 'AAA' and trade['exit_reason'] == 'TRAILING_STOP_PROXY'
    assert trade['outcome_unreliable'] is False
    assert ui.stages.count('SIGNALPRUEFUNG') == 329 and ui.stages[-1] == 'FINISHED'
    assert min(market.latest_end) >= pd.Timedelta(minutes=16)   # never recent SIP
    page = (out / 'cockpit.html').read_text(encoding='utf-8')
    assert 'Verzögerte Live-Beobachtung · SIP 16 Min. versetzt' in page
    assert 'data-lag-ms="960000"' in (tmp_path / 'cockpit_latest.html').read_text()
    assert 'Start-Snapshot des Alpaca-Paper-Kontos' in page


def test_delayed_broker_exposes_only_read_methods():
    public = {name for name in dir(replay.DelayedBroker) if not name.startswith('_')}
    assert public == {'get_clock', 'get_calendar', 'get_account', 'get_all_positions',
                      'get_orders', 'get_all_assets'}
