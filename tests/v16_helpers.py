"""Synthetic Alpaca markets for the V1.6 tests (no network)."""
from datetime import datetime
from types import SimpleNamespace

import pandas as pd

import hist_data_v16 as hist
from test_replay_v14 import SECTORS, SyntheticMarket

NY = 'America/New_York'
HOLIDAY = '2026-09-16'      # synthetic exchange holiday (a Wednesday)
EARLY_CLOSE = '2026-09-17'  # synthetic 13:00 close
WALL_NOW = pd.Timestamp('2026-09-29 12:00', tz='UTC')


class ApiError(Exception):
    """Stands in for alpaca.common.exceptions.APIError (only status_code is used)."""

    def __init__(self, status_code, message='error'):
        super().__init__(message)
        self.status_code = status_code


class CalendarMarket(SyntheticMarket):
    """SyntheticMarket with a holiday, an early close and request recording."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.requests = []
        self.empty_days = set()          # sessions for which no bar at all is delivered
        self.missing = {}                # (symbol, day) -> no bars for that symbol/day
        self.fail = {}                   # method -> exception to raise

    def get_calendar(self, request):
        rows = []
        for entry in super().get_calendar(request):
            if str(entry.date) == HOLIDAY:
                continue
            if str(entry.date) == EARLY_CLOSE:
                d = entry.date
                entry = SimpleNamespace(date=d, open=entry.open, close=datetime(d.year, d.month, d.day, 13, 0))
            rows.append(entry)
        return rows

    def get_stock_bars(self, request):
        self.requests.append(('bars', request))
        if 'bars' in self.fail:
            raise self.fail['bars']
        answer = super().get_stock_bars(request)
        frame = answer.df
        days = frame.timestamp.dt.tz_convert(NY).dt.strftime('%Y-%m-%d')
        keep = ~days.isin(self.empty_days)
        for (symbol, day) in self.missing:
            keep &= ~((frame.symbol == symbol) & (days == day))
        frame = frame[keep]
        return SimpleNamespace(data={s: [1] for s in frame.symbol.unique()}, df=frame)

    def get_stock_quotes(self, request):
        self.requests.append(('quotes', request))
        if 'quotes' in self.fail:
            raise self.fail['quotes']
        return super().get_stock_quotes(request)

    def get_stock_trades(self, request):
        self.requests.append(('trades', request))
        if 'trades' in self.fail:
            raise self.fail['trades']
        return super().get_stock_trades(request)


def universe_frame():
    return pd.DataFrame([dict(symbol=s, company=s, sector=sector, tradable_now=True, universe_result='PASS')
                         for s, sector in SECTORS.items()])


def make_store(folder, market, now=WALL_NOW, **kwargs):
    store = hist.HistoricalDataStore(folder / 'historical', market, market,
                                     retry=hist.RetryPolicy(spacing_seconds=0), now=lambda: now,
                                     sleep=lambda seconds: None, **kwargs)
    store.set_universe(universe_frame(), dict(source='synthetic_test'))
    return store


def count(market, kind):
    return sum(1 for k, _ in market.requests if k == kind)
