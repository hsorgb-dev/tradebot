"""V1.4 replay: the unchanged live loop over a synthetic SIP market, end to end."""
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

import sip_replay_v14 as replay
import sip_shadow_sim_v14 as sim
import us_orb_scanner_v03 as scanner
import us_orb_test_v02 as base

NY = 'America/New_York'
DAY = '2026-09-15'
C = base.Config(test_date=DAY)
S = scanner.ScannerConfig()
SECTORS = {'AAA': 'Technology', 'BBB': 'Technology', 'CCC': 'Health Care'}


def aaa_path(i):
    """Breakout at minute 25 (signal 26), rally to 101.5, drop through the stop at 90."""
    if i < 25:
        return 100.0
    if i <= 26:
        return 100.2
    if i <= 60:
        return 100.2 + (i - 26) * (101.5 - 100.2) / 34
    return 101.5 if i < 90 else 100.0


def ccc_path(i):
    """Price breakout at minute 40 without extra volume -> VOLUME_REJECT."""
    return 80.0 if i < 40 else 80.1


class SyntheticMarket:
    """Stands in for Alpaca's StockHistoricalDataClient and TradingClient."""

    def __init__(self, day=DAY):
        self.day = day
        self.calls = 0

    def session_days(self, start, end):
        current, end = date.fromisoformat(str(start)[:10]), date.fromisoformat(str(end)[:10])
        while current <= end:
            if current.weekday() < 5:
                yield current
            current += timedelta(days=1)

    def get_calendar(self, request):
        return [SimpleNamespace(date=d, open=datetime(d.year, d.month, d.day, 9, 30),
                                close=datetime(d.year, d.month, d.day, 16, 0))
                for d in self.session_days(request.start, request.end)]

    def price(self, symbol, day, i):
        if day == self.day and symbol == 'AAA':
            return aaa_path(i), 300.0
        if day == self.day and symbol == 'CCC':
            return ccc_path(i), 100.0
        return {'QQQ': 400.0, 'BBB': 50.0, 'CCC': 80.0}.get(symbol, 100.0), 100.0

    def get_stock_bars(self, request):
        self.calls += 1
        symbols = [request.symbol_or_symbols] if isinstance(request.symbol_or_symbols, str) \
            else request.symbol_or_symbols
        start, end = replay.as_utc(request.start), replay.as_utc(request.end)
        rows = []
        for d in self.session_days(start.tz_convert(NY).date(), end.tz_convert(NY).date()):
            opening = pd.Timestamp(f'{d} 09:30', tz=NY).tz_convert('UTC')
            for symbol in symbols:
                if request.timeframe.value == '1Day':
                    rows.append(dict(symbol=symbol, timestamp=pd.Timestamp(str(d), tz=NY).tz_convert('UTC'),
                                     open=100.0, high=100.0, low=100.0, close=100.0,
                                     volume=1e6, vwap=100.0))
                    continue
                previous = self.price(symbol, str(d), 0)[0]
                for i in range(390):
                    close, volume = self.price(symbol, str(d), i)
                    rows.append(dict(symbol=symbol, timestamp=opening + pd.Timedelta(minutes=i),
                                     open=previous, high=max(previous, close), low=min(previous, close),
                                     close=close, volume=volume, vwap=close))
                    previous = close
        frame = pd.DataFrame(rows, columns=replay.BAR_COLUMNS)
        frame = frame[(frame.timestamp >= start) & (frame.timestamp <= end)]
        return SimpleNamespace(data={s: [1] for s in frame.symbol.unique()}, df=frame)

    def get_stock_quotes(self, request):
        """One quote 0.2 s before the request end around the current minute's price."""
        self.calls += 1
        symbol = request.symbol_or_symbols
        at = replay.as_utc(request.end) - pd.Timedelta(seconds=0.2)
        opening = pd.Timestamp(f'{at.tz_convert(NY).date()} 09:30', tz=NY).tz_convert('UTC')
        i = max(0, min(389, int((at - opening) / pd.Timedelta(minutes=1))))
        price = self.price(symbol, str(at.tz_convert(NY).date()), i)[0]
        quote = SimpleNamespace(timestamp=at.to_pydatetime(), bid_price=price - 0.01,
                                ask_price=price + 0.01, bid_size=5, ask_size=5)
        return SimpleNamespace(data={symbol: [quote]})

    def get_stock_trades(self, request):
        self.calls += 1
        return SimpleNamespace(data={})


def make_store(folder, market):
    store = replay.ReplayStore(folder / 'cache', market, market, spacing_seconds=0)
    store.set_universe(pd.DataFrame([dict(symbol=s, company=s, sector=sector, tradable_now=True,
                                          universe_result='PASS') for s, sector in SECTORS.items()]),
                       dict(source='synthetic_test'))
    return store


@pytest.fixture(scope='module')
def replayed(tmp_path_factory):
    folder = tmp_path_factory.mktemp('replay')
    market = SyntheticMarket()
    store = make_store(folder, market)
    summary = replay.run_replay(store, folder / 'drive', C, S, DAY, DAY)
    return SimpleNamespace(folder=folder, market=market, store=store, summary=summary)


def day_folder(result):
    (folder,) = (result.folder / 'drive' / 'shadow_replay_v1_4' / DAY).iterdir()
    return folder


def load(folder, name):
    return json.loads((folder / name).read_text())


def test_replay_day_trades_breakout_and_stop(replayed):
    folder = day_folder(replayed)
    (trade,) = load(folder, 'shadow_trades.json')
    assert trade['symbol'] == 'AAA' and trade['qty'] == 29
    assert trade['entry'] == pytest.approx(100.21)
    assert trade['exit_reason'] == 'TRAILING_STOP_PROXY'
    assert trade['exit_price_proxy'] == pytest.approx(101.5 * 0.99)
    opening = pd.Timestamp(f'{DAY} 09:30', tz=NY).tz_convert('UTC')
    # V1.4.1+: the fill time is when the (timely) bar was processed, the trigger bar is kept.
    assert trade['exit_trigger_bar_minute_utc'] == (opening + pd.Timedelta(minutes=90)).isoformat()
    exit_at = pd.Timestamp(trade['exit_at_utc'])
    assert opening + pd.Timedelta(minutes=91) < exit_at <= opening + pd.Timedelta(minutes=91, seconds=15)
    assert trade['outcome_unreliable'] is False
    entry = pd.Timestamp(trade['entry_at_utc'])
    signal = opening + pd.Timedelta(minutes=26)
    assert signal + pd.Timedelta(seconds=5) <= entry < signal + pd.Timedelta(seconds=60)


def test_replay_day_is_complete_and_order_free(replayed):
    folder = day_folder(replayed)
    status = load(folder, 'status.json')
    assert status['status'] == 'SHADOW_SIM_COMPLETED_NO_ORDERS' and status['orders_sent'] == 0
    coverage = load(folder, 'coverage.json')
    assert coverage['missed_decision_minutes'] == 0 and coverage['data_error_minutes'] == 0
    manifest = load(folder, 'manifest.json')
    assert manifest['mode'] == 'REPLAY_VIRTUAL_PORTFOLIO' and manifest['replay']['fingerprint']
    assert set(manifest['code_sha256']) >= {'us_orb_test_v02.py', 'us_orb_scanner_v03.py',
                                            'shadow_portfolio_v14.py', 'sip_shadow_sim_v14.py'}
    assert not (folder / 'failed.json').exists()


def test_replay_rejects_breakout_without_volume(replayed):
    folder = day_folder(replayed)
    opening = pd.Timestamp(f'{DAY} 09:30', tz=NY).tz_convert('UTC')
    signal = load(folder, 'signals/' + sim.minute_key(opening + pd.Timedelta(minutes=41), 'CCC'))
    assert signal['status'] == 'VOLUME_REJECT' and signal['rvol'] == pytest.approx(1.0)


def test_summary(replayed):
    summary = load(replayed.summary, 'summary.json')
    assert summary['days_replayed'] == 1 and summary['trades'] == 1 and summary['days_failed'] == 0
    assert summary['total_pnl_usd'] == pytest.approx(29 * (101.5 * 0.99 - 100.21))
    trades = pd.read_csv(replayed.summary / 'trades.csv')
    assert list(trades.symbol) == ['AAA']


def test_completed_day_is_reused(replayed):
    calls = replayed.market.calls
    replay.run_replay(replayed.store, replayed.folder / 'drive', C, S, DAY, DAY)
    assert len(list((replayed.folder / 'drive' / 'shadow_replay_v1_4' / DAY).iterdir())) == 1
    assert replayed.market.calls == calls


def test_replay_runs_offline_from_cache(replayed):
    offline = replay.ReplayStore(replayed.store.folder)   # no source at all
    folder = replay.replay_day(offline, DAY, replayed.folder / 'offline', C, S,
                               replay.replay_settings_default())
    assert load(folder, 'shadow_trades.json') == load(day_folder(replayed), 'shadow_trades.json')


def test_missing_cache_without_source_fails_loudly(tmp_path):
    with pytest.raises(ValueError, match='Replay-Daten fehlen'):
        replay.ReplayStore(tmp_path).sessions(DAY, DAY)


def test_replay_data_never_returns_future_data(replayed):
    from alpaca.data.requests import StockBarsRequest, StockQuotesRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.common.enums import Sort
    opening = pd.Timestamp(f'{DAY} 09:30', tz=NY).tz_convert('UTC')
    now = opening + pd.Timedelta(minutes=30, seconds=1)
    clock = replay.ReplayClock(now)
    data = replay.ReplayData(replayed.store, clock)
    answer = data.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=['AAA'], timeframe=TimeFrame.Minute,
        start=opening.to_pydatetime(), end=(opening + pd.Timedelta(hours=6)).to_pydatetime()))
    # Minute 29 closes at 30:00 and is published 2 s later: not yet visible at 30:01.
    assert answer.df.timestamp.max() == opening + pd.Timedelta(minutes=28)
    asked_at = clock.now()   # the bar request already took 0.3 s
    quotes = data.get_stock_quotes(StockQuotesRequest(
        symbol_or_symbols='AAA', start=(now - pd.Timedelta(seconds=10)).to_pydatetime(),
        end=(now + pd.Timedelta(minutes=5)).to_pydatetime(), limit=1, sort=Sort.DESC))
    assert quotes.data['AAA'] and all(q.timestamp <= asked_at for q in quotes.data['AAA'])
    assert clock.now() == now + pd.Timedelta(seconds=0.6)   # two requests of 0.3 s


def test_late_bars_lose_the_signal_but_never_trade_on_it(tmp_path):
    """Bars published 70 s late: the crossing is only seen after its 60 s TTL."""
    market = SyntheticMarket()
    store = make_store(tmp_path, market)
    rs = replay.ReplaySettings(bar_delay_seconds=70)
    folder = replay.replay_day(store, DAY, tmp_path / 'drive', C, S,
                               replay.replay_settings_default(), rs)
    assert load(folder, 'shadow_trades.json') == []
    opening = pd.Timestamp(f'{DAY} 09:30', tz=NY).tz_convert('UTC')
    signal = load(folder, 'signals/' + sim.minute_key(opening + pd.Timedelta(minutes=26), 'AAA'))
    assert signal['status'] == 'HISTORICAL_ONLY_STARTUP_OR_RESTART'
